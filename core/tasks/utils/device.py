#!/usr/bin/env python3
"""Typed device configuration backed only by config.toml."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional

from core.tasks.config import load_config


@dataclass(frozen=True)
class StorageTarget:
    filename: str
    dev_path: str
    pci_dev: str
    vfio_device: Optional[str]
    valid_pcie_speeds: Optional[list[int]]


@dataclass(init=False)
class Devices:
    hostname: str
    nvme_pci: str
    dev_path: str
    valid_pcie_speeds: list[int]
    qemu_nvme_pci: str
    qemu_nvme_dev_path: str
    vm_device_addresses: dict[str, str]
    gpu_pci: Optional[str] = None

    def __init__(self, hostname: Optional[str] = None):
        cfg = load_config()
        actual_hostname = hostname or socket.gethostname()
        try:
            host = cfg.hosts[actual_hostname]
        except KeyError as exc:
            known_hosts = ", ".join(sorted(cfg.hosts))
            raise ValueError(
                f"No host config found for '{actual_hostname}'. Known hosts: {known_hosts}"
            ) from exc

        self.hostname = actual_hostname
        self.nvme_pci = self.full_bdf(host.nvme_pci)
        self.dev_path = host.dev_path
        self.valid_pcie_speeds = host.valid_pcie_speeds
        self.gpu_pci = self.full_bdf(host.gpu_pci) if host.gpu_pci else None
        self.qemu_nvme_pci = self.full_bdf(cfg.qemu_nvme_pci)
        self.qemu_nvme_dev_path = cfg.qemu_nvme_dev_path
        self.vm_device_addresses = {
            setup: self.full_bdf(address)
            for setup, address in cfg.vm_device_addresses.items()
        }

    @staticmethod
    def full_bdf(address: str) -> str:
        if address.count(":") == 1:
            return f"0000:{address}"
        return address

    @staticmethod
    def short_bdf(address: str) -> str:
        return ":".join(Devices.full_bdf(address).split(":")[1:])

    @staticmethod
    def spdk_bdf(address: str) -> str:
        return Devices.full_bdf(address).replace(":", ".")

    @property
    def nvme_short(self) -> str:
        return self.short_bdf(self.nvme_pci)

    @property
    def gpu_short(self) -> Optional[str]:
        return self.short_bdf(self.gpu_pci) if self.gpu_pci else None

    @property
    def qemu_nvme_short(self) -> str:
        return self.short_bdf(self.qemu_nvme_pci)

    def vm_pci(self, setup: str) -> str:
        try:
            return self.vm_device_addresses[setup]
        except KeyError as exc:
            known_setups = ", ".join(sorted(self.vm_device_addresses))
            raise ValueError(
                f"No VM device address configured for setup '{setup}'. "
                f"Known setups: {known_setups}"
            ) from exc

    def storage_target(
        self,
        setup: str,
        *,
        qemu_nvme: bool = False,
        spdk: bool = False,
    ) -> StorageTarget:
        if qemu_nvme:
            if setup == "host":
                raise ValueError("QEMU NVMe is not available for host setup")
            filename = (
                f"trtype=PCIe traddr={self.spdk_bdf(self.qemu_nvme_pci)} ns=1"
                if spdk
                else self.qemu_nvme_dev_path
            )
            return StorageTarget(
                filename=filename,
                dev_path=self.qemu_nvme_dev_path,
                pci_dev=self.qemu_nvme_pci,
                vfio_device=None,
                valid_pcie_speeds=None,
            )

        if setup == "host":
            pci = self.nvme_pci
            vfio_device = None
        else:
            pci = self.vm_pci(setup)
            vfio_device = self.nvme_short

        filename = (
            f"trtype=PCIe traddr={self.spdk_bdf(pci)} ns=1" if spdk else self.dev_path
        )
        return StorageTarget(
            filename=filename,
            dev_path=self.dev_path,
            pci_dev=pci,
            vfio_device=vfio_device,
            valid_pcie_speeds=self.valid_pcie_speeds,
        )
