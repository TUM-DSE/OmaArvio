#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""GPU management tasks using NVIDIA gpu-admin-tools."""

import errno
import os
import re
import subprocess
from pathlib import Path
from typing import Any, List, Optional, Tuple

from invoke import task

from core.tasks.config import PROJECT_ROOT
from core.tasks.procs import run
from core.tasks.utils.device import Devices
from core.tasks.utils.pci import get_pci_device
from core.tasks.utils.vfio import get_pci_ids


GPU_TOOLS_CMD = "nvidia-gpu-tools"
NVIDIA_DRIVER = "nvidia"
VFIO_DRIVER = "vfio-pci"
SYSFS_PCI = Path("/sys/bus/pci")


def _gpu_pci_id() -> str:
    """Return nvidia-gpu-tools selector arguments"""
    devices = Devices()
    gpu_bdf = devices.gpu_pci
    if gpu_bdf is None:
        raise ValueError(
            f"No GPU PCI BDF configured for host '{devices.hostname}'. "
            f"Please add gpu_pci to config.toml."
        )
    return gpu_bdf


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError(
            "Rebinding the GPU driver requires root. Use oma-arvio-root."
        )


def _current_driver(gpu_bdf: str) -> Optional[str]:
    """Return the name of the driver bound to the GPU, or None if unbound."""
    driver_path = SYSFS_PCI / "devices" / gpu_bdf / "driver"
    return driver_path.resolve().name if driver_path.exists() else None


def _remove_vfio_dynamic_id(gpu_bdf: str) -> None:
    """Drop the vendor:device ID that bind_device_to_vfio() adds via new_id.

    Otherwise vfio-pci keeps claiming every GPU of this model on later probes.
    """
    remove_id = SYSFS_PCI / "drivers" / VFIO_DRIVER / "remove_id"
    if not remove_id.exists():
        return
    vendor_id, device_id = get_pci_ids(gpu_bdf)
    try:
        remove_id.write_text(f"{vendor_id} {device_id}")
        print(f"Removed {vendor_id}:{device_id} from {VFIO_DRIVER} dynamic IDs")
    except OSError as e:
        # ENODEV: the ID was never added
        if e.errno != errno.ENODEV:
            raise


@task
def unbind_nvidia(c: Any) -> None:
    """Release the GPU from the nvidia driver.

    gpu-admin-tools cannot map BAR0 while the nvidia driver holds it, so run
    this before gpu.query-cc-mode / gpu.set-cc-mode-*.
    Requires root privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        oma-arvio-root gpu.unbind-nvidia
    """
    _require_root()
    gpu_bdf = _gpu_pci_id()
    driver = _current_driver(gpu_bdf)
    if driver is None:
        print(f"GPU {gpu_bdf} is not bound to any driver")
        return
    if driver != NVIDIA_DRIVER:
        print(f"GPU {gpu_bdf} is bound to {driver}, not {NVIDIA_DRIVER}; leaving it")
        return
    print(f"Unbinding GPU {gpu_bdf} from {NVIDIA_DRIVER}...")
    (SYSFS_PCI / "drivers" / NVIDIA_DRIVER / "unbind").write_text(gpu_bdf)
    print(f"GPU {gpu_bdf} unbound from {NVIDIA_DRIVER}")


@task
def bind_nvidia(c: Any) -> None:
    """Bind the GPU back to the nvidia driver.

    Releases the GPU from vfio-pci first if needed (vm.start --vfio-pcie
    leaves a GPU that had no driver on vfio-pci).
    Requires root privileges.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        oma-arvio-root gpu.bind-nvidia
    """
    _require_root()
    gpu_bdf = _gpu_pci_id()
    driver = _current_driver(gpu_bdf)
    if driver == NVIDIA_DRIVER:
        print(f"GPU {gpu_bdf} is already bound to {NVIDIA_DRIVER}")
        return
    if driver is not None:
        print(f"Unbinding GPU {gpu_bdf} from {driver}...")
        (SYSFS_PCI / "drivers" / driver / "unbind").write_text(gpu_bdf)
    _remove_vfio_dynamic_id(gpu_bdf)

    nvidia_driver = SYSFS_PCI / "drivers" / NVIDIA_DRIVER
    if not nvidia_driver.exists():
        # Loading the module probes the now driverless GPU by itself
        run(["modprobe", NVIDIA_DRIVER])
    if _current_driver(gpu_bdf) != NVIDIA_DRIVER:
        print(f"Binding GPU {gpu_bdf} to {NVIDIA_DRIVER}...")
        try:
            (nvidia_driver / "bind").write_text(gpu_bdf)
        except OSError as e:
            raise RuntimeError(
                f"Failed to bind GPU {gpu_bdf} to {NVIDIA_DRIVER}: {e}. "
                f"Check dmesg for the driver's error."
            ) from e
    print(f"GPU {gpu_bdf} bound to {NVIDIA_DRIVER}")


def _vfio_in_use(gpu_bdf: str) -> bool:
    """Return True if a process (e.g. a running VM) has the GPU's VFIO device open."""
    dev = SYSFS_PCI / "devices" / gpu_bdf
    vfio_nodes = set()
    if (dev / "iommu_group").exists():
        vfio_nodes.add(Path("/dev/vfio") / (dev / "iommu_group").resolve().name)
    if (dev / "vfio-dev").exists():
        vfio_nodes |= {
            Path("/dev/vfio/devices") / n.name for n in (dev / "vfio-dev").iterdir()
        }
    for fd in Path("/proc").glob("[0-9]*/fd/*"):
        try:
            if Path(os.readlink(fd)) in vfio_nodes:
                return True
        except OSError:
            # The process exited while scanning
            continue
    return False


def _query_cc_mode(gpu_bdf: str, driver: Optional[str]) -> str:
    """Return the GPU's CC mode, or why it could not be queried."""
    if os.geteuid() != 0:
        return "unknown (needs root, use oma-arvio-root)"
    if driver == NVIDIA_DRIVER:
        return "unknown (nvidia driver holds the GPU, run gpu.unbind-nvidia first)"
    if driver not in (None, VFIO_DRIVER):
        return f"unknown (GPU is bound to {driver})"
    if driver == VFIO_DRIVER and _vfio_in_use(gpu_bdf):
        return "unknown (GPU is in use by a VM)"
    result = run(
        [GPU_TOOLS_CMD, "--gpu-bdf", gpu_bdf, "--query-cc-mode"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        verbose=False,
        cwd=PROJECT_ROOT,
    )
    match = re.search(r"CC mode is (\S+)", result.stdout)
    if match is None:
        return f"unknown ({GPU_TOOLS_CMD} failed, run gpu.query-cc-mode for details)"
    return match.group(1)


@task
def check(c: Any) -> None:
    """Show the GPU's PCI address and ID, bound driver, and CC mode.

    The CC mode is read with gpu-admin-tools, which needs root and a GPU that
    the nvidia driver does not hold; otherwise it is reported as unknown.
    Uses the current host's gpu_pci from config.toml.

    Examples:
        oma-arvio-root gpu.check
    """
    gpu_bdf = _gpu_pci_id()
    pci_device = get_pci_device(gpu_bdf)
    driver = _current_driver(gpu_bdf)
    print(f"PCI address: {gpu_bdf}")
    print(f"PCI ID:      {pci_device.vendor_id}:{pci_device.device_id}")
    print(f"Driver:      {driver or 'none'}")
    print(f"CC mode:     {_query_cc_mode(gpu_bdf, driver)}")


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
