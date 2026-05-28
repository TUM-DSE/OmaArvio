#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSD pre-conditioning utilities for storage benchmarks.

Provides configurable workload independent pre-conditioning (WIPC) using
FIO to fill the SSD and reach steady state before benchmarking.

Supported schemes:
- SD_CUSTOM: LFSR-based sprandom + 10-minute random write
- SNIA: 2x sequential 128K writes across full LBA range
- NULL: Skip preconditioning entirely
"""

from pathlib import Path
from typing import Union
import json

from core.tasks.qemu import QemuVm, HostRunner
from core.tasks.config import PreconditionScheme, DEFAULT_PRECONDITION_SCHEME

# Preconditioning block size for SD_CUSTOM scheme
# 16KB based on https://github.com/axboe/fio/discussions/1990#discussioncomment-14808186
PRECONDITION_BLOCK_SIZE = "16384"

# Preconditioning block size for SNIA scheme
SNIA_SEQ_BLOCK_SIZE = "131072"  # 128KB

# Bandwidth logging interval
BW_LOG_INTERVAL_MS = 1000  # Log bandwidth every 1 second


def get_nvme_lba_formats(vm: Union[QemuVm, HostRunner], dev_path: str) -> list:
    """Query supported LBA formats from NVMe device.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-... or /dev/nvme0n1)

    Returns:
        List of dicts with keys: lbaf_index (int), block_size (int)
    """
    result = vm.ssh_cmd(
        ["nvme", "id-ns", dev_path, "-o", "json"], check=True, bypass=True
    )
    ns_info = json.loads(result.stdout)
    lbafs = ns_info.get("lbafs", [])

    formats = []
    for i, lbaf in enumerate(lbafs):
        ds = lbaf.get("ds", 0)
        ms = lbaf.get("ms", 0)
        if ds > 0 and ms == 0:
            formats.append({"lbaf_index": i, "block_size": 2**ds})
    return formats


def get_lbaf_index(
    vm: Union[QemuVm, HostRunner], dev_path: str, block_size: int
) -> int:
    """Find the LBAF index for a given block size.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path
        block_size: Desired block size in bytes (e.g., 512, 4096)

    Returns:
        LBAF index for the requested block size

    Raises:
        ValueError: If the device does not support the requested block size
    """
    formats = get_nvme_lba_formats(vm, dev_path)
    for fmt in formats:
        if fmt["block_size"] == block_size:
            return fmt["lbaf_index"]

    supported = [f"{fmt['block_size']}B (lbaf {fmt['lbaf_index']})" for fmt in formats]
    raise ValueError(
        f"Device {dev_path} does not support block size {block_size}B. "
        f"Supported: {', '.join(supported)}"
    )


def nvme_secure_erase(
    vm: Union[QemuVm, HostRunner], dev_path: str, block_size: int = None
):
    """Secure erase NVMe device using nvme format command.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-... or /dev/nvme0n1)
        block_size: Optional LBA block size to set (512, 4096).
                    If specified, also changes the LBA format during the erase.
    """
    cmd = ["nvme", "format", dev_path, "-s", "1"]

    if block_size is not None:
        lbaf = get_lbaf_index(vm, dev_path, block_size)
        cmd.extend(["-l", str(lbaf)])
        print(
            f"Secure erasing NVMe device: {dev_path} (setting LBA format to {block_size}B, lbaf={lbaf})..."
        )
    else:
        print(f"Secure erasing NVMe device: {dev_path}...")

    vm.ssh_cmd(cmd, check=True, bypass=True)

    # Ensure that the device is actually ready (otherwise we might run into race conditions, especially on the host)
    vm.ssh_cmd(
        ["udevadm", "trigger", "--settle", "--name-match", dev_path],
        check=True,
        bypass=True,
    )


def get_nvme_char_device(vm: Union[QemuVm, HostRunner], dev_path: str) -> str:
    """Get NVMe generic character device from block device path.

    Resolves the ng character device needed for io_uring_cmd passthrough.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-KIOXIA_...)

    Returns:
        Character device path (e.g., /dev/ng0n1)
    """
    # Resolve symlink to get block device name (e.g., nvme0n1)
    result = vm.ssh_cmd(["readlink", "-f", dev_path], check=True, bypass=True)
    block_dev = result.stdout.strip()  # /dev/nvme0n1
    block_name = block_dev.split("/")[-1]  # nvme0n1

    # Find ng device in /sys/block/<block_name>/device/
    sys_path = f"/sys/block/{block_name}/device"
    result = vm.ssh_cmd(["ls", sys_path], check=True, bypass=True)

    for entry in result.stdout.split():
        if entry.startswith("ng"):
            return f"/dev/{entry}"

    raise RuntimeError(f"No ng device found for {dev_path}")


def run_precondition_sd_custom(
    vm: Union[QemuVm, HostRunner],
    dev_path: str,
    output_dir: Path,
    timestamp: str,
):
    """Run SD_CUSTOM pre-conditioning scheme.

    Uses random writes with LFSR generator (based on FIO sprandom.fio example)
    to properly fill the SSD and reach steady state before benchmarking.

    Always uses libaio for compatibility with both block and character devices.

    Stage 1: LFSR-based random write (sprandom) - fills entire device
    Stage 2: Time-based random write (10min) - reaches steady state

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-... or /dev/nvme0n1)
        output_dir: Directory to store pre-conditioning logs (guest path)
        timestamp: Timestamp string to include in log filenames
    """
    # Use block device directly with libaio
    filename = dev_path

    # Create precondition logs subdirectory
    precond_dir = output_dir / f"{timestamp}-precondition"
    vm.ssh_cmd(["mkdir", "-p", str(precond_dir)], check=True)

    # Stage 1: Random write preconditioning using FIO's sprandom feature
    # As we do not know the actual OP of the device, we error on the conservative side and add another random write stage
    print(f"[SD_CUSTOM] Stage 1: Running sprandom on {filename} (ioengine=libaio)...")
    cmd = [
        "fio",
        "--name=preconditioning_sprandom",
        "--ioengine=libaio",
        "--direct=1",
        "--rw=randwrite",
        f"--bs={PRECONDITION_BLOCK_SIZE}",
        f"--blockalign={PRECONDITION_BLOCK_SIZE}",
        "--norandommap=1",
        "--iodepth=64",
        "--sprandom=1",
        "--spr_op=0.12",
        "--spr_num_regions=1000",
        "--group_reporting=1",
        f"--filename={filename}",
        "--output=precondition_sprandom.out",
        "--bandwidth-log",
        f"--log_avg_msec={BW_LOG_INTERVAL_MS}",
    ]
    vm.ssh_cmd(cmd, check=True, cwd=str(precond_dir))

    # Stage 2: Time-based random write to reach steady state
    print(f"[SD_CUSTOM] Stage 2: Running 100% capacity randwrite on {filename}...")
    cmd = [
        "fio",
        "--name=preconditioning_randwrite",
        "--ioengine=libaio",
        "--direct=1",
        f"--filename={filename}",
        "--group_reporting=1",
        "--norandommap=1",
        "--rw=randwrite",
        f"--bs={PRECONDITION_BLOCK_SIZE}",
        "--iodepth=128",
        "--numjobs=4",
        "--size=100%",
        "--output=precondition_randwrite.out",
        "--bandwidth-log",
        f"--log_avg_msec={BW_LOG_INTERVAL_MS}",
    ]
    vm.ssh_cmd(cmd, check=True, cwd=str(precond_dir))

    print("[SD_CUSTOM] Preconditioning complete.")


def run_precondition_snia(
    vm: Union[QemuVm, HostRunner],
    dev_path: str,
    output_dir: Path,
    timestamp: str,
):
    """Run SNIA-style pre-conditioning scheme.

    Writes 128K blocks sequentially across the namespace's full LBA range twice
    to ensure the controller accesses the NAND media for each subsequent I/O.

    Always uses libaio for compatibility with both block and character devices.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-... or /dev/nvme0n1)
        output_dir: Directory to store pre-conditioning logs (guest path)
        timestamp: Timestamp string to include in log filenames
    """
    # Use block device directly with libaio
    filename = dev_path

    # Create precondition logs subdirectory
    precond_dir = output_dir / f"{timestamp}-precondition"
    vm.ssh_cmd(["mkdir", "-p", str(precond_dir)], check=True)

    print(f"[SNIA] Writing 128K blocks sequentially 2x across {filename}...")
    cmd = [
        "fio",
        "--name=preconditioning_sequential",
        "--ioengine=libaio",
        "--direct=1",
        "--rw=write",
        f"--bs={SNIA_SEQ_BLOCK_SIZE}",
        "--iodepth=128",
        "--numjobs=1",
        "--loops=2",
        "--group_reporting=1",
        f"--filename={filename}",
        "--output=precondition_sequential.out",
        "--bandwidth-log",
        f"--log_avg_msec={BW_LOG_INTERVAL_MS}",
    ]
    vm.ssh_cmd(cmd, check=True, cwd=str(precond_dir))

    print("[SNIA] Preconditioning complete.")


def run_precondition(
    vm: Union[QemuVm, HostRunner],
    dev_path: str,
    output_dir: Path = None,
    timestamp: str = None,
    scheme: Union[PreconditionScheme, str] = None,
):
    """Run SSD pre-conditioning before benchmark tests.

    Dispatches to the appropriate preconditioning scheme based on configuration.

    Args:
        vm: QemuVm or HostRunner instance
        dev_path: Device path (e.g., /dev/disk/by-id/nvme-... or /dev/nvme0n1)
        output_dir: Directory to store pre-conditioning logs (guest path)
        timestamp: Timestamp string to include in log filenames
        scheme: Preconditioning scheme to use (sd_custom or snia).
                Defaults to DEFAULT_PRECONDITION_SCHEME from config.py.
    """
    # Use default scheme if not specified
    if scheme is None:
        scheme = DEFAULT_PRECONDITION_SCHEME

    # Convert string to enum if needed
    if isinstance(scheme, str):
        scheme = PreconditionScheme(scheme)

    print(f"Starting preconditioning with scheme: {scheme.value}")

    if scheme == PreconditionScheme.NULL:
        print("[NULL] Skipping preconditioning.")
        return
    elif scheme == PreconditionScheme.SD_CUSTOM:
        run_precondition_sd_custom(vm, dev_path, output_dir, timestamp)
    elif scheme == PreconditionScheme.SNIA:
        run_precondition_snia(vm, dev_path, output_dir, timestamp)
    else:
        raise ValueError(f"Unknown preconditioning scheme: {scheme}")
