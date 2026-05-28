#!/usr/bin/env python3
"""Thin helpers to retrieve per-host device addresses from config.toml.

These replace the direct dict lookups on DEVICE_CONFIG that were scattered
across benchmarks.py, gds.py, etc.  They are intentionally thin wrappers —
all the data lives in config.toml, all the parsing is in load_config().
"""

from typing import Optional

from core.tasks.config import load_config


def get_nvme_pci(hostname: str) -> str:
    """Return the NVMe PCI address for *hostname* from config.toml."""
    return load_config().hosts[hostname].nvme_pci


def get_dev_path(hostname: str) -> str:
    """Return the NVMe block-device path for *hostname* from config.toml."""
    return load_config().hosts[hostname].dev_path


def get_gpu_pci(hostname: str) -> Optional[str]:
    """Return the GPU PCI address for *hostname*, or None if not present."""
    return load_config().hosts[hostname].gpu_pci


def get_valid_pcie_speeds(hostname: str):
    """Return the list of valid PCIe speeds for *hostname*."""
    return load_config().hosts[hostname].valid_pcie_speeds
