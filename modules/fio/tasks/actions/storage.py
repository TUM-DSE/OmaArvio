#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import json
import subprocess
import re
import time
from core.tasks.config import PROJECT_ROOT
from core.tasks.qemu import QemuVm, HostRunner, setup_hugepages
from core.tasks.utils.utils import get_benchmark_output_path

# Import NVMe utilities from the nvme module
from modules.nvme.tasks.utils.nvme import (
    PRECONDITION_BLOCK_SIZE,
    get_nvme_lba_formats,
    get_lbaf_index,
    nvme_secure_erase,
    get_nvme_char_device,
    run_precondition,
)
from core.tasks.actions.registry import register_action


def block_size_label(block_size: int) -> str:
    """Convert block size in bytes to a short label for directory names.

    Args:
        block_size: Block size in bytes (512, 4096)

    Returns:
        Short label string (e.g., "512", "4k")
    """
    labels = {
        512: "512",
        4096: "4k",
    }
    if block_size in labels:
        return labels[block_size]
    # Fallback: use raw byte count
    if block_size >= 1024 and block_size % 1024 == 0:
        return f"{block_size // 1024}k"
    return str(block_size)


def parse_size_to_mb(size_str: str) -> int:
    """Convert size string (e.g., '10G', '512M') to MB.

    Args:
        size_str: Size string with unit (e.g., '10G', '512M')

    Returns:
        Size in MB
    """
    size_str = size_str.upper().strip()
    if size_str.endswith("G"):
        return int(size_str[:-1]) * 1024
    elif size_str.endswith("M"):
        return int(size_str[:-1])
    elif size_str.endswith("T"):
        return int(size_str[:-1]) * 1024 * 1024
    else:
        # Assume MB if no unit
        return int(size_str)


def parse_job_filesystem(job_name: str) -> str:
    """Extract filesystem type from job name.

    Job name patterns:
    - luks-{fs}-{cipher}: e.g., luks-ext4-aes, luks-f2fs-aes
    - dmverity-{fs}: e.g., dmverity-ext4, dmverity-f2fs
    - fsverity-{fs}: e.g., fsverity-ext4, fsverity-f2fs
    - {fs}: e.g., ext4, f2fs (plain filesystem)

    Args:
        job_name: Job name string

    Returns:
        Filesystem type (ext4 or f2fs)

    Raises:
        ValueError: If filesystem type cannot be determined
    """
    for fs_type in ["ext4", "f2fs"]:
        if fs_type in job_name:
            return fs_type

    raise ValueError(f"Cannot determine filesystem from job name: {job_name}")


def make_filesystem(vm: QemuVm, device: str, fs_type: str, verity: bool = False):
    """Create filesystem on device.

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path
        fs_type: Filesystem type (ext4 or f2fs)
        verity: Enable fs-verity feature (only for ext4)

    Raises:
        ValueError: If filesystem type is unsupported
    """
    if fs_type == "ext4":
        cmd = ["mkfs.ext4", "-F"]
        if verity:
            cmd.extend(["-O", "verity"])
        cmd.append(device)
    elif fs_type == "f2fs":
        cmd = ["mkfs.f2fs", "-f"]
        if verity:
            # f2fs has built-in verity support via -O option
            cmd.extend(["-O", "verity"])
        cmd.append(device)
    else:
        raise ValueError(f"Unsupported filesystem type: {fs_type}")

    vm.ssh_cmd(cmd, check=True, bypass=True)


def create_test_file(
    vm: QemuVm, mount_point: str, filename: str = "testfile", size_mb: int = None
):
    """Create test file with random data.

    Args:
        vm: QemuVm or HostRunner instance
        mount_point: Mount point where the file should be created
        filename: Name of the test file (default: testfile)
        size_mb: Size of file in MB (default: 9216 MB, which is 10GB - 10%)
    """
    if size_mb is None:
        size_mb = 9216  # 10GB - 10%
    print(f"Creating test file with random data ({size_mb}MB)...")
    vm.ssh_cmd(
        [
            "dd",
            "if=/dev/urandom",
            f"of={mount_point}/{filename}",
            "bs=1M",
            f"count={size_mb}",
            "status=progress",
        ],
        check=True,
    )


def get_partition_name(device: str, partition_num: int) -> str:
    """Get partition name for a device.

    Handles different device path formats:
    - /dev/disk/by-id/ paths use -partX suffix
    - NVMe block devices use pX suffix
    - Other devices use just the number

    Args:
        device: Device path (e.g., /dev/disk/by-id/nvme-XXX or /dev/nvme0n1)
        partition_num: Partition number (1, 2, etc.)

    Returns:
        Full partition path with correct suffix
    """
    if device.startswith("/dev/disk/by-id/"):
        return f"{device}-part{partition_num}"
    elif "nvme" in device:
        return f"{device}p{partition_num}"
    else:
        return f"{device}{partition_num}"


def create_partition(vm: QemuVm, device: str, partitions: list):
    """Create partitions on device using parted.

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path
        partitions: List of (start, end) tuples for each partition
                   e.g., [("0%", "10G"), ("10G", "100%")]
    """
    # Build parted command
    parted_cmd = ["parted", "-s", device, "mklabel", "gpt"]

    for start, end in partitions:
        parted_cmd.extend(["mkpart", "primary", start, end])

    vm.ssh_cmd(parted_cmd, check=True, bypass=True)

    # Force kernel to re-read partition table
    vm.ssh_cmd(["partprobe", device], check=False, bypass=True)

    # Small delay to ensure partitions are fully visible
    time.sleep(0.5)


def format_luks_device_with_mode(
    vm: QemuVm,
    device: str,
    fs_type: str,
    size: str = "10G",
    passphrase: str = "test",
    cipher: str = "aes-xts-plain64",
    integrity: str = None,
    key_size: int = None,
) -> str:
    """Format device with LUKS encryption (supports different cipher modes).

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path
        fs_type: Filesystem type (ext4 or f2fs)
        size: Partition size
        passphrase: LUKS passphrase
        cipher: Cipher mode (aes-xts-plain64, aegis128-random, aes-gcm-random)
        integrity: Integrity algorithm (None, poly1305, hmac-sha256)

    Returns:
        Path to test file for FIO: /mnt/encrypted/testfile
    """
    print(
        f"Formatting LUKS device: {device} (cipher: {cipher}, size: {size}, fs: {fs_type})"
    )

    # Create partition
    create_partition(vm, device, [("0%", size)])
    partition = get_partition_name(device, 1)

    # Build LUKS format command
    luks_cmd = [
        "cryptsetup",
        "luksFormat",
        "--batch-mode",
        "--type=luks2",
        f"--cipher={cipher}",
    ]

    if integrity:
        luks_cmd.append(f"--integrity={integrity}")

    if key_size:
        luks_cmd.append(f"--key-size={key_size}")

    luks_cmd.append(partition)

    # Format with LUKS
    vm.ssh_cmd(luks_cmd, check=True, input=f"{passphrase}\n{passphrase}\n", bypass=True)

    # Open LUKS device
    vm.ssh_cmd(
        ["cryptsetup", "open", partition, "luks-encrypted"],
        check=True,
        input=f"{passphrase}\n",
        bypass=True,
    )

    # Create filesystem
    make_filesystem(vm, "/dev/mapper/luks-encrypted", fs_type)

    # Create mount point and mount
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(
        ["mount", "/dev/mapper/luks-encrypted", "/mnt/encrypted"],
        check=True,
        bypass=True,
    )

    # Create test file with random data (FS size - 20%)
    fs_size_mb = int(parse_size_to_mb(size) * 0.8)
    create_test_file(vm, "/mnt/encrypted", size_mb=fs_size_mb)

    return "/mnt/encrypted/testfile"


def format_luks_aes_device(
    vm: QemuVm, device: str, fs_type: str, size: str = "10G", passphrase: str = "test"
) -> str:
    """LUKS with AES-XTS-256 (standard LUKS2 mode)."""
    return format_luks_device_with_mode(
        vm, device, fs_type, size, passphrase, cipher="aes-xts-plain64", integrity=None
    )


def format_luks_aegis128_device(
    vm: QemuVm, device: str, fs_type: str, size: str = "10G", passphrase: str = "test"
) -> str:
    """LUKS with AEGIS-128 authenticated encryption."""
    return format_luks_device_with_mode(
        vm,
        device,
        fs_type,
        size,
        passphrase,
        cipher="aegis128-plain64",
        integrity="aead",
        key_size=128,
    )


def format_luks_aes_xts_device(
    vm: QemuVm, device: str, fs_type: str, size: str = "10G", passphrase: str = "test"
) -> str:
    """LUKS with AES-XTS + HMAC-SHA256."""
    return format_luks_device_with_mode(
        vm,
        device,
        fs_type,
        size,
        passphrase,
        cipher="aes-xts-random",
        integrity="hmac-sha256",
    )


def format_dmverity_device(
    vm: QemuVm, device: str, fs_type: str, size: str = "10G"
) -> str:
    """Format device with dm-verity integrity checking and create test file.

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path (e.g., /dev/nvme0n1)
        fs_type: Filesystem type (ext4 or f2fs)
        size: Partition size (default: 10G)

    Returns:
        Path to test file for FIO: /mnt/encrypted/testfile
    """
    print(f"Formatting dm-verity device: {device} (size: {size}, fs: {fs_type})")

    # Create two partitions: data (size) and hash (2GB)
    create_partition(
        vm, device, [("0%", size), (size, f"{parse_size_to_mb(size) + 2048}M")]
    )

    data_partition = get_partition_name(device, 1)
    hash_partition = get_partition_name(device, 2)

    # Create filesystem on data partition
    make_filesystem(vm, data_partition, fs_type)

    # Mount temporarily to create test file
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(["mount", data_partition, "/mnt/encrypted"], check=True, bypass=True)

    # Create test file with random data (data partition size - 20%)
    data_partition_mb = int(parse_size_to_mb(size) * 0.8)
    create_test_file(vm, "/mnt/encrypted", size_mb=data_partition_mb)

    # Unmount
    vm.ssh_cmd(["umount", "/mnt/encrypted"], check=True, bypass=True)

    # Create dm-verity hash table
    print("Creating dm-verity hash table...")
    result = vm.ssh_cmd(
        ["veritysetup", "format", data_partition, hash_partition],
        check=False,
        bypass=True,
    )
    # Extract root hash from output
    root_hash = None
    for line in result.stdout.splitlines():
        if "Root hash:" in line:
            root_hash = line.split("Root hash:")[1].strip()
            break

    if not root_hash:
        raise RuntimeError("Failed to extract root hash from veritysetup output")

    print(f"Root hash: {root_hash}")

    # Open dm-verity device (read-only)
    vm.ssh_cmd(
        ["veritysetup", "open", data_partition, "verity", hash_partition, root_hash],
        check=True,
        bypass=True,
    )

    # Mount dm-verity device (read-only)
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(
        ["mount", "-o", "ro", "/dev/mapper/verity", "/mnt/encrypted"],
        check=True,
        bypass=True,
    )

    return "/mnt/encrypted/testfile"


def format_fsverity_device(
    vm: QemuVm, device: str, fs_type: str, size: str = "10G"
) -> str:
    """Format device with fs-verity enabled filesystem and create test file.

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path (e.g., /dev/nvme0n1)
        fs_type: Filesystem type (ext4 or f2fs)
        size: Partition size (default: 10G)

    Returns:
        Path to test file for FIO: /mnt/encrypted/testfile
    """
    print(f"Formatting fs-verity device: {device} (size: {size}, fs: {fs_type})")

    # Create partition
    create_partition(vm, device, [("0%", size)])

    partition = get_partition_name(device, 1)

    # Create filesystem with verity feature
    make_filesystem(vm, partition, fs_type, verity=True)

    # Mount filesystem
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    vm.ssh_cmd(["mount", partition, "/mnt/encrypted"], check=True, bypass=True)

    # Create test file with random data (FS size - 20%)
    fs_size_mb = int(parse_size_to_mb(size) * 0.8)
    create_test_file(vm, "/mnt/encrypted", size_mb=fs_size_mb)

    # Enable fs-verity on test file
    print("Enabling fs-verity on test file...")
    vm.ssh_cmd(
        ["fsverity", "enable", "/mnt/encrypted/testfile"], check=True, bypass=True
    )

    # Verify fs-verity is enabled
    vm.ssh_cmd(
        ["fsverity", "measure", "/mnt/encrypted/testfile"], check=True, bypass=True
    )

    return "/mnt/encrypted/testfile"


def format_plain_device(
    vm: QemuVm,
    device: str,
    fs_type: str,
    size: str = "10G",
    file_size: str = None,
    mount_options: list = None,
) -> str:
    """Format device with plain filesystem and create test file.

    Args:
        vm: QemuVm or HostRunner instance
        device: Device path (e.g., /dev/nvme0n1)
        fs_type: Filesystem type (ext4 or f2fs)
        size: Partition size (default: 10G)

    Returns:
        Path to test file for FIO: /mnt/encrypted/testfile
    """
    print(f"Formatting plain device: {device} (size: {size}, fs: {fs_type})")

    # Create partition
    create_partition(vm, device, [("0%", size)])
    partition = get_partition_name(device, 1)

    # Create filesystem
    make_filesystem(vm, partition, fs_type)

    # Mount filesystem
    vm.ssh_cmd(["mkdir", "-p", "/mnt/encrypted"], check=True, bypass=True)
    mount_cmd = ["mount"]
    if mount_options:
        mount_cmd += ["-o", ",".join(mount_options)]
    mount_cmd += [partition, "/mnt/encrypted"]
    vm.ssh_cmd(mount_cmd, check=True, bypass=True)

    # Create test file with random data
    fs_size_mb = (
        int(parse_size_to_mb(file_size))
        if file_size is not None
        else int(parse_size_to_mb(size) * 0.8)
    )
    create_test_file(vm, "/mnt/encrypted", size_mb=fs_size_mb)

    return "/mnt/encrypted/testfile"


def cleanup_encrypted_device(vm: QemuVm, encryption_type: str, device: str):
    """Cleanup encrypted device setup.

    Args:
        vm: QemuVm or HostRunner instance
        encryption_type: Type of encryption (ext4, luks-aes, luks-aegis128, luks-aes-gcm, dmverity, fsverity)
        device: Device path (e.g., /dev/nvme0n1)
    """
    print(f"Cleaning up {encryption_type} device...")

    try:
        # Unmount
        vm.ssh_cmd(["umount", "/mnt/encrypted"], check=False, bypass=True)

        # Close encryption mappings
        # All LUKS variants use same cleanup
        if encryption_type.startswith("luks-"):
            vm.ssh_cmd(
                ["cryptsetup", "close", "luks-encrypted"], check=False, bypass=True
            )
        elif encryption_type == "dmverity":
            vm.ssh_cmd(["veritysetup", "close", "verity"], check=False, bypass=True)

        # Wipe partition table
        vm.ssh_cmd(["wipefs", "-a", device], check=False, bypass=True)

    except Exception as e:
        print(f"Warning: Cleanup error (non-fatal): {e}")


# --- FIO Job Configuration ---

# Engine-specific FIO parameters, injected as CLI args to override [global] in .fio files.
# The .fio files contain only workload job definitions (no engine params).
ENGINE_CONFIGS = {
    "libaio": {"ioengine": "libaio", "direct": "1", "thread": "1"},
    "spdk": {"ioengine": "spdk", "direct": "1", "thread": "1"},
    "io_uring_cmd": {"ioengine": "io_uring_cmd", "cmd_type": "nvme"},
}

# Order matters: io_uring_cmd first because it contains '-' which would match libaio prefix
KNOWN_ENGINES = ["io_uring_cmd", "libaio", "spdk"]

DEFAULT_FIO_JOB_DIRS = [Path("/shared/modules/fio")]


def resolve_fio_job_file(job: str, job_dirs: list[Path] = None) -> str:
    dirs = job_dirs if job_dirs else DEFAULT_FIO_JOB_DIRS
    filename = f"{job}.fio"
    for jobs_dir in dirs:
        candidate = jobs_dir / filename
        if candidate.exists():
            return str(candidate)
    return str(dirs[0] / filename)


# Device formatter dispatch — all have signature: (vm, device, fs_type, device_size) -> fio_target
STORAGE_FORMATTERS = {
    "plain": format_plain_device,
    "luks-aes": format_luks_aes_device,
    "luks-aegis128": format_luks_aegis128_device,
    "luks-aes-xts": format_luks_aes_xts_device,
    "dmverity": format_dmverity_device,
    "fsverity": format_fsverity_device,
}


@dataclass
class FioJobConfig:
    """Parsed FIO job configuration — single source of truth for all job parameters.

    Job names encode the engine prefix: "spdk", "libaio-ext4", "libaio-luks-ext4-aes", etc.
    Use FioJobConfig.from_job_name() to parse a job name into all derived fields.
    """

    job: str  # Full job name (e.g., "libaio-ext4")
    engine: str  # "libaio", "spdk", "io_uring_cmd"
    storage_type: str  # Full storage string: "raw", "ext4", "luks-ext4-aes", etc.
    storage_category: str  # Category: "raw", "plain", "luks", "dmverity", "fsverity"
    engine_args: dict  # CLI args for FIO engine
    job_file: str  # Guest path to .fio job file
    readonly: bool  # Whether to run with --readonly
    needs_cleanup: bool  # Whether device cleanup is needed after FIO
    needs_spdk_setup: bool  # Whether SPDK hugepages/binding setup is needed
    needs_precondition: bool  # Whether to run SSD preconditioning
    filesystem: str = None  # "ext4" or "f2fs" (None for raw)
    cipher: str = None  # "aes", "aegis128", "aes-xts" (None unless LUKS)
    formatter_key: str = None  # Key into STORAGE_FORMATTERS (None for raw)
    suffix: str = None  # Optional job variant suffix (e.g., "bandwidth", "latency")

    @staticmethod
    def from_job_name(job: str, job_dirs: list[Path] = None) -> "FioJobConfig":
        """Parse a job name into a complete configuration.

        Job names can have optional suffix separated by underscore:
            "libaio-ext4_bandwidth"  → config: "libaio-ext4", suffix: "bandwidth"
            "libaio-ext4"            → config: "libaio-ext4", suffix: None

        Examples:
            "spdk"                       → engine=spdk, category=raw
            "libaio-ext4"                → engine=libaio, category=plain, fs=ext4
            "libaio-luks-ext4-aes"       → engine=libaio, category=luks, fs=ext4, cipher=aes
            "libaio-dmverity-ext4"       → engine=libaio, category=dmverity, fs=ext4
            "libaio-ext4_bandwidth"      → engine=libaio, category=plain, fs=ext4, suffix=bandwidth
        """
        # Extract suffix if present (separated by underscore)
        suffix = None
        job_base = job
        if "_" in job:
            parts = job.split("_", 1)  # Split on first underscore only
            job_base = parts[0]  # Config part (e.g., "libaio-ext4")
            suffix = parts[1]  # Suffix part (e.g., "bandwidth")

        # Parse engine prefix using job_base (not original job)
        engine, storage_type = None, None
        for eng in KNOWN_ENGINES:
            if job_base == eng:
                engine, storage_type = eng, "raw"
                break
            if job_base.startswith(f"{eng}-"):
                engine, storage_type = eng, job_base[len(eng) + 1 :]
                break
        if engine is None:
            raise ValueError(f"Cannot determine engine from job name: {job}")

        # Parse storage category, filesystem, cipher
        filesystem, cipher, formatter_key = None, None, None
        if storage_type == "raw":
            storage_category = "raw"
        elif storage_type in ("ext4", "f2fs"):
            storage_category = "plain"
            filesystem = storage_type
            formatter_key = "plain"
        elif storage_type.startswith("luks-"):
            storage_category = "luks"
            filesystem = parse_job_filesystem(storage_type)
            cipher = storage_type.split("-", 2)[2]  # "aes", "aegis128", "aes-xts"
            formatter_key = f"luks-{cipher}"
        elif storage_type.startswith("dmverity-"):
            storage_category = "dmverity"
            filesystem = parse_job_filesystem(storage_type)
            formatter_key = "dmverity"
        elif storage_type.startswith("fsverity-"):
            storage_category = "fsverity"
            filesystem = parse_job_filesystem(storage_type)
            formatter_key = "fsverity"
        else:
            print(f"Assuming storage type is raw for type: {storage_type}")
            storage_category = "raw"

        return FioJobConfig(
            job=job,
            engine=engine,
            storage_type=storage_type,
            storage_category=storage_category,
            engine_args=ENGINE_CONFIGS[engine],
            job_file=resolve_fio_job_file(job, job_dirs),
            readonly=storage_category in ("dmverity", "fsverity"),
            needs_cleanup=storage_category != "raw",
            needs_spdk_setup=engine == "spdk",
            needs_precondition=storage_category in ("raw", "luks"),
            filesystem=filesystem,
            cipher=cipher,
            formatter_key=formatter_key,
            suffix=suffix,
        )


def _fio_path(name, action_config):
    job = action_config.get("job", "")
    bs = action_config.get("block_size")
    job_dir = f"{job}_bs{block_size_label(bs)}" if bs else job
    return ("fio", name, job_dir)


@register_action("fio", path_fn=_fio_path)
def run_fio(
    name: str,
    vm: QemuVm,
    job: str = "spdk",
    filename: str = "trtype=PCIe traddr=0000.00.06.0 ns=1",
    dev_path: str = None,
    device_size: str = "10G",
    timestamp: Optional[str] = None,
    block_size: int = None,
    force_mps: bool = False,
    pci_dev: str = None,
    skip_precondition: bool = False,
    precondition_scheme: str = None,
    job_dirs: list[str] = None,
):
    if not pci_dev:
        raise ValueError("pci_dev is required")

    effective_job_dirs = [Path(d) for d in job_dirs] if job_dirs else None
    cfg = FioJobConfig.from_job_name(job, effective_job_dirs)

    # Use provided timestamp, or generate new one if not provided
    if block_size is not None:
        block_size = int(block_size)
    job_dir = f"{job}_bs{block_size_label(block_size)}" if block_size else job
    outputdir_host, outputdir_guest, date = get_benchmark_output_path(
        "fio", name, job_dir, timestamp=timestamp
    )
    output = outputdir_guest / f"{date}.json"

    # Secure erase NVMe device before any runs (and optionally set block size)
    nvme_secure_erase(vm, dev_path, block_size=block_size)

    # Force Max Payload Size (MPS) if requested to 512B size
    if force_mps:
        print(f"Forcing MPS: setpci -s {pci_dev} 78.w=284f")
        vm.ssh_cmd(["setpci", "-s", pci_dev, "78.w=284f"], check=True, bypass=True)

    # Device setup — 2 cases via STORAGE_FORMATTERS dispatch
    if cfg.storage_category == "raw":
        if cfg.engine == "io_uring_cmd":
            if not dev_path:
                raise ValueError("dev_path is required for io_uring_cmd job")
            fio_target = get_nvme_char_device(vm, dev_path)
        else:
            fio_target = filename
    else:
        fio_target = STORAGE_FORMATTERS[cfg.formatter_key](
            vm, filename, cfg.filesystem, device_size
        )

    if cfg.needs_precondition and not skip_precondition:
        pre_con_path = fio_target
        if cfg.engine in ["spdk", "io_uring_cmd"]:
            # For SPDK and io_uring_cmd, we need to precondition the underlying block device, not the filename
            pre_con_path = dev_path

        run_precondition(
            vm,
            pre_con_path,
            output_dir=outputdir_guest,
            timestamp=date,
            scheme=precondition_scheme,
        )

    try:
        if cfg.needs_spdk_setup:
            print(f"Setting up SPDK for {pci_dev}")
            vm.ssh_cmd(["spdk-setup"], check=True, extra_env={"PCI_ALLOWED": pci_dev})
            # If using smaller sizes of hugepages the FIO test may fail due to allocating DMA memory over non continuous IOVA/PA space.
            # For some reason this breaks when running on the host, so only do this for VMs.
            setup_hugepages(vm)

        # Create logs subdirectory
        logs_dir_guest = outputdir_guest / f"{date}-logs"
        logs_dir_host = outputdir_host / f"{date}-logs"
        logs_dir_host.mkdir(parents=True, exist_ok=True)

        # Build FIO command with spdk-fio for ALL jobs
        cmd = [
            "spdk-fio",
            f"--filename={fio_target}",
        ]

        # Inject engine-specific parameters as CLI overrides
        for key, value in cfg.engine_args.items():
            cmd.append(f"--{key}={value}")

        if cfg.readonly:
            cmd.append("--readonly")

        # Add logging options - use --bandwidth-log with CWD set to logs dir
        cmd.extend(
            [
                "--bandwidth-log",
                f"--output={output}",
                "--output-format=json",
                cfg.job_file,
            ]
        )

        readonly_str = " (read-only)" if cfg.readonly else ""
        print(f"Starting FIO{readonly_str} on {fio_target}")
        vm.ssh_cmd(cmd, cwd=str(logs_dir_guest))
    finally:
        print("Cleaning up after FIO run...")
        if cfg.needs_spdk_setup:
            print(f"Resetting SPDK for {pci_dev}")
            vm.ssh_cmd(
                ["spdk-setup", "reset"], check=True, extra_env={"PCI_ALLOWED": pci_dev}
            )
        if cfg.needs_cleanup:
            cleanup_encrypted_device(vm, cfg.storage_type, filename)
        # Reset block size back to 512B if a different size was used
        if block_size is not None and block_size != 512:
            try:
                print("Resetting NVMe block size back to 512B...")
                nvme_secure_erase(vm, dev_path, block_size=512)
            except Exception as e:
                print(f"Warning: Failed to reset block size: {e}")
