#!/usr/bin/env python3
import json
import socket
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from humanfriendly import parse_size
from invoke import task

import core.tasks.config as config
from core.tasks.config import PROJECT_ROOT


def _config_to_plain(value):
    if is_dataclass(value):
        return _config_to_plain(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _config_to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_config_to_plain(item) for item in value]
    return value


def _print_config_section(title: str, value) -> None:
    print(title)
    print(json.dumps(_config_to_plain(value), indent=2))


def parse_size_to_bytes(size: str | int | float) -> int:
    """Parse sizes like 512K, 10G, or 2.5G using binary units."""
    return int(parse_size(str(size), binary=True))


def parse_size_to_mb(size: str | int | float) -> int:
    """Parse a size and return whole MiB."""
    return parse_size_to_bytes(size) // (1024 * 1024)


def get_benchmark_output_path(
    *path_components, timestamp: Optional[str] = None, create_dirs: bool = True
) -> tuple[Path, Path, str]:
    """Generate standardized benchmark output paths.

    Creates paths following: bench-result/{component1}/{component2}/.../

    This function ensures that both vm.py and action functions generate identical
    paths, which is critical for VFIO trace file placement. When vfio_trace is
    enabled, vm.py uses the outputdir_host returned by this function to place
    trace files in the same directory as benchmark outputs.

    Args:
        *path_components: Variable path components (e.g., 'fio', name, job)
        timestamp: Optional timestamp string (default: auto-generate)
                  IMPORTANT: Pass the same timestamp to both vm.py and action
                  calls to ensure paths match exactly
        create_dirs: Whether to create directories on host (default: True)

    Returns:
        tuple: (outputdir_host, outputdir_guest, timestamp)

    Example:
        # FIO
        host, guest, ts = get_benchmark_output_path("fio", name, job_dir, timestamp=ts)

        # OpenSSL
        host, guest, ts = get_benchmark_output_path("openssl", name, timestamp=ts)
    """
    date = timestamp if timestamp else datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    # Build path from components
    path_str = "/".join(str(c) for c in path_components)
    outputdir = Path(f"./bench-result/{path_str}/")

    outputdir_host = PROJECT_ROOT / outputdir
    if create_dirs:
        outputdir_host.mkdir(parents=True, exist_ok=True)

    outputdir_guest = Path("/share") / outputdir

    return outputdir_host, outputdir_guest, date


@task
def show_config(ctx):
    """Show script configuration"""
    cfg = config.load_config()
    hostname = socket.gethostname()
    host_config = cfg.hosts.get(hostname)

    general_config = {
        "project_root": config.PROJECT_ROOT,
        "build_dir": config.BUILD_DIR,
        "linux_dir": config.LINUX_DIR,
        "current_hostname": hostname,
        "ssh_port": cfg.ssh_port,
        "vm_ip": cfg.vm_ip,
        "valid_pcie_speeds": cfg.valid_pcie_speeds,
        "qemu_devices": {
            "nvme_pci": cfg.qemu_nvme_pci,
            "nvme_dev_path": cfg.qemu_nvme_dev_path,
        },
        "vm_device_addresses": cfg.vm_device_addresses,
        "output_root": cfg.output_root,
        "default_vm_resources": cfg.default_vm_resources,
    }

    _print_config_section("General config", general_config)

    if host_config is None:
        known_hosts = ", ".join(sorted(cfg.hosts)) or "(none)"
        print(f"\nHost config for {hostname!r}: not found")
        print(f"Known hosts: {known_hosts}")
        return

    print()
    _print_config_section(f"Host config for {hostname!r}", host_config)
