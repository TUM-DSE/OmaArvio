#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import shlex
import socket
from pathlib import Path
from typing import Any, List, Optional

from invoke import task

from core.tasks.actions.runner import (
    configure_vfio_trace,
    run_benchmark_action,
)
from core.tasks.actions.registry import get_action
from core.tasks.config import SSH_PORT
from core.tasks.qemu import spawn_runner
from core.tasks.qemu_options import (
    get_amd_qemu_cmd_general,
    qemu_option_nvme,
    qemu_option_virtio_blk,
    qemu_option_virtio_nic,
)
from core.tasks.resources import get_vm_resource
from core.tasks.utils.vfio import bind_device_to_vfio, unbind_device_from_vfio


def start_and_attach(
    qemu_cmd: Optional[List[str]] = None, pin: bool = True, **kwargs: Any
) -> None:
    with spawn_runner(qemu_cmd, config=kwargs["config"], pin=pin) as runner:
        runner.attach()


def ssh_cmd(
    qemu_cmd: Optional[List[str]] = None, pin: bool = True, **kwargs: Any
) -> None:
    cmds: list[str] = kwargs["config"]["ssh_cmd"]
    with spawn_runner(qemu_cmd, config=kwargs["config"], pin=pin) as runner:
        runner.wait_for_ssh()
        for cmd in cmds:
            runner.ssh_cmd(shlex.split(cmd))


def do_action(action: str, **kwargs: Any) -> None:
    if action == "attach":
        start_and_attach(**kwargs)
        return

    if action == "ssh-cmd":
        ssh_cmd(**kwargs)
        return

    # Check if action exists and then run it
    get_action(action)
    run_benchmark_action(action_type=action, **kwargs)


@task
def start(
    ctx: Any,
    type: str = "amd",
    size: str = "medium",
    hostname: str = None,
    direct: bool = False,
    action: str = "attach",
    ssh_port: int = SSH_PORT,
    guest_cid: int = 11,
    pin: bool = True,
    pin_base: Optional[int] = None,
    extra_cmdline: str = "",
    extra_qemu_cmd: str = "",
    ssh_cmd: Optional[List[str]] = None,
    boot_trace: bool = True,
    boot_prealloc: bool = True,
    repeat: int = 1,
    virtio_iommu: bool = False,
    virtio_nic: bool = False,
    virtio_nic_vhost: bool = False,
    virtio_nic_mq: bool = False,
    virtio_nic_tap: str = "tap_cvm",
    virtio_nic_mtap: str = "mtap_cvm",
    virtio_blk: Optional[str] = None,
    virtio_blk_aio: str = "native",
    virtio_blk_direct: bool = True,
    virtio_blk_iothread: bool = True,
    nvme: bool = False,
    nvme_size: str = "500G",
    nvme_bps_rd: str = "10G",
    nvme_bps_wr: str = "2.5G",
    tls: bool = False,
    warn: bool = True,
    name_extra: str = "",
    vfio_pcie: Optional[List[str]] = None,
    edu: bool = False,
    vfio_trace: bool = False,
    vfio_trace_file: Optional[str] = None,
    attestation: bool = False,
    sar_enabled: bool = True,
    sar_interval: int = 1,
    sar_options: str = "-u -r -n DEV",
    kvm_perf_enabled: bool = True,
    action_config: Optional[dict] = None,
) -> None:
    if hostname is None:
        hostname = socket.gethostname()
    if ssh_cmd is None:
        ssh_cmd = []
    if vfio_pcie is None:
        vfio_pcie = []

    config: dict = locals()
    resource = get_vm_resource(hostname, size)
    config["resource"] = resource
    config["vfio_devices"] = vfio_pcie

    if vfio_trace and not vfio_pcie:
        raise ValueError("VFIO tracing requires at least one VFIO device (--vfio-pcie)")

    supported_types = ["amd", "snp", "host"]
    if type not in supported_types:
        raise ValueError(f"Type needs to be one of: {supported_types}")

    if attestation:
        if type != "snp":
            raise ValueError("Attestation requires SNP mode (--type snp)")
        if vfio_pcie:
            raise ValueError(
                "VFIO device passthrough is not supported with attestation mode. "
                "Please use either --attestation OR --vfio-pcie, not both."
            )

    if config["pin_base"] is None:
        config.pop("pin_base", None)

    if type == "host":
        name = f"host-{size}" + name_extra
        print(f"Starting host runner: {name}")
        do_action(action, pin=pin, name=name, config=config)
        return

    qemu_name = f"{type}-direct" if direct else type
    name = f"{type}-{'direct' if direct else 'disk'}-{size}" + name_extra
    configure_vfio_trace(config, name, action)

    qemu_cmd = get_amd_qemu_cmd_general(
        resource,
        config,
        qemu_name,
        direct,
        type == "snp",
        edu,
        config.get("vfio_trace", False),
        Path(config["vfio_trace_file"]) if config.get("vfio_trace_file") else None,
    )

    if virtio_nic:
        qemu_cmd += qemu_option_virtio_nic(
            tap=virtio_nic_tap,
            mtap=virtio_nic_mtap,
            vhost=virtio_nic_vhost,
            mq=virtio_nic_mq,
            config=config,
        )

    if virtio_blk:
        virtio_blk_path = Path(virtio_blk)
        print(f"Use virtio-blk: {virtio_blk_path}")
        if virtio_blk_path.is_block_device() and warn:
            print(
                f"WARN: use {virtio_blk_path} as a virtio-blk. "
                "This overrides the existing disk image. Ok? [y/N]"
            )
            if input() != "y":
                return
        elif not virtio_blk_path.is_file():
            print(f"{virtio_blk_path} is not a file nor a block device")
            return
        qemu_cmd += qemu_option_virtio_blk(
            virtio_blk_path,
            virtio_blk_aio,
            virtio_blk_direct,
            virtio_blk_iothread,
            virtio_iommu,
        )

    nvme_backing_file = None
    if nvme:
        print(
            f"Use emulated NVMe: size={nvme_size}, "
            f"bps_rd={nvme_bps_rd}, bps_wr={nvme_bps_wr}"
        )
        nvme_opts, nvme_backing_file = qemu_option_nvme(
            size=nvme_size, bps_rd=nvme_bps_rd, bps_wr=nvme_bps_wr
        )
        qemu_cmd += nvme_opts

    if extra_qemu_cmd:
        qemu_cmd += shlex.split(extra_qemu_cmd)

    vfio_original_drivers = {}
    for device in vfio_pcie:
        original_driver = bind_device_to_vfio(device)
        if original_driver:
            vfio_original_drivers[device] = original_driver

    print(f"Starting VM: {name}")
    try:
        do_action(action, qemu_cmd=qemu_cmd, pin=pin, name=name, config=config)
    finally:
        if nvme_backing_file:
            try:
                os.unlink(nvme_backing_file)
                print(f"Cleaned up NVMe backing file: {nvme_backing_file}")
            except OSError as exc:
                print(f"Warning: Failed to clean up NVMe backing file: {exc}")

        for device in reversed(vfio_pcie):
            if device in vfio_original_drivers:
                try:
                    unbind_device_from_vfio(device, vfio_original_drivers[device])
                except Exception as exc:
                    print(f"Warning: Failed to restore {device}: {exc}")
