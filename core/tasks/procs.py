#!/usr/bin/env python3

# Based on
# https://github.com/Mic92/vmsh/blob/358cd4b6ec7de0dcac05a12e32486ef30658018c/tests/procs.py

import json
import os
import subprocess
from pathlib import Path
from typing import IO, Dict, List, Optional, Text, Union
from core.tasks.config import PROJECT_ROOT

ChildFd = Union[None, int, IO]


def pprint_cmd(cmd: List[str], extra_env: Optional[Dict[str, str]] = None) -> None:
    if extra_env is None:
        extra_env = {}
    env_string = []
    for k, v in extra_env.items():
        env_string.append(f"{k}={v}")
    print(f"$ {' '.join(env_string + cmd)}", flush=True)


def run(
    cmd: List[str],
    extra_env: Optional[Dict[str, str]] = None,
    stdout: ChildFd = subprocess.PIPE,
    stderr: ChildFd = None,
    input: Optional[str] = None,
    stdin: ChildFd = None,
    check: bool = True,
    verbose: bool = True,
    cwd: Optional[Path] = None,
) -> "subprocess.CompletedProcess[Text]":
    if extra_env is None:
        extra_env = {}
    env = os.environ.copy()
    env.update(extra_env)
    if verbose:
        pprint_cmd(cmd, extra_env)
    return subprocess.run(
        cmd,
        stdout=stdout,
        stderr=stderr,
        check=check,
        env=env,
        text=True,
        input=input,
        stdin=stdin,
        cwd=cwd,
    )


def systemd_run(
    cmd: List[str],
    extra_env: Optional[Dict[str, str]] = None,
    stdout: ChildFd = subprocess.PIPE,
    stderr: ChildFd = None,
    input: Optional[str] = None,
    stdin: ChildFd = None,
    check: bool = True,
    verbose: bool = True,
    cwd: Optional[Path] = None,
    cpus: int = 4,
    memory_gigabytes: int = 8,
    env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Run a command with systemd-run with memory and CPU restrictions."""
    if extra_env is None:
        extra_env = {}
    if env is None:
        env = {}
    assert memory_gigabytes >= 1
    # if 0 this is an empty string, which means no restrictions
    mask = ",".join(map(str, range(cpus)))
    high_mem = (memory_gigabytes - 0.5) * 1000
    systemd_cmd = [
        "systemd-run",
        "--pty",
        "--wait",
        "--collect",
        "-p",
        f"MemoryHigh={high_mem}M",
        "-p",
        f"MemoryMax={memory_gigabytes}G",
        "-p",
        f"AllowedCPUs={mask}",
    ]
    for k, v in env.items():
        systemd_cmd.append(f"--setenv={k}={v}")
    systemd_cmd.append("--")
    systemd_cmd.extend(cmd)
    return run(
        systemd_cmd, extra_env, stdout, stderr, input, stdin, check, verbose, cwd
    )


def get_nix_env() -> Dict[str, str]:
    """Get environment variables from Nix benchmarking dev shell.

    This should be called once and cached (e.g., in HostRunner.__init__),
    not on every command execution.

    Returns:
        Dictionary of environment variables from the Nix dev shell.
        Includes system paths (/nix/var/nix/profiles/default/bin, /run/current-system/sw/bin)
        in PATH for access to system tools.
    """

    nix_cmd = [
        "nix",
        "print-dev-env",
        f"{PROJECT_ROOT}#benchmarking",
        "--json",
    ]

    result = run(
        nix_cmd,
        extra_env={},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        verbose=False,  # Don't spam output during init
    )

    # Parse JSON and extract environment variables
    dev_env = json.loads(result.stdout)
    env_vars = {}

    for var_name, var_info in dev_env.get("variables", {}).items():
        if "value" in var_info:
            # Ensure that the value of the environment variable is a string, some might be valid JSON
            if isinstance(var_info["value"], (dict, list)):
                env_vars[var_name] = json.dumps(var_info["value"])
            else:
                env_vars[var_name] = str(var_info["value"])

    # Ensure system paths are in PATH for access to system tools like sudo, systemd-run
    system_paths = [
        "/nix/var/nix/profiles/default/bin",
        "/run/current-system/sw/bin",
    ]

    if "PATH" in env_vars:
        # Prepend system paths to existing PATH
        existing_path = env_vars["PATH"]
        env_vars["PATH"] = ":".join([existing_path] + system_paths)
    else:
        # Create PATH with system paths
        env_vars["PATH"] = ":".join(system_paths)

    return env_vars


def system_run(
    cmd: List[str],
    nix_env: Dict[str, str],
    extra_env: Optional[Dict[str, str]] = None,
    stdout: ChildFd = subprocess.PIPE,
    stderr: ChildFd = None,
    input: Optional[str] = None,
    stdin: ChildFd = None,
    check: bool = True,
    verbose: bool = False,
    cwd: Optional[Path] = None,
    cpus: Optional[int] = None,
    memory_gigabytes: Optional[int] = None,
) -> "subprocess.CompletedProcess[Text]":
    """Run a command with Nix environment and systemd-run resource limits.

    This wraps the command with 'sudo systemd-run' to:
    1. Set Nix development environment variables (from get_nix_env())
    2. Enforce CPU and memory limits via systemd cgroups
    3. Create isolated /share bind mount pointing to PROJECT_ROOT

    Args:
        cmd: Command to run (e.g., ["spdk-fio", "--help"])
        nix_env: Environment variables from Nix dev shell (from get_nix_env())
        extra_env: Additional environment variables
        stdout: stdout file descriptor
        stderr: stderr file descriptor
        input: Input string to pass to stdin
        stdin: stdin file descriptor
        check: Whether to raise exception on non-zero exit
        verbose: Whether to print the command
        cwd: Working directory (defaults to None)
        cpus: Number of CPUs to allow (required)
        memory_gigabytes: Memory limit in GB (required)

    Returns:
        CompletedProcess with stdout/stderr from the command
    """
    if extra_env is None:
        extra_env = {}

    # Get the flake root - assumes this script is in tasks/ subdirectory
    from core.tasks.config import PROJECT_ROOT

    # Resource limits are required
    if cpus is None or memory_gigabytes is None:
        raise ValueError("cpus and memory_gigabytes are required for system_run")

    assert memory_gigabytes >= 1, "Memory must be at least 1GB"

    # Build CPU mask (e.g., "0,1,2,3" for 4 CPUs)
    cpu_mask = ",".join(map(str, range(cpus)))

    # Calculate memory limits
    # MemoryHigh is set slightly below MemoryMax to trigger reclaim before hitting hard limit
    high_mem = (memory_gigabytes - 0.5) * 1000  # in MB

    # Build systemd-run command with resource limits and /share bind mount
    # Note: sudo is required for bind mounts
    # Change to --pipe mode if input is provided, so that stdin actually works for systemd-run,
    # use --pty so that commands are stopped correctly on Ctrl-C
    run_type = "--pipe" if input else "--pty"
    systemd_cmd = [
        "sudo",
        "systemd-run",
        run_type,
        "--wait",
        "--collect",
        "-p",
        f"MemoryHigh={high_mem}M",
        "-p",
        f"MemoryMax={memory_gigabytes}G",
        "-p",
        f"AllowedCPUs={cpu_mask}",
        # Create isolated /share bind mount pointing to PROJECT_ROOT
        # This is only visible to the process, not system-wide
        # Format: BindPaths=source:destination
        f"--property=BindPaths={PROJECT_ROOT}:/share",
    ]

    module_shared_data = os.environ.get("MODULE_SHARED_DATA")
    if module_shared_data:
        systemd_cmd.append(f"--property=BindPaths={module_shared_data}:/shared:ro")

    # Add Nix environment variables via --setenv
    for var_name, var_value in nix_env.items():
        systemd_cmd.append(f"--setenv={var_name}={var_value}")

    # Add user's extra environment variables
    for k, v in extra_env.items():
        systemd_cmd.append(f"--setenv={k}={v}")

    systemd_cmd.append("--")
    systemd_cmd.append("env")
    systemd_cmd.extend(cmd)

    # Note: All environment variables (nix_env and extra_env) are already added
    # to systemd-run via --setenv, so we pass empty dict to run()
    return run(systemd_cmd, {}, stdout, stderr, input, stdin, check, verbose, cwd)
