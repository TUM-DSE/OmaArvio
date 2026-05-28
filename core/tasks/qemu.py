#!/usr/bin/env python3

# Original version:
# (c) 2021-2022 Jörg Thalheim
# https://github.com/Mic92/vmsh/blob/358cd4b6ec7de0dcac05a12e32486ef30658018c/tests/qemu.py

import atexit
import os
import re
import socket
import subprocess
import psutil
import time
from contextlib import contextmanager
from functools import cache
from pathlib import Path
from shlex import quote
from tempfile import TemporaryDirectory
from typing import Any, Dict, Iterator, List, Text, Optional, Union

from core.tasks.procs import ChildFd, pprint_cmd, run, system_run, get_nix_env
from core.tasks.config import PROJECT_ROOT


from qemu.qmp.legacy import QEMUMonitorProtocol


class QmpSession:
    def __init__(self, path: Path) -> None:
        self.qmp = QEMUMonitorProtocol(str(path))
        self.qmp.connect(negotiate=True)

    def events(self) -> Iterator[Dict[str, Any]]:
        events = self.qmp.get_events(wait=False)
        for event in events:
            yield dict(event)
        if not events:
            event = self.qmp.pull_event(wait=True)
            if event is not None:
                yield dict(event)

    def send(self, cmd: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return dict(self.qmp.cmd_raw(cmd, args))


def is_port_open(ip: str, port: int, wait_response: bool = False) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((ip, int(port)))
        if wait_response:
            s.recv(1)
        s.shutdown(2)
        return True
    except Exception:
        return False


@contextmanager
def connect_qmp(path: Path) -> Iterator[QmpSession]:
    session = QmpSession(path)
    try:
        yield session
    finally:
        session.qmp.close()


def parse_regs(qemu_output: str) -> Dict[str, int]:
    regs = {}
    for match in re.finditer(r"(\S+)\s*=\s*([0-9a-f ]+)", qemu_output):
        name = match.group(1)
        content = match.group(2).replace(" ", "")
        regs[name.lower()] = int(content, 16)
    return regs


def get_ssh_port(session: QmpSession) -> int:
    usernet_info = session.send(
        "human-monitor-command", args={"command-line": "info usernet"}
    )
    ssh_port = None
    for line in usernet_info["return"].splitlines():
        fields = line.split()
        if "TCP[HOST_FORWARD]" in fields and "22" in fields:
            ssh_port = int(line.split()[3])
    assert ssh_port is not None
    return ssh_port


def _find_ssh_key() -> Path:
    """Locate the guest SSH private key (bundled at ``core/nix/ssh_key``)."""
    # core/tasks/qemu.py → core/nix/ssh_key
    key_path = Path(__file__).resolve().parent.parent / "nix" / "ssh_key"
    if not key_path.exists():
        raise FileNotFoundError(f"SSH key not found at {key_path}")
    return key_path


class GuestSshKey:
    def __init__(self) -> None:
        self._source_path = _find_ssh_key()
        self._tmpdir = TemporaryDirectory(prefix="cvm-ssh-key-")
        self._key_path = Path(self._tmpdir.name) / "ssh_key"
        key_dir = Path(self._tmpdir.name)
        key_dir.chmod(0o700)

        fd = os.open(
            self._key_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "wb") as key_file:
            key_file.write(self._source_path.read_bytes())

        self._key_path.chmod(0o400)

    @property
    def path(self) -> Path:
        return self._key_path

    def cleanup(self) -> None:
        self._tmpdir.cleanup()


@cache
def _guest_ssh_key() -> GuestSshKey:
    return GuestSshKey()


def _cleanup_guest_ssh_key() -> None:
    if _guest_ssh_key.cache_info().currsize:
        _guest_ssh_key().cleanup()
        _guest_ssh_key.cache_clear()


atexit.register(_cleanup_guest_ssh_key)


def ssh_cmd(port: int) -> List[str]:
    key_path = _guest_ssh_key().path
    return [
        "ssh",
        "-i",
        str(key_path),
        "-p",
        str(port),
        "-oBatchMode=yes",
        "-oStrictHostKeyChecking=no",
        "-oConnectTimeout=5",
        "-oIdentitiesOnly=yes",
        "-oUserKnownHostsFile=/dev/null",
        "root@localhost",
    ]


class QemuVm:
    def __init__(
        self,
        qmp_session: QmpSession,
        tmux_session: str,
        pid: int,
        config: Optional[dict] = None,
    ) -> None:
        self.qmp_session = qmp_session
        self.tmux_session = tmux_session
        self.pid = pid
        self.ssh_port = get_ssh_port(qmp_session)
        self.config = config or {}

    def events(self) -> Iterator[Dict[str, Any]]:
        return self.qmp_session.events()

    def wait_for_ssh(self) -> None:
        """
        Block until ssh port is accessible
        """
        print(f"wait for ssh on {self.ssh_port}")
        while True:
            if (
                self.ssh_cmd(
                    ["echo", "ok"],
                    check=False,
                    stderr=subprocess.DEVNULL,
                    verbose=False,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.1)

    def ssh_Popen(
        self,
        stdout: ChildFd = subprocess.PIPE,
        stderr: ChildFd = None,
        stdin: ChildFd = None,
    ) -> subprocess.Popen:
        """
        opens a background process with an interactive ssh session
        """
        cmd = ssh_cmd(self.ssh_port)
        pprint_cmd(cmd)
        return subprocess.Popen(cmd, stdin=stdin, stdout=stdout, stderr=stderr)

    def ssh_cmd(
        self,
        argv: List[str],
        extra_env: Optional[Dict[str, str]] = None,
        check: bool = True,
        stdin: ChildFd = None,
        stdout: ChildFd = subprocess.PIPE,
        stderr: ChildFd = None,
        verbose: bool = True,
        input: Optional[str] = None,
        bypass: bool = False,  # Does nothing here, just for compatibility with HostRunner
        cwd: Optional[str] = None,
    ) -> "subprocess.CompletedProcess[Text]":
        """
        @return: CompletedProcess.stderr/stdout contains output of `cmd` which
        is run in the vm via ssh.
        """
        if extra_env is None:
            extra_env = {}
        env_cmd = []
        if len(extra_env):
            env_cmd.append("env")
            # "-" option makes phoronix-test-suite to complain about mktemp and sh not found
            # TODO: check if this is correct way to handle this
            # env_cmd.append("-")
            for k, v in extra_env.items():
                env_cmd.append(f"{k}={v}")
        remote_cmd = " ".join(map(quote, argv))
        if cwd:
            remote_cmd = f"cd {quote(cwd)} && {remote_cmd}"
        cmd = ssh_cmd(self.ssh_port) + ["--"] + env_cmd + [remote_cmd]
        return run(
            cmd,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            check=check,
            verbose=verbose,
            input=input,
        )

    def regs(self) -> Dict[str, int]:
        """
        Get cpu register:
        TODO: add support for multiple cpus
        """
        res = self.send(
            "human-monitor-command", args={"command-line": "info registers"}
        )
        return parse_regs(res["return"])

    def dump_physical_memory(self, addr: int, num_bytes: int) -> bytes:
        res = self.send(
            "human-monitor-command",
            args={"command-line": f"xp/{num_bytes}bx 0x{addr:x}"},
        )
        hexval = "".join(
            m.group(1) for m in re.finditer("0x([0-9a-f]{2})", res["return"])
        )
        return bytes.fromhex(hexval)

    def attach(self) -> None:
        """
        Attach to qemu session via tmux. This is useful for debugging
        """
        subprocess.run(["tmux", "-L", self.tmux_session, "attach"])

    def send(self, cmd: str, args: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """
        Send a Qmp command (https://wiki.qemu.org/Documentation/QMP)
        """
        return self.qmp_session.send(cmd, args or {})

    def pin_vcpu(self, pcpu_base: int = 0) -> None:
        """Pin vCPUs to physical CPUs"""
        cpu_info = self.send("query-cpus-fast")["return"]
        num_cpus = len(cpu_info)
        for cpu in cpu_info:
            tid = cpu["thread-id"]
            cpuidx = cpu["cpu-index"]
            try:
                cmd = ["taskset", "-pc", str(cpuidx + pcpu_base), str(tid)]
                run(cmd)
            except subprocess.CalledProcessError as e:
                print("Failed to pin vCPU{}: {}".format(cpuidx, e))
                return

        iothreads_info = self.send("query-iothreads")["return"]
        num_iothreads = len(iothreads_info)
        if num_iothreads == 0:
            print("No iothreads found")
            return
        print("Pin iothreads")
        for i, cpu in enumerate(iothreads_info):
            tid = cpu["thread-id"]
            try:
                cmd = ["taskset", "-pc", str(pcpu_base + num_cpus + i), str(tid)]
                run(cmd)
            except subprocess.CalledProcessError as e:
                # FIXME: this can happen if pcpu_base + num_cpus + i is bigger than the number of available CPUs
                print("Failed to pin vCPU{}: {}".format(cpuidx, e))
                return

    def shutdown(self, timeout=10) -> None:
        """Try graceful shutdown"""
        print("shutdown vm")
        try:
            self.ssh_cmd(["poweroff"])
        except subprocess.CalledProcessError:
            print("ssh failed, the server might be already down")
            return
        count = 0
        if count < timeout and psutil.pid_exists(self.pid):
            time.sleep(1)
            print(".")
            count += 1

    def create_file(self, path: str, content: str, mode: str = "w") -> None:
        """Create or append to a file on the VM via stdin using tee.

        Uses tee with stdin piping to avoid shell escaping issues. Supports
        both write and append modes.

        Args:
            path: Path to the file on the VM
            content: File content as string
            mode: 'w' for write (default) or 'a' for append
        """
        cmd = ["tee", path] if mode == "w" else ["tee", "-a", path]
        self.ssh_cmd(cmd, input=content, check=True)


class HostRunner:
    """Execute commands on the host using the benchmarking dev environment.

    This class mimics the QemuVm interface but runs commands directly on the host
    using 'nix develop .#benchmarking -c' to ensure all SPDK/FIO tools are available.

    Commands are run with systemd-run to enforce CPU and memory limits based on
    the VM size configuration, and /share is bind-mounted to PROJECT_ROOT.
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        if config is None:
            config = {}
        self.config = config
        self.ssh_port = None  # Not applicable for host
        self.pid = os.getpid()  # Just use our own PID

        # Extract resource configuration for systemd limits
        # Resource contains: cpu (count), memory (GB), pin_base, numa_node
        self.resource = config.get("resource")
        if self.resource:
            self.cpus = self.resource.cpu
            self.memory_gb = self.resource.memory
        else:
            # Fallback to no limits if resource not configured
            self.cpus = None
            self.memory_gb = None

        # Get and cache Nix environment once (expensive operation: ~1-2 seconds)
        # This avoids calling 'nix print-dev-env' on every command
        self.nix_env = get_nix_env()

    def events(self) -> Iterator[Dict[str, Any]]:
        """No events for host runner"""
        return iter([])

    def wait_for_ssh(self) -> None:
        """No-op for host runner - always ready"""
        print("Host runner ready (no SSH wait needed)")

    def ssh_Popen(
        self,
        stdout: ChildFd = subprocess.PIPE,
        stderr: ChildFd = None,
        stdin: ChildFd = None,
    ) -> subprocess.Popen:
        """Not implemented for host runner"""
        raise NotImplementedError("ssh_Popen not supported for host runner")

    def ssh_cmd(
        self,
        argv: List[str],
        extra_env: Optional[Dict[str, str]] = None,
        check: bool = True,
        stdin: ChildFd = None,
        stdout: ChildFd = subprocess.PIPE,
        stderr: ChildFd = None,
        verbose: bool = False,
        input: Optional[str] = None,
        bypass: bool = False,
        cwd: Optional[str] = None,
    ) -> "subprocess.CompletedProcess[Text]":
        """
        Run a command on the host in the benchmarking environment with resource limits.

        This mimics the ssh_cmd interface but runs directly on the host using systemd-run
        to enforce CPU and memory limits based on the VM size configuration.
        The /share directory is bind-mounted to PROJECT_ROOT (isolated to this process).

        @return: CompletedProcess.stderr/stdout contains output of `cmd`
        """
        if extra_env is None:
            extra_env = {}

        # Translate /share paths to host paths
        if cwd and cwd.startswith("/share/"):
            host_cwd = PROJECT_ROOT / cwd[7:]  # Remove "/share/" prefix
        elif cwd:
            host_cwd = Path(cwd)
        else:
            host_cwd = PROJECT_ROOT

        if bypass:
            # Run command directly without systemd-run
            env = self.nix_env.copy()
            env.update(extra_env)
            return run(
                argv,
                extra_env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                check=check,
                verbose=verbose,
                input=input,
                cwd=host_cwd,
            )

        # Run the command with systemd-run for resource limits
        # Uses cached Nix environment (loaded once in __init__)
        # /share bind mount is created automatically by system_run
        return system_run(
            argv,
            nix_env=self.nix_env,
            extra_env=extra_env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            check=check,
            verbose=verbose,
            input=input,
            cwd=host_cwd,
            cpus=self.cpus,
            memory_gigabytes=self.memory_gb,
        )

    def regs(self) -> Dict[str, int]:
        """Not applicable for host runner"""
        raise NotImplementedError("regs() not supported for host runner")

    def dump_physical_memory(self, addr: int, num_bytes: int) -> bytes:
        """Not applicable for host runner"""
        raise NotImplementedError(
            "dump_physical_memory() not supported for host runner"
        )

    def attach(self) -> None:
        """No-op for host runner - just print message"""
        print("Host runner: commands executed directly on host")
        print("Press Ctrl-C to exit")
        try:
            # Just wait indefinitely until user interrupts
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nExiting host runner")

    def send(self, cmd: str, args: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Not applicable for host runner"""
        raise NotImplementedError("send() not supported for host runner")

    def pin_vcpu(self, pcpu_base: int = 0) -> None:
        """No-op for host runner"""
        print("Host runner: vCPU pinning not applicable")

    def shutdown(self, timeout=10) -> None:
        """No-op for host runner"""
        print("Host runner: shutdown (no-op)")

    def create_file(self, path: str, content: str, mode: str = "w") -> None:
        """Create or append to a file on the host via stdin using tee.

        Uses tee with stdin piping to avoid shell escaping issues. Supports
        both write and append modes.

        Args:
            path: Path to the file on the host
            content: File content as string
            mode: 'w' for write (default) or 'a' for append
        """
        cmd = ["tee", path] if mode == "w" else ["tee", "-a", path]
        self.ssh_cmd(cmd, input=content, check=True)


@contextmanager
def spawn_host_runner(config: Optional[dict] = None) -> Iterator[HostRunner]:
    """Spawn a host runner for executing commands on the host.

    Args:
        config: Configuration dictionary (for compatibility with spawn_qemu)

    Yields:
        HostRunner instance

    Example:
        with spawn_host_runner(config) as runner:
            runner.ssh_cmd(["spdk-fio", "--help"])
    """
    runner = HostRunner(config)
    try:
        yield runner
    finally:
        # Nothing to clean up for host runner
        pass


@contextmanager
def spawn_runner(
    qemu_command: Optional[List[str]] = None,
    *,
    config: Optional[dict] = None,
    pin: bool = False,
    shutdown: bool = True,
) -> Iterator[Union[QemuVm, HostRunner]]:
    """Spawn a host or VM runner and manage common runner lifecycle.

    When requested, vCPU pinning is applied before yielding the runner. By
    default the runner is gracefully shut down when the context exits.
    """
    if config is None:
        config = {}

    pin_base = None
    if pin:
        pin_resource = config.get("resource")
        if pin_resource is None:
            raise ValueError("config['resource'] is required when pin=True")
        pin_base = config.get("pin_base", pin_resource.pin_base)

    if config.get("type") == "host":
        runner_context = spawn_host_runner(config=config)
    else:
        if qemu_command is None:
            raise ValueError("qemu_command is required for VM runner")

        resource = config["resource"]
        runner_context = spawn_qemu(
            qemu_command,
            numa_node=resource.numa_node,
            config=config,
        )

    with runner_context as runner:
        if pin:
            runner.pin_vcpu(pin_base)
        try:
            yield runner
        finally:
            if shutdown:
                runner.shutdown()


@contextmanager
def spawn_qemu(
    qemu_command: List[str],
    extra_args: Optional[List[str]] = None,
    extra_args_pre: Optional[List[str]] = None,
    numa_node: Optional[List[int]] = None,
    config: Optional[dict] = None,
) -> Iterator[QemuVm]:
    if extra_args is None:
        extra_args = []
    if extra_args_pre is None:
        extra_args_pre = []
    if config is None:
        config = {}
    with TemporaryDirectory() as tempdir:
        qmp_socket = Path(tempdir).joinpath("qmp.sock")
        cmd = extra_args_pre.copy()

        if numa_node is not None:
            cmd += [
                "numactl",
                f"--cpunodebind={','.join(map(str, numa_node))}",
                f"--membind={','.join(map(str, numa_node))}",
            ]

        qmp_command = [
            "-qmp",
            f"unix:{str(qmp_socket)},server,nowait",
        ]
        cmd += qemu_command
        cmd += qmp_command
        cmd += extra_args

        print(cmd)

        # run qemu in a tmux session so that we can kill qemu threads easily
        tmux_session = f"pytest-{os.getpid()}"
        tmux = [
            "tmux",
            "-L",
            tmux_session,
            "new-session",
            "-d",
            " ".join(map(quote, cmd)),
        ]
        print("$ " + " ".join(map(quote, tmux)))
        subprocess.run(tmux, check=True)
        qemu_pid = None
        try:
            proc = subprocess.run(
                [
                    "tmux",
                    "-L",
                    tmux_session,
                    "list-panes",
                    "-a",
                    "-F",
                    "#{pane_pid}",
                ],
                stdout=subprocess.PIPE,
                check=True,
            )
            qemu_pid = int(proc.stdout)
            print(f"qemu pid: {qemu_pid}")
            while not qmp_socket.exists():
                try:
                    os.kill(qemu_pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    raise Exception("qemu vm was terminated")
            with connect_qmp(qmp_socket) as session:
                yield QemuVm(session, tmux_session, qemu_pid, config)
        finally:
            subprocess.run(["tmux", "-L", tmux_session, "kill-server"])
            while True:
                try:
                    if not qemu_pid:
                        break
                    os.kill(qemu_pid, 0)
                except ProcessLookupError:
                    break
                else:
                    print("waiting for qemu to stop")
                    time.sleep(1)
            print("qemu stopped")


def setup_hugepages(
    vm: Union[QemuVm, HostRunner], total_size_gb: int = 4, page_size_gb: int = 1
) -> None:
    """Setup hugepages inside the VM or on the host.

    Args:
        vm: QemuVm or HostRunner instance
        total_size_gb: Total size of hugepages in GB (default: 4G)
        page_size_gb: Size of each hugepage in GB (default: 1G)
    """
    cmd = [
        "dpdk-hugepages.py",
        "--setup",
        f"{total_size_gb}G",
        "--pagesize",
        f"{page_size_gb}G",
    ]
    print(f"Setting up {total_size_gb}G hugepages")
    vm.ssh_cmd(cmd, check=True, bypass=True)
