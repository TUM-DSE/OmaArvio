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
from core.tasks.config import SSH_PORT, PROJECT_ROOT
from core.tasks.qemu import spawn_runner
from core.tasks.qemu_builder import (
    get_vm_config,
    QemuVmBuilder,
    QemuFeature,
    CpuMemoryFeature,
    AmdMachineFeature,
    BootFeature,
    UserNetFeature,
    ConsoleFeature,
    SharedFolderFeature,
    VirtioBlkFeature,
    VirtioNicFeature,
    NvmeEmulationFeature,
    VfioGroupFeature,
    EduFeature,
    ExtraCmdFeature,
)
from core.tasks.resources import get_vm_resource


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

    vmconfig = get_vm_config(qemu_name, attestation=attestation)

    builder = QemuVmBuilder(vmconfig.qemu, resource)

    builder.add_feature(CpuMemoryFeature(resource, prealloc=boot_prealloc))
    builder.add_feature(
        AmdMachineFeature(
            confidential=(type == "snp"), hostname=hostname, attestation=attestation
        )
    )

    builder.add_feature(
        BootFeature(vmconfig=vmconfig, direct=direct, extra_cmdline=extra_cmdline)
    )

    builder.add_feature(UserNetFeature(ssh_port=ssh_port))
    builder.add_feature(ConsoleFeature())

    # Shared folders
    builder.add_feature(SharedFolderFeature(str(PROJECT_ROOT), "share"))
    module_shared_data = os.environ.get("MODULE_SHARED_DATA", "")
    if module_shared_data:
        builder.add_feature(SharedFolderFeature(module_shared_data, "shared"))

    if virtio_nic:
        builder.add_feature(
            VirtioNicFeature(
                tap=virtio_nic_tap,
                mtap=virtio_nic_mtap,
                vhost=virtio_nic_vhost,
                mq=virtio_nic_mq,
                cpu_count=resource.cpu,
                iommu_option=virtio_iommu,
            )
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
        builder.add_feature(
            VirtioBlkFeature(
                file_path=virtio_blk_path,
                aio=virtio_blk_aio,
                direct=virtio_blk_direct,
                iothread=virtio_blk_iothread,
                iommu_option=virtio_iommu,
            )
        )

    if nvme:
        builder.add_feature(
            NvmeEmulationFeature(
                size=nvme_size,
                bps_rd=nvme_bps_rd,
                bps_wr=nvme_bps_wr,
            )
        )

    if vfio_pcie:
        trace_file = (
            Path(config["vfio_trace_file"]) if config.get("vfio_trace_file") else None
        )
        builder.add_feature(
            VfioGroupFeature(
                pci_ids=vfio_pcie,
                trace_file=trace_file,
            )
        )

    if edu:
        builder.add_feature(EduFeature())

    if extra_qemu_cmd:
        builder.add_feature(ExtraCmdFeature(extra_qemu_cmd))

    print(f"Starting VM: {name}")
    builder.setup()
    try:
        do_action(
            action, qemu_cmd=builder.build_command(), pin=pin, name=name, config=config
        )
    finally:
        builder.teardown()
