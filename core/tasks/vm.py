#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, List, Iterator, Tuple, Union
from pathlib import Path
import shlex
import os
import socket
import subprocess
import time
import tempfile

from invoke import task

from core.tasks.config import BUILD_DIR, PROJECT_ROOT, LINUX_DIR, SSH_PORT, load_config
from core.tasks.qemu import spawn_qemu, spawn_host_runner, QemuVm, HostRunner
from core.tasks.actions import ACTIONS
from core.tasks.utils.monitoring import monitor_with_perf_kvm
from core.tasks.utils.vfio import (
    bind_device_to_vfio,
    unbind_device_from_vfio,
    get_pci_ids,
)
from core.tasks.utils.utils import get_benchmark_output_path


@dataclass
class NodeInfo:
    cpus: str
    mem: int
    dist: [int]


@dataclass
class VMResource:
    cpu: int
    memory: int  # GB
    pin_base: int
    numa_node: [int] = None
    vnuma: Optional[NodeInfo] = None


@dataclass
class VMConfig:
    qemu: Path
    image: Path
    ovmf: Path
    kernel: Optional[Path]
    initrd: Optional[Path]
    cmdline: Optional[str]


def ensure_snp_certificates(hostname: str) -> Path:
    """Ensure SEV-SNP certificates are fetched and bundled for the host.

    Fetches certificates from AMD KDS and creates a kernel-format bundle
    suitable for QEMU's sev-snp-guest certs-filename parameter.

    Certificates are cached per-hostname in BUILD_DIR/sev-snp/certs/<hostname>/
    and only fetched once. To refetch, delete the cache directory.

    Args:
        hostname: Hostname of the machine (for cache directory)

    Returns:
        Path to the certificate bundle file

    Raises:
        subprocess.CalledProcessError: If certificate fetching fails
        PermissionError: If /dev/sev is not accessible
    """
    # Define cache paths
    certs_base = BUILD_DIR / "sev-snp" / "certs" / hostname
    cert_bundle = certs_base / "cert_bundle"

    # Return early if bundle exists
    if cert_bundle.exists():
        print(f"SEV-SNP certificates cached at: {cert_bundle}")
        return cert_bundle

    print(f"Fetching SEV-SNP certificates for {hostname}...")

    # Create cache directory
    certs_base.mkdir(parents=True, exist_ok=True)

    # Fetch CA certificates (ARK, ASK)
    print("  Fetching CA certificates from AMD KDS...")
    subprocess.run(
        ["snphost", "fetch", "ca", "pem", str(certs_base)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Fetch VCEK certificate (hardware-specific)
    print("  Fetching VCEK certificate...")
    subprocess.run(
        ["snphost", "fetch", "vek", "pem", str(certs_base)],
        check=True,
        capture_output=True,
        text=True,
    )

    # Create kernel-format bundle
    print("  Creating certificate bundle...")
    subprocess.run(
        ["snphost", "import", str(certs_base), str(cert_bundle)],
        check=True,
        capture_output=True,
        text=True,
    )

    print(f"Certificate bundle created: {cert_bundle}")
    return cert_bundle


def _vmresource_from_config(rc) -> VMResource:
    """Convert a VMResourceConfig (from config.toml) to a VMResource."""
    return VMResource(
        cpu=rc.cpu, memory=rc.memory, numa_node=rc.numa_node, pin_base=rc.pin_base
    )


def get_vm_resource(hostname: str, name: str) -> VMResource:
    cfg = load_config()

    # Try host-specific resources first
    host = cfg.hosts.get(hostname)
    if host and name in host.vm_resources:
        return _vmresource_from_config(host.vm_resources[name])

    # Fall back to [defaults.vm_resources]
    if name in cfg.default_vm_resources:
        if host is None:
            print(
                f"Warning: No VM resource config found for hostname '{hostname}', falling back to defaults"
            )
        return _vmresource_from_config(cfg.default_vm_resources[name])

    available = list(cfg.default_vm_resources.keys())
    raise ValueError(f"Unknown VM size: {name!r}. Available sizes: {available}")


@contextmanager
def monitor_with_sar(
    vm: Union[QemuVm, HostRunner],
    output_dir: Path,
    timestamp: str,
    config: dict,
) -> Iterator[None]:
    """Context manager to run sar on host and guest during benchmarks.

    Args:
        vm: QemuVm instance (host and guest monitoring) or HostRunner (host only)
        output_dir: Directory to save sar output files
        timestamp: Consistent timestamp for file naming
        config: Configuration dict with sar_enabled, sar_interval, sar_options

    Yields:
        None - monitoring runs in background during context

    Example:
        with monitor_with_sar(vm, output_dir, timestamp, config):
            run_fio(...)  # sar collects data during this call
    """
    sar_enabled = config.get("sar_enabled", True)
    if not sar_enabled:
        yield  # No monitoring, just pass through
        return

    sar_interval = config.get("sar_interval", 1)
    sar_options = config.get("sar_options", "-u -r -n DEV")

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    host_sar_proc = None

    try:
        # Start host sar
        host_sar_file = output_dir / f"{timestamp}_host_sar.txt"
        print(f"Starting host sar monitoring -> {host_sar_file}")
        host_sar_proc = subprocess.Popen(
            ["sar"] + sar_options.split() + [str(sar_interval)],
            stdout=open(host_sar_file, "w"),
            stderr=subprocess.DEVNULL,
        )

        # Start guest sar if VM is provided (but not for HostRunner)
        # HostRunner executes on the host, so we don't want duplicate SAR monitoring
        if vm and not isinstance(vm, HostRunner):
            # Use relative path from /share mount
            guest_sar_relpath = output_dir.relative_to(PROJECT_ROOT)
            guest_sar_path = f"/share/{guest_sar_relpath}/{timestamp}_guest_sar.txt"
            print(f"Starting guest sar monitoring -> {guest_sar_path}")

            vm.ssh_cmd(
                [
                    "sh",
                    "-c",
                    f"nohup sar {sar_options} {sar_interval} > {guest_sar_path} 2>&1 &",
                ],
                check=False,
                verbose=True,
            )

        # Give sar processes time to initialize
        time.sleep(2)

        # Yield control back - benchmark runs here
        yield

    finally:
        # Cleanup: stop all sar processes
        print("Stopping sar monitoring...")

        if host_sar_proc:
            host_sar_proc.terminate()
            try:
                host_sar_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                host_sar_proc.kill()
                host_sar_proc.wait()

        if vm and not isinstance(vm, HostRunner):
            # Kill sar on guest (only for real VMs, not host runner)
            vm.ssh_cmd(["pkill", "-TERM", "sar"], check=False, verbose=False)
            time.sleep(1)  # Give time to flush buffers

        print("SAR monitoring stopped and data saved")


def get_vm_config(name: str, attestation: bool = False) -> VMConfig:
    if name.startswith(("snp", "amd")):
        # Default to AMD SNP specific QEMU and OVMF
        image = BUILD_DIR / "image/snp-guest-image.raw"
        # Just always use upstream QEMU and OVMF
        ovmf = BUILD_DIR / "ovmf-upstream-fd/FV/OVMF.fd"

        # Select QEMU binary based on attestation flag
        if attestation and "snp" in name:
            qemu = BUILD_DIR / "qemu-amd/bin/qemu-system-x86_64"
        else:
            qemu = BUILD_DIR / "qemu-upstream/bin/qemu-system-x86_64"

        kernel = None
        initrd = None
        cmdline = None

        if "direct" in name:
            # Use direct-guest image for direct kernel boot (smaller, no bootloader)
            image = BUILD_DIR / "image/direct-guest-image.raw"
            kernel = LINUX_DIR / "build/arch/x86/boot/bzImage"
            initrd = None
            cmdline = "root=/dev/vda1 console=hvc0 iommu=off intel_iommu=off amd_iommu=off hugepagesz=2M hugepages=4096 loglevel=4 lsm=landlock,yama,bpf vfio.enable_unsafe_noiommu_mode=1"

        return VMConfig(qemu, image, ovmf, kernel, initrd, cmdline)

    raise ValueError(f"Unknown VM image: {name}")


def get_amd_qemu_cmd_general(
    resource: VMResource,
    config: dict,
    vmconfig_name: str,
    direct: bool,
    confidential: bool,
    edu: bool,
    vfio_trace: bool,
    vfio_trace_file: Optional[Path] = None,
) -> List[str]:
    attestation = config.get("attestation", False)
    vmconfig: VMConfig = get_vm_config(vmconfig_name, attestation=attestation)
    ssh_port = config["ssh_port"]
    vfio_devices = config.get("vfio_devices", [])

    # Fetch SNP certificates if attestation is enabled
    cert_bundle_path = None
    if attestation:
        hostname = config.get("hostname", socket.gethostname())
        cert_bundle_path = ensure_snp_certificates(hostname)

    # Only generate default path if vfio_trace is enabled
    if vfio_trace:
        if vfio_trace_file is None:
            vfio_trace_file = Path(
                f"./vfio_trace_{vmconfig_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.log"
            )

        # Validate configuration
        if not vfio_devices:
            raise ValueError(
                "VFIO tracing requires at least one VFIO device (--vfio-pcie)"
            )
    if config["boot_prealloc"]:
        prealloc = "on"
    else:
        prealloc = "off"

    kernel_config = ""
    bootindex_config = ",bootindex=0"  # Default: boot from disk
    if direct:
        extra_cmdline = config.get("extra_cmdline", "")
        kernel_config = f"""
        -kernel {vmconfig.kernel}
        -append '{vmconfig.cmdline}{extra_cmdline}'
        """
        bootindex_config = ""  # Don't set bootindex when using direct kernel boot
    # VFIO PCIe passthrough configuration
    vfio_config = ""
    if vfio_devices:
        # Modern VFIO with iommufd, PCIe root port, and explicit device IDs
        vfio_config = """
        -object iommufd,id=iommufd0
        """
        if vfio_trace:
            vfio_config += f"""
            -trace enable=vfio_region_write,enable=vfio_region_read,file={vfio_trace_file}
            """
        tracing = ",x-no-mmap=true" if vfio_trace else ""
        for idx, device in enumerate(vfio_devices):
            vendor_id, device_id = get_pci_ids(device)
            vfio_config += f"""
        -device pcie-root-port,id=rp{idx},bus=pcie.0,chassis=0,slot={idx},multifunction=off,pref64-reserve=274877906944B,mem-reserve=4194304B
        -device vfio-pci,host=0000:{device},x-pci-vendor-id={vendor_id},x-pci-device-id={device_id},bus=rp{idx},iommufd=iommufd0{tracing}
        """

    machine_config = f"""
        -machine q35,memory-backend=ram1,vmport=off,kernel_irqchip=split
        -object memory-backend-memfd,id=ram1,size={resource.memory}G,share=true,prealloc={prealloc}
        """
    if confidential:
        # Sanity check if we allowed to start a SEV machine
        if not os.access("/dev/sev", os.R_OK):
            raise PermissionError("Cannot access /dev/sev. Likely need to run as root!")

        # Build sev-snp-guest object with optional certs-filename
        sev_guest_config = "id=sev0,cbitpos=51,reduced-phys-bits=1,policy=0x30000"
        if attestation and cert_bundle_path:
            sev_guest_config += f",certs-filename={cert_bundle_path}"

        machine_config = f"""
        -machine q35,memory-backend=ram1,memory-encryption=sev0,vmport=off,kernel_irqchip=split
        -object sev-snp-guest,{sev_guest_config}
        -object memory-backend-memfd,id=ram1,size={resource.memory}G,share=true,prealloc={prealloc}
        """

    module_shared_data = os.environ.get("MODULE_SHARED_DATA", "")
    shared_virtfs = (
        f"-virtfs local,path={module_shared_data},security_model=none,mount_tag=shared"
        if module_shared_data
        else ""
    )

    qemu_cmd = f"""
    {vmconfig.qemu}
    -enable-kvm
    -cpu EPYC-Genoa-v1,host-phys-bits=true
    -smp {resource.cpu}
    -m {resource.memory}G

    {machine_config}
    {kernel_config}

    -blockdev raw,node-name=q2,file.driver=file,file.filename={vmconfig.image}
    -device virtio-blk-pci,drive=q2{bootindex_config}
    -device virtio-net-pci,netdev=net0
    -netdev user,id=net0,hostfwd=tcp::{ssh_port}-:22,hostfwd=tcp::8895-:8895
    -virtfs local,path={PROJECT_ROOT},security_model=none,mount_tag=share
    {shared_virtfs}
    -bios {vmconfig.ovmf}
    {vfio_config}

    -nographic
    -serial null
    -device virtio-serial
    -chardev stdio,mux=on,id=char0,signal=off
    -mon chardev=char0,mode=readline
    -device virtconsole,chardev=char0,id=vc0,nr=0
    """

    # Use edu device with 64bit mask, as this is what we also normally expect for PCI devices
    if edu:
        qemu_cmd += " -device edu,dma_mask=0xffffffffffffffff"

    return shlex.split(qemu_cmd)


def qemu_option_virtio_blk(
    file: Path,  # file or block device to be used as a backend of virtio-blk
    aio: str = "native",  # either of threads, native (POSIX AIO), io_uring
    direct: bool = True,  # if True, QEMU uses O_DIRECT to open the file
    iothread: bool = True,  # if True, use QEMU iothread
    iommu_option: bool = False,  # if True, enable VIRTIO_F_ACCESS_PLATFORM (VIRTIO_F_IOMMU_PLATFORM) feature bit
    # (this is necessary to force bounce buffers in a normal VM for testing)
) -> List[str]:
    # QEMU options (https://www.qemu.org/docs/master/system/qemu-manpage.html)
    # -drive cache=
    #
    # |              | cache.writeback | cache.direct | cache.no-flush |
    # |--------------|-----------------|--------------|----------------|
    # | writeback    | on              | off          | off            |
    # | none         | on              | on           | off            |
    # | writethrough | off             | off          | off            |
    # | directsync   | off             | on           | off            |
    # | unsafe       | on              | off          | on             |
    #
    # NOTE:
    # - cache.writeback=on by default
    # - aio=native requires cache.direct=on (open file with O_DIRECT)
    # - by default, we use the same configuration as the "cache=none"
    #
    # - aio=threads vs native: https://bugzilla.redhat.com/show_bug.cgi?id=1545721
    # > With aio=native, IO submissions on the host by Qemu are limited to 1
    # > cpu, where as io=threads is multi-cpu.  io=native provides higher
    # > efficiency (less cpu overhead), but cannot scale to the levels io=threads
    # > does.  However, io=threads can consume more cpu as similar IO levels.  If
    # > there is ample CPU on the host, then io=threads will scale better.

    if file.is_block_device():
        driver = "host_device"
    else:
        driver = "file"

    if direct:
        cache_direct = "on"
    else:
        cache_direct = "off"

    if iommu_option:
        iommu = ",iommu_platform=on,disable-modern=off,disable-legacy=on"
    else:
        iommu = ""

    if iothread:
        option = f"""
            -blockdev node-name=q1,driver=raw,file.driver={driver},file.filename={file},file.aio={aio},cache.direct={cache_direct},cache.no-flush=off
            -device virtio-blk-pci,drive=q1,iothread=iothread0{iommu}
            -object iothread,id=iothread0
        """
    else:
        option = f"""
            -blockdev node-name=q1,driver=raw,file.driver={driver},file.filename={file},file.aio={aio},cache.direct={cache_direct},cache.no-flush=off
            -device virtio-blk-pci,drive=q1{iommu}
        """

    return shlex.split(option)


def parse_size_to_bytes(size: str) -> int:
    """Parse a human-readable size string to bytes (e.g., "4G" -> 4294967296)."""
    s = size.upper().strip()
    if s.endswith("G"):
        return int(float(s[:-1]) * 1024 * 1024 * 1024)
    elif s.endswith("M"):
        return int(float(s[:-1]) * 1024 * 1024)
    elif s.endswith("K"):
        return int(float(s[:-1]) * 1024)
    else:
        return int(s)


def qemu_option_nvme(
    size: str = "500G",  # size of NVMe namespace (e.g., "4G", "8G")
    bps_rd: str = "10G",  # read bandwidth limit (e.g., "10G", "500M"), 0 = unlimited
    bps_wr: str = "2.5G",  # write bandwidth limit (e.g., "2.5G", "500M"), 0 = unlimited
) -> Tuple[List[str], str]:
    """Create an emulated NVMe device in QEMU with temporary file storage.

    Args:
        size: Size of the NVMe namespace (e.g., "4G")
        bps_rd: Read bandwidth limit in bytes/sec (human-readable, e.g., "10G")
        bps_wr: Write bandwidth limit in bytes/sec (human-readable, e.g., "2.5G")

    Returns:
        Tuple of (QEMU command line options, path to backing file for cleanup)
    """

    # Create a named temporary file for the NVMe backing store
    # Note: delete=False keeps the file after the handle closes
    tmpfile = tempfile.NamedTemporaryFile(
        mode="wb", prefix="nvme_", suffix=".img", delete=False
    )
    tmpfile_path = tmpfile.name
    tmpfile.close()

    total_bytes = parse_size_to_bytes(size)

    # Create file
    subprocess.run(["truncate", "-s", str(total_bytes), tmpfile_path], check=True)

    bps_rd_bytes = parse_size_to_bytes(bps_rd)
    bps_wr_bytes = parse_size_to_bytes(bps_wr)

    drive_opts = f"file={tmpfile_path},if=none,id=nvme0n1,format=raw,cache=writethrough"
    if bps_rd_bytes > 0:
        drive_opts += f",throttling.bps-read={bps_rd_bytes}"
    if bps_wr_bytes > 0:
        drive_opts += f",throttling.bps-write={bps_wr_bytes}"

    option = f"""
        -drive {drive_opts}
        -device nvme,id=nvme0,serial=deadbeef
        -device nvme-ns,drive=nvme0n1,bus=nvme0,nsid=1
    """

    return shlex.split(option), tmpfile_path


def qemu_option_virtio_nic(
    tap="tap0", mtap="mtap0", vhost=False, mq=False, config={}
) -> List[str]:
    """Qreate a virtio-nic with a tap interface.
    If mq is True, then create multiple queues as many as the number of CPUs.

    See justfile for the bridge configuration.
    """

    resource: VMResource = config["resource"]
    iommu_option = config.get("virtio_iommu", False)
    num_cpus = resource.cpu
    if vhost:
        vhost_option = "on"
    else:
        vhost_option = "off"
    if iommu_option:
        iommu = ",iommu_platform=on,disable-modern=off,disable-legacy=on"
    else:
        iommu = ""

    if mq:
        option = f"""
        -netdev tap,id=en0,ifname={mtap},script=no,downscript=no,vhost={vhost_option},queues={num_cpus}
        -device virtio-net-pci,netdev=en0,mq=on,vectors=18{iommu}
        """
    else:
        option = f"""
        -netdev tap,id=en0,ifname={tap},script=no,downscript=no,vhost={vhost_option}
        -device virtio-net-pci,netdev=en0,mq=off,vectors=18{iommu}
        """
        # option = f"""
        #     -netdev bridge,id=en0,br={bridge}
        #     -device virtio-net-pci,netdev=en0
        # """

    return shlex.split(option)


def start_and_attach(qemu_cmd: List[str], pin: bool, **kargs: Any) -> None:
    """Start a VM and attach to the console (tmux session) to interact with the VM.
    Note 1: The VM automatically terminates when the tmux session is closed.
    Note 2: Ctrl-C goes to the tmux session, not the VM, killing the entier session with the VM.
    """
    resource: VMResource = kargs["config"]["resource"]
    pin_base: int = kargs["config"].get("pin_base", resource.pin_base)
    vm: QemuVM
    with spawn_qemu(qemu_cmd, numa_node=resource.numa_node) as vm:
        if pin:
            vm.pin_vcpu(pin_base)
        vm.attach()
        vm.shutdown()


def ssh_cmd(qemu_cmd: List[str], pin: bool, **kargs: Any) -> None:
    """Start a VM and then send cmd via ssh
    Example:

    # we can have multiple ssh commands
    inv vm.start --type intel --ssh-cmd "echo hi" --ssh-cmd "ls /" --action ssh-cmd
    """
    resource: VMResource = kargs["config"]["resource"]
    pin_base: int = kargs["config"].get("pin_base", resource.pin_base)
    cmds: [str] = kargs["config"]["ssh_cmd"]
    vm: QemuVM
    with spawn_qemu(
        qemu_cmd, numa_node=resource.numa_node, config=kargs["config"]
    ) as vm:
        if pin:
            vm.pin_vcpu(pin_base)
        vm.wait_for_ssh()

        for cmd in cmds:
            cmd_ = shlex.split(cmd)
            vm.ssh_cmd(cmd_)

        vm.shutdown()


def start_and_attach_host(pin: bool, **kargs: Any) -> None:
    """Start host runner and attach (wait for Ctrl-C)"""
    with spawn_host_runner(config=kargs["config"]) as runner:
        runner.attach()


def ssh_cmd_host(pin: bool, **kargs: Any) -> None:
    """Start host runner and execute commands"""
    cmds: [str] = kargs["config"]["ssh_cmd"]
    with spawn_host_runner(config=kargs["config"]) as runner:
        runner.wait_for_ssh()
        for cmd in cmds:
            cmd_ = shlex.split(cmd)
            runner.ssh_cmd(cmd_)
        runner.shutdown()


def run_benchmark_action(action_type: str, **kargs: Any) -> None:
    """Run FIO benchmark on VM or host - unified handler.

    Args:
        action_type: "fio"
        action_config: Action-specific parameters as dict
        **kargs: Standard kwargs including qemu_cmd, pin, name, config
    """
    config = kargs["config"]
    action_config = config.get("action_config") or {}
    is_host = config.get("type") == "host"
    name = kargs["name"]
    resource = config["resource"]
    pin_base = config.get("pin_base", resource.pin_base)
    qemu_cmd = kargs.get("qemu_cmd")
    pin = kargs.get("pin", True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    # Look up action from registry
    if action_type not in ACTIONS:
        raise ValueError(
            f"Unknown benchmark action type: {action_type}. Available: {list(ACTIONS.keys())}"
        )

    action = ACTIONS[action_type]
    path_components = action.path_fn(name, action_config)
    action_fn = action.fn

    outputdir_host, outputdir_guest, _ = get_benchmark_output_path(
        *path_components, timestamp=timestamp, create_dirs=True
    )

    # Configure VFIO trace file path for benchmark output
    if config.get("vfio_trace", False):
        if config.get("vfio_trace_file") is None:
            # Use the filename prepared by start(), place in output directory
            trace_filename = config.get(
                "vfio_trace_filename", f"{timestamp}_vfio_trace.log"
            )
            vfio_trace_file = outputdir_host / trace_filename
            config["vfio_trace_file"] = str(vfio_trace_file)
            print(f"VFIO tracing enabled -> {vfio_trace_file}")
        else:
            # User provided explicit path or it was set for non-benchmark action
            print(f"VFIO tracing enabled -> {config['vfio_trace_file']}")

    # Choose spawner
    if is_host:
        spawn_runner = spawn_host_runner(config=config)
    else:
        spawn_runner = spawn_qemu(qemu_cmd, numa_node=resource.numa_node, config=config)

    with spawn_runner as runner:
        if pin:
            runner.pin_vcpu(pin_base)

        runner.wait_for_ssh()

        with monitor_with_sar(runner, outputdir_host, timestamp, config):
            if is_host:
                action_fn(name=name, vm=runner, timestamp=timestamp, **action_config)
            else:
                with monitor_with_perf_kvm(
                    outputdir_host, timestamp, config, vm=runner
                ):
                    action_fn(
                        name=name, vm=runner, timestamp=timestamp, **action_config
                    )

        runner.shutdown()


def do_action(action: str, **kwargs: Any) -> None:
    """Route actions to appropriate handlers (VM or host runner).

    Detects execution mode (VM vs host) from config and routes actions accordingly.
    """
    config = kwargs.get("config", {})
    is_host = config.get("type") == "host"

    if action == "attach":
        handler = start_and_attach_host if is_host else start_and_attach
        handler(**kwargs)
    elif action == "ssh-cmd":
        handler = ssh_cmd_host if is_host else ssh_cmd
        handler(**kwargs)
    elif action.startswith("run-"):
        # Benchmark actions use unified handler
        action_type = action.replace("run-", "")
        if action_type in ACTIONS:
            run_benchmark_action(action_type=action_type, **kwargs)
        else:
            raise ValueError(
                f"Unknown action: {action}. Available run-* actions: {['run-' + k for k in ACTIONS.keys()]}"
            )
    else:
        raise ValueError(f"Unknown action: {action}")


# ------------------------------------------------------------


# examples:
# inv vm.start --type snp --size small
# inv vm.start --type normal --no-direct
# inv vm.start --type snp --action run-phoronix
@task
def start(
    ctx: Any,
    type: str = "amd",  # amd, snp, intel, tdx
    size: str = "medium",  # small, medium, large, numa
    hostname: str = None,  # by default use the local hostname
    direct: bool = False,  # if True, do direct boot. otherwise boot from the disk
    action: str = "attach",
    ssh_port: int = SSH_PORT,
    guest_cid: int = 11,  # Guest CID for vsock (only for TDX)
    pin: bool = True,  # if True, pin vCPUs
    pin_base: Optional[int] = None,  # pinning base
    extra_cmdline: str = "",  # extra kernel cmdline (only for direct boot)
    extra_qemu_cmd: str = "",
    # ssh_cmd options
    ssh_cmd: [str] = [],
    # boot eval options
    boot_trace: bool = True,
    boot_prealloc: bool = True,
    # application bench options
    repeat: int = 1,
    virtio_iommu: bool = False,  # enable VIRTIO_F_ACCESS_PLATFORM (VIRTIO_F_IOMMU_PLATFORM) feature bit
    # virtio-nic options
    virtio_nic: bool = False,
    virtio_nic_vhost: bool = False,
    virtio_nic_mq: bool = False,
    virtio_nic_tap: str = "tap_cvm",
    virtio_nic_mtap: str = "mtap_cvm",
    # virtio-blk options
    virtio_blk: Optional[
        str
    ] = None,  # create a virtio-blk backed by a specified file (or drive)
    virtio_blk_aio: str = "native",
    virtio_blk_direct: bool = True,
    virtio_blk_iothread: bool = True,
    # NVMe options
    nvme: bool = False,  # create an emulated NVMe device
    nvme_size: str = "500G",  # size of NVMe namespace (e.g., "4G", "8G")
    nvme_bps_rd: str = "10G",  # read bandwidth limit (e.g., "10G", "500M")
    nvme_bps_wr: str = "2.5G",  # write bandwidth limit (e.g., "2.5G", "500M")
    tls: bool = False,
    warn: bool = True,
    name_extra: str = "",
    # VFIO PCIe passthrough options
    vfio_pcie: List[
        str
    ] = [],  # PCIe device IDs for VFIO passthrough (e.g., ["01:00.0", "02:00.0"])
    edu: bool = False,  # Add edu device
    vfio_trace: bool = False,  # Enable VFIO region tracing (requires upstream QEMU)
    vfio_trace_file: Optional[str] = None,  # Custom path for VFIO trace output
    attestation: bool = False,  # Use qemu-amd for attestation (SNP only, incompatible with VFIO)
    # Shared monitoring options (for benchmark actions)
    sar_enabled: bool = True,  # Enable sar monitoring during benchmarks
    sar_interval: int = 1,  # SAR sampling interval in seconds
    sar_options: str = "-u -r -n DEV",  # SAR options: -u=CPU, -r=memory, -n DEV=network
    kvm_perf_enabled: bool = True,  # Enable perf monitoring during benchmarks
    action_config: Optional[dict] = None,  # Action-specific configuration (for run-fio)
) -> None:
    if hostname is None:
        hostname = socket.gethostname()
    config: dict = locals()
    resource: VMResource = get_vm_resource(hostname, size)
    config["resource"] = resource

    # vfio_pcie is already a list of strings
    vfio_devices = vfio_pcie
    config["vfio_devices"] = vfio_devices

    # Validate VFIO tracing requirements
    if vfio_trace and not vfio_devices:
        raise ValueError("VFIO tracing requires at least one VFIO device (--vfio-pcie)")

    # Configure VFIO trace file path based on action type
    if vfio_trace and vfio_trace_file is None:
        # Determine output directory based on action
        if action in ["run-fio", "run-accelstore", "run-nvbandwidth", "run-openssl"]:
            # Pre-compute the full output path so QEMU uses the correct file
            timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            vm_name = f"{type}-{'direct' if direct else 'disk'}-{size}" + name_extra
            action_type_key = action.replace("run-", "")
            action_obj = ACTIONS[action_type_key]
            action_config_dict = config.get("action_config") or {}
            path_components = action_obj.path_fn(vm_name, action_config_dict)
            outputdir_host_pre, _, _ = get_benchmark_output_path(
                *path_components, create_dirs=True
            )
            trace_file = outputdir_host_pre / f"{timestamp}_vfio_trace.log"
            config["vfio_trace_file"] = str(trace_file)
        else:
            # For non-benchmark actions (attach, ssh-cmd), use current directory
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            name_part = f"{type}-{'direct' if direct else 'disk'}-{size}"
            trace_file = Path(f"./vfio_trace_{name_part}_{timestamp}.log")
            config["vfio_trace_file"] = str(trace_file)
            print(f"VFIO tracing enabled -> {trace_file}")

    if direct and (type == "intel-ubuntu" or type == "tdx-ubuntu"):
        raise NotImplementedError(
            "No support of direct boot of ubuntu (use --no-direct option)"
        )

    SUPPORTED_TYPES = ["amd", "snp", "host"]
    if type not in SUPPORTED_TYPES:
        raise ValueError("Type needs to be:", SUPPORTED_TYPES)

    if attestation:
        if type != "snp":
            raise ValueError("Attestation requires SNP mode (--type snp)")
        if vfio_devices:
            raise ValueError(
                "VFIO device passthrough is not supported with attestation mode. "
                "The qemu-amd binary required for attestation has broken VFIO support for SEV-SNP. "
                "Please use either --attestation OR --vfio-pcie, not both."
            )

    # Handle host runner (no QEMU, runs directly on host)
    if type == "host":
        name = f"host-{size}" + name_extra
        print(f"Starting host runner: {name}")
        # Use unified action handler (will detect host runner via config)
        do_action(action, pin=pin, name=name, config=config)
        return

    # VM-based execution (amd, snp, etc.)
    name = type
    if direct:
        name = f"{name}-direct"

    confidential = type in ["snp"]
    qemu_cmd = get_amd_qemu_cmd_general(
        resource,
        config,
        name,
        direct,
        confidential,
        edu,
        config.get("vfio_trace", False),
        Path(config["vfio_trace_file"]) if config.get("vfio_trace_file") else None,
    )

    if virtio_nic:
        qemu_cmd += qemu_option_virtio_nic(
            tap=virtio_nic_tap,
            mtap=virtio_nic_mtap,
            vhost=virtio_nic_vhost,
            mq=virtio_nic_mq,
            config=config,
        )

    if virtio_blk:
        virtio_blk = Path(virtio_blk)
        print(f"Use virtio-blk: {virtio_blk}")
        if virtio_blk.is_block_device():
            if warn:
                print(
                    f"WARN: use {virtio_blk} as a virtio-blk. This overrides the existing disk image. Ok? [y/N]"
                )
                ok = input()
                if ok != "y":
                    return
        elif not virtio_blk.is_file():
            print(f"{virtio_blk} is not a file nor a block device")
            return
        qemu_cmd += qemu_option_virtio_blk(
            virtio_blk,
            virtio_blk_aio,
            virtio_blk_direct,
            virtio_blk_iothread,
            virtio_iommu,
        )

    nvme_backing_file = None
    if nvme:
        print(
            f"Use emulated NVMe: size={nvme_size}, bps_rd={nvme_bps_rd}, bps_wr={nvme_bps_wr}"
        )
        nvme_opts, nvme_backing_file = qemu_option_nvme(
            size=nvme_size, bps_rd=nvme_bps_rd, bps_wr=nvme_bps_wr
        )
        qemu_cmd += nvme_opts
    if extra_qemu_cmd:
        qemu_cmd += shlex.split(extra_qemu_cmd)

    if config["pin_base"] is None:
        config.pop("pin_base", None)

    # Bind all VFIO devices before VM start
    vfio_original_drivers = {}  # Map device -> original driver
    for device in vfio_devices:
        original_driver = bind_device_to_vfio(device)
        if original_driver:
            vfio_original_drivers[device] = original_driver

    name = f"{type}-{'direct' if direct else 'disk'}-{size}" + name_extra
    print(f"Starting VM: {name}")

    try:
        do_action(action, qemu_cmd=qemu_cmd, pin=pin, name=name, config=config)
    finally:
        # Clean up NVMe backing file
        if nvme_backing_file:
            try:
                os.unlink(nvme_backing_file)
                print(f"Cleaned up NVMe backing file: {nvme_backing_file}")
            except OSError as e:
                print(f"Warning: Failed to clean up NVMe backing file: {e}")
        # Restore devices in REVERSE order (GPU first, then NVMe)
        for device in reversed(vfio_devices):
            if device in vfio_original_drivers:
                try:
                    unbind_device_from_vfio(device, vfio_original_drivers[device])
                except Exception as e:
                    print(f"Warning: Failed to restore {device}: {e}")
                    # Continue restoring other devices
