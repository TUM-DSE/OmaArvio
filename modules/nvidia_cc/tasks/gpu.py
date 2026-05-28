#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""GPU management tasks using NVIDIA gpu-admin-tools."""

import socket
from typing import Any, List, Tuple

from invoke import task

from core.tasks.config import PROJECT_ROOT, load_config
from core.tasks.procs import run


GPU_TOOLS_CMD = "nvidia-gpu-tools"


def _gpu_pci_id() -> str:
    """Return nvidia-gpu-tools selector arguments"""
    cfg = load_config()
    hostname = socket.gethostname()

    if hostname not in cfg.hosts:
        known_hosts = ", ".join(sorted(cfg.hosts))
        raise ValueError(
            f"No host config found for '{hostname}'. Known hosts: {known_hosts}"
        )

    gpu_bdf = cfg.hosts[hostname].gpu_pci
    if gpu_bdf is None:
        raise ValueError(
            f"No GPU PCI BDF configured for host '{hostname}'. "
            f"Please add gpu_pci to config.toml."
        )
    return gpu_bdf


@task
def set_cc_mode_devtools(c: Any) -> None:
    """Enable Confidential Computing mode (devtools) on GPU.

    This enables all CC security features but allows DevTools profiling/debugging.
    Requires sudo privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        sudo inv gpu.set-cc-mode-devtools
    """
    gpu_bdf = _gpu_pci_id()
    cmd = [
        GPU_TOOLS_CMD,
        "--gpu-bdf",
        gpu_bdf,
        "--set-cc-mode=devtools",
        "--reset-after-cc-mode-switch",
    ]
    print(f"Setting CC mode to 'devtools' on GPU {gpu_bdf}...")
    run(cmd, cwd=PROJECT_ROOT, stdout=None, stderr=None)
    print(f"CC mode set to 'devtools' on GPU {gpu_bdf}")


@task
def set_cc_mode_off(c: Any) -> None:
    """Disable Confidential Computing mode on GPU.

    The GPU operates in its default mode; no CC features are enabled.
    Requires sudo privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        sudo inv gpu.set-cc-mode-off
    """
    gpu_bdf = _gpu_pci_id()
    cmd = [
        GPU_TOOLS_CMD,
        "--gpu-bdf",
        gpu_bdf,
        "--set-cc-mode=off",
        "--reset-after-cc-mode-switch",
    ]
    print(f"Setting CC mode to 'off' on GPU {gpu_bdf}...")
    run(cmd, cwd=PROJECT_ROOT, stdout=None, stderr=None)
    print(f"CC mode set to 'off' on GPU {gpu_bdf}")


@task
def set_cc_mode_on(c: Any) -> None:
    """Enable full Confidential Computing mode on GPU.

    All supported GPU security features are enabled (e.g., bus encryption,
    performance counters off). DevTools profiling/debugging is blocked.
    Requires sudo privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        sudo inv gpu.set-cc-mode-on
    """
    gpu_bdf = _gpu_pci_id()
    cmd = [
        GPU_TOOLS_CMD,
        "--gpu-bdf",
        gpu_bdf,
        "--set-cc-mode=on",
        "--reset-after-cc-mode-switch",
    ]
    print(f"Setting CC mode to 'on' on GPU {gpu_bdf}...")
    run(cmd, cwd=PROJECT_ROOT, stdout=None, stderr=None)
    print(f"CC mode set to 'on' on GPU {gpu_bdf}")


@task
def query_cc_mode(c: Any) -> None:
    """Query the current Confidential Computing mode of GPU.

    Requires sudo privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        sudo inv gpu.query-cc-mode
    """
    gpu_bdf = _gpu_pci_id()
    cmd = [
        GPU_TOOLS_CMD,
        "--gpu-bdf",
        gpu_bdf,
        "--query-cc-mode",
    ]
    run(cmd, cwd=PROJECT_ROOT, stdout=None, stderr=None)
