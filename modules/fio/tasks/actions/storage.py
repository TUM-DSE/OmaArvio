#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.tasks.qemu import QemuVm, HostRunner, setup_hugepages
from core.tasks.actions import ActionContext
from modules.nvme.tasks.utils.storage import (
    cleanup_encrypted_device,
    format_dmverity_device,
    format_fsverity_device,
    format_luks_aegis128_device,
    format_luks_aes_device,
    format_luks_aes_xts_device,
    format_plain_device,
    parse_job_filesystem,
)

# Import NVMe utilities from the nvme module
from modules.nvme.tasks.utils.nvme import (
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


# --- FIO Job Configuration ---

# Engine-specific FIO parameters, injected as CLI args to override [global] in .fio files.
# The .fio files contain only workload job definitions (no engine params).
ENGINE_CONFIGS = {
    "libaio": {"ioengine": "libaio", "direct": "1", "thread": "1"},
    "spdk": {"ioengine": "spdk", "direct": "1", "thread": "1"},
    "io_uring_cmd": {"ioengine": "io_uring_cmd", "cmd_type": "nvme"},
    "libcufilep2p": {
        "ioengine": "libcufile",
        "cuda_io": "cufile",
        "direct": "1",
        "thread": "1",
    },
    "libcufileposix": {
        "ioengine": "libcufile",
        "cuda_io": "posix",
        "direct": "1",
        "thread": "1",
    },
}

# Order matters: longer/more specific engine prefixes must be checked first.
KNOWN_ENGINES = ["io_uring_cmd", "libaio", "spdk", "libcufilep2p", "libcufileposix"]

CUDA_ENGINES = {"libcufilep2p", "libcufileposix"}

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


def mount_options_for_config(cfg: "FioJobConfig") -> list[str] | None:
    if cfg.engine == "libcufilep2p" and cfg.filesystem == "ext4":
        return ["data=ordered"]
    return None


@dataclass
class FioJobConfig:
    """Parsed FIO job configuration — single source of truth for all job parameters.

    Job names encode the engine prefix: "spdk", "libaio-ext4", "libcufilep2p-ext4",
    "libcufileposix-ext4", "libaio-luks-ext4-aes", etc.
    Use FioJobConfig.from_job_name() to parse a job name into all derived fields.
    """

    job: str  # Full job name (e.g., "libaio-ext4")
    engine: str  # "libaio", "spdk", "io_uring_cmd", "libcufilep2p", "libcufileposix"
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
            "libcufilep2p-ext4"          → engine=libcufilep2p, category=plain, fs=ext4
            "libcufileposix-ext4_bandwidth" → engine=libcufileposix, category=plain, fs=ext4, suffix=bandwidth
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
    ctx: ActionContext,
    job: str = "spdk",
    filename: str = "trtype=PCIe traddr=0000.00.06.0 ns=1",
    dev_path: str = None,
    device_size: str = "10G",
    block_size: int = None,
    force_mps: bool = False,
    pci_dev: str = None,
    skip_precondition: bool = False,
    precondition_scheme: str = None,
    job_dirs: list[str] = None,
):
    if not pci_dev:
        raise ValueError("pci_dev is required")

    vm = ctx.vm
    effective_job_dirs = [Path(d) for d in job_dirs] if job_dirs else None
    cfg = FioJobConfig.from_job_name(job, effective_job_dirs)

    if block_size is not None:
        block_size = int(block_size)
    outputdir_host = ctx.outputdir_host
    outputdir_guest = ctx.outputdir_guest
    date = ctx.timestamp
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
        mount_options = mount_options_for_config(cfg)
        formatter_kwargs = {}
        if cfg.formatter_key == "plain" and mount_options:
            formatter_kwargs["mount_options"] = mount_options
        fio_target = STORAGE_FORMATTERS[cfg.formatter_key](
            vm, filename, cfg.filesystem, device_size, **formatter_kwargs
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

        fio_binary = "fio-cuda" if cfg.engine in CUDA_ENGINES else "spdk-fio"
        cmd = [
            fio_binary,
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

        extra_env = {}
        if cfg.engine in CUDA_ENGINES:
            extra_env = {
                "CUFILE_USE_PCIP2PDMA": "true",
                "CUFILE_ALLOW_COMPAT_MODE": "false",
            }

        readonly_str = " (read-only)" if cfg.readonly else ""
        print(f"Starting FIO{readonly_str} on {fio_target}")
        vm.ssh_cmd(cmd, cwd=str(logs_dir_guest), extra_env=extra_env)
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
