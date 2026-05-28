#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


class PreconditionScheme(str, Enum):
    """SSD preconditioning schemes."""

    SD_CUSTOM = "sd_custom"  # LFSR-based sprandom + 10-minute random write
    SNIA = "snia"  # 2x sequential 128K writes across full LBA range
    NULL = "null"  # Skip preconditioning entirely


DEFAULT_PRECONDITION_SCHEME = PreconditionScheme.SNIA


def _find_project_root() -> Path:
    """Find repo root by locating config.toml walking up from cwd.

    When *core* is installed as a Nix package, ``__file__`` resolves into the
    read-only ``/nix/store``.  Walking up from the current working directory
    instead ensures we always land in the actual repo checkout.

    Falls back to a ``__file__``-relative path for backwards-compatibility
    when *core* is used as an editable install from inside the repo.
    """
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "config.toml").exists():
            return candidate
    # Fallback: 3 levels up from this file (core/tasks/config.py → repo root)
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT: Path = _find_project_root()
LINUX_DIR: Path = PROJECT_ROOT.parent / "linux"
SSH_PORT: int = 2225
VM_IP = "172.44.0.2"


# Default allowed PCIe speeds (numeric: 4=16GT/s, 5=32GT/s)
DEFAULT_VALID_PCIE_SPEEDS = [4, 5]

# NVMe device configuration for the different hosts
# Add valid_speeds per host if needed, else fallback to DEFAULT_VALID_PCIE_SPEEDS
DEVICE_CONFIG = {
    "vislor": {
        "nvme_pci": "0000:43:00.0",
        "dev_path": "/dev/disk/by-id/nvme-KIOXIA_KCMYXRUG3T84_8F30A0240LM3_1",
        "valid_speeds": [4, 5],  # can override per host
        "gpu_pci": "0000:01:00.0",  # NVIDIA GPU
    },
    "jamie": {
        "nvme_pci": "0000:e1:00.0",
        "dev_path": "/dev/disk/by-id/nvme-KIOXIA_KCMYXRUG3T84_8F30A0240LM3_1",
        "valid_speeds": [5],  # This is in a Gen5 slot, so only allow 32GT/s
        "gpu_pci": "0000:21:00.0",  # NVIDIA GPU
    },
    "irene": {
        "nvme_pci": "0000:c3:00.0",
        "dev_path": "/dev/disk/by-id/nvme-KIOXIA_KCMYXRUG3T84_8F30A0220LM3_1",
        "valid_speeds": [4, 5],
        "gpu_pci": None,  # Not configured yet
    },
    "polly": {
        "nvme_pci": "0000:91:00.0",
        "dev_path": "/dev/disk/by-id/nvme-KIOXIA_KCMYXRUG3T84_4FB0A0CT0LM3_1",
        "valid_speeds": [4, 5],
        "gpu_pci": "0000:01:00.0",  # RTX PRO 6000 Blackwell GPU
    },
}

# VM virtual device addresses (same across all hosts)
VM_DEVICE_ADDRESSES = {
    "snp": "0000:01:00.0",
    "amd": "0000:01:00.0",
}

# QEMU emulated NVMe device (available when --nvme is passed to vm.start)
QEMU_NVME_PCI = "0000:00:06.0"
QEMU_NVME_DEV_PATH = "/dev/disk/by-id/nvme-QEMU_NVMe_Ctrl_deadbeef_1"


# ---------------------------------------------------------------------------
# TOML-based config (new API — load_config() / HostConfig)
# The legacy DEVICE_CONFIG dict above is kept for backward compatibility.
# ---------------------------------------------------------------------------


@dataclass
class VMResourceConfig:
    cpu: int
    memory: int
    numa_node: List[int]
    pin_base: int


@dataclass
class HostConfig:
    nvme_pci: str
    dev_path: str
    valid_pcie_speeds: List[int]
    gpu_pci: Optional[str] = None
    vm_resources: Dict[str, VMResourceConfig] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    hosts: Dict[str, HostConfig]
    default_vm_resources: Dict[str, VMResourceConfig]
    ssh_port: int
    vm_ip: str
    valid_pcie_speeds: List[int]
    output_root: Optional[Path] = None  # overrides default PROJECT_ROOT/build location


def _parse_vm_resources(raw: dict) -> Dict[str, VMResourceConfig]:
    result = {}
    for name, v in raw.items():
        result[name] = VMResourceConfig(
            cpu=v["cpu"],
            memory=v["memory"],
            numa_node=v["numa_node"],
            pin_base=v["pin_base"],
        )
    return result


@lru_cache(maxsize=1)
def load_config(config_path: Optional[str] = None) -> ProjectConfig:
    """Load config.toml from the repo root (LRU-cached after first call).

    Args:
        config_path: Optional explicit path; defaults to PROJECT_ROOT/config.toml.
    """
    path = Path(config_path) if config_path else PROJECT_ROOT / "config.toml"
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    defaults = raw.get("defaults", {})
    default_vm_res = _parse_vm_resources(defaults.get("vm_resources", {}))

    hosts: Dict[str, HostConfig] = {}
    for hostname, hraw in raw.get("hosts", {}).items():
        host_vm_res = _parse_vm_resources(hraw.get("vm_resources", {}))
        # Fall back to defaults for any sizes not overridden
        merged_vm_res = {**default_vm_res, **host_vm_res}
        hosts[hostname] = HostConfig(
            nvme_pci=hraw["nvme_pci"],
            dev_path=hraw["dev_path"],
            valid_pcie_speeds=hraw.get(
                "valid_pcie_speeds", defaults.get("valid_pcie_speeds", [4, 5])
            ),
            gpu_pci=hraw.get("gpu_pci"),
            vm_resources=merged_vm_res,
        )

    output_root_raw = defaults.get("output_root")
    output_root = (
        Path(output_root_raw).expanduser().resolve() if output_root_raw else None
    )

    return ProjectConfig(
        hosts=hosts,
        default_vm_resources=default_vm_res,
        ssh_port=defaults.get("ssh_port", SSH_PORT),
        vm_ip=defaults.get("vm_ip", VM_IP),
        valid_pcie_speeds=defaults.get("valid_pcie_speeds", DEFAULT_VALID_PCIE_SPEEDS),
        output_root=output_root,
    )


def _compute_build_dir() -> Path:
    """Return the build output directory.

    Uses ``output_root`` from ``config.toml`` if set; otherwise falls back to
    ``PROJECT_ROOT / "build"``.  Errors loading the config are silently ignored
    so imports never fail in environments without a config.toml.
    """
    try:
        cfg = load_config()
        if cfg.output_root is not None:
            return cfg.output_root
    except Exception:
        pass
    return PROJECT_ROOT / "build"


BUILD_DIR: Path = _compute_build_dir()
