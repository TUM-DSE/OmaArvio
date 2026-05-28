#!/usr/bin/env python3
"""QEMU command-line construction helpers."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from core.tasks.config import BUILD_DIR, LINUX_DIR, PROJECT_ROOT
from core.tasks.resources import VMResource
from core.tasks.utils.utils import parse_size_to_bytes
from core.tasks.utils.vfio import get_pci_ids


@dataclass
class VMConfig:
    qemu: Path
    image: Path
    ovmf: Path
    kernel: Optional[Path]
    initrd: Optional[Path]
    cmdline: Optional[str]


def ensure_snp_certificates(hostname: str) -> Path:
    """Fetch and bundle SEV-SNP certificates for *hostname* if not cached."""
    certs_base = BUILD_DIR / "sev-snp" / "certs" / hostname
    cert_bundle = certs_base / "cert_bundle"

    if cert_bundle.exists():
        print(f"SEV-SNP certificates cached at: {cert_bundle}")
        return cert_bundle

    print(f"Fetching SEV-SNP certificates for {hostname}...")
    certs_base.mkdir(parents=True, exist_ok=True)

    print("  Fetching CA certificates from AMD KDS...")
    subprocess.run(
        ["snphost", "fetch", "ca", "pem", str(certs_base)],
        check=True,
        capture_output=True,
        text=True,
    )

    print("  Fetching VCEK certificate...")
    subprocess.run(
        ["snphost", "fetch", "vek", "pem", str(certs_base)],
        check=True,
        capture_output=True,
        text=True,
    )

    print("  Creating certificate bundle...")
    subprocess.run(
        ["snphost", "import", str(certs_base), str(cert_bundle)],
        check=True,
        capture_output=True,
        text=True,
    )

    print(f"Certificate bundle created: {cert_bundle}")
    return cert_bundle


def get_vm_config(name: str, attestation: bool = False) -> VMConfig:
    if name.startswith(("snp", "amd")):
        image = BUILD_DIR / "image/snp-guest-image.raw"
        ovmf = BUILD_DIR / "ovmf-upstream-fd/FV/OVMF.fd"
        qemu = (
            BUILD_DIR / "qemu-amd/bin/qemu-system-x86_64"
            if attestation and "snp" in name
            else BUILD_DIR / "qemu-upstream/bin/qemu-system-x86_64"
        )

        kernel = None
        initrd = None
        cmdline = None

        if "direct" in name:
            image = BUILD_DIR / "image/direct-guest-image.raw"
            kernel = LINUX_DIR / "build/arch/x86/boot/bzImage"
            cmdline = "root=/dev/vda1 console=hvc0 iommu=off intel_iommu=off amd_iommu=off hugepagesz=2M hugepages=4096 loglevel=4 lsm=landlock,yama,bpf vfio.enable_unsafe_noiommu_mode=1"

        return VMConfig(qemu, image, ovmf, kernel, initrd, cmdline)

    raise ValueError(f"Unknown VM image: {name}")


def get_amd_qemu_cmd_general(
    resource: VMResource,
    config: dict,
    vmconfig_name: str,
    direct: bool,
    confidential: bool,
    edu: bool,
    vfio_trace: bool,
    vfio_trace_file: Optional[Path] = None,
) -> List[str]:
    attestation = config.get("attestation", False)
    vmconfig = get_vm_config(vmconfig_name, attestation=attestation)
    ssh_port = config["ssh_port"]
    vfio_devices = config.get("vfio_devices", [])

    cert_bundle_path = None
    if attestation:
        hostname = config.get("hostname", socket.gethostname())
        cert_bundle_path = ensure_snp_certificates(hostname)

    if vfio_trace:
        if vfio_trace_file is None:
            vfio_trace_file = Path(
                f"./vfio_trace_{vmconfig_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}.log"
            )
        if not vfio_devices:
            raise ValueError(
                "VFIO tracing requires at least one VFIO device (--vfio-pcie)"
            )

    prealloc = "on" if config["boot_prealloc"] else "off"

    kernel_config = ""
    bootindex_config = ",bootindex=0"
    if direct:
        extra_cmdline = config.get("extra_cmdline", "")
        kernel_config = f"""
        -kernel {vmconfig.kernel}
        -append '{vmconfig.cmdline}{extra_cmdline}'
        """
        bootindex_config = ""

    vfio_config = ""
    if vfio_devices:
        vfio_config = """
        -object iommufd,id=iommufd0
        """
        if vfio_trace:
            vfio_config += f"""
            -trace enable=vfio_region_write,enable=vfio_region_read,file={vfio_trace_file}
            """
        tracing = ",x-no-mmap=true" if vfio_trace else ""
        for idx, device in enumerate(vfio_devices):
            vendor_id, device_id = get_pci_ids(device)
            vfio_config += f"""
        -device pcie-root-port,id=rp{idx},bus=pcie.0,chassis=0,slot={idx},multifunction=off,pref64-reserve=274877906944B,mem-reserve=4194304B
        -device vfio-pci,host=0000:{device},x-pci-vendor-id={vendor_id},x-pci-device-id={device_id},bus=rp{idx},iommufd=iommufd0{tracing}
        """

    machine_config = f"""
        -machine q35,memory-backend=ram1,vmport=off,kernel_irqchip=split
        -object memory-backend-memfd,id=ram1,size={resource.memory}G,share=true,prealloc={prealloc}
        """
    if confidential:
        if not os.access("/dev/sev", os.R_OK):
            raise PermissionError("Cannot access /dev/sev. Likely need to run as root!")

        sev_guest_config = "id=sev0,cbitpos=51,reduced-phys-bits=1,policy=0x30000"
        if attestation and cert_bundle_path:
            sev_guest_config += f",certs-filename={cert_bundle_path}"

        machine_config = f"""
        -machine q35,memory-backend=ram1,memory-encryption=sev0,vmport=off,kernel_irqchip=split
        -object sev-snp-guest,{sev_guest_config}
        -object memory-backend-memfd,id=ram1,size={resource.memory}G,share=true,prealloc={prealloc}
        """

    module_shared_data = os.environ.get("MODULE_SHARED_DATA", "")
    shared_virtfs = (
        f"-virtfs local,path={module_shared_data},security_model=none,mount_tag=shared"
        if module_shared_data
        else ""
    )

    qemu_cmd = f"""
    {vmconfig.qemu}
    -enable-kvm
    -cpu EPYC-Genoa-v1,host-phys-bits=true
    -smp {resource.cpu}
    -m {resource.memory}G

    {machine_config}
    {kernel_config}

    -blockdev raw,node-name=q2,file.driver=file,file.filename={vmconfig.image}
    -device virtio-blk-pci,drive=q2{bootindex_config}
    -device virtio-net-pci,netdev=net0
    -netdev user,id=net0,hostfwd=tcp::{ssh_port}-:22,hostfwd=tcp::8895-:8895
    -virtfs local,path={PROJECT_ROOT},security_model=none,mount_tag=share
    {shared_virtfs}
    -bios {vmconfig.ovmf}
    {vfio_config}

    -nographic
    -serial null
    -device virtio-serial
    -chardev stdio,mux=on,id=char0,signal=off
    -mon chardev=char0,mode=readline
    -device virtconsole,chardev=char0,id=vc0,nr=0
    """

    if edu:
        qemu_cmd += " -device edu,dma_mask=0xffffffffffffffff"

    return shlex.split(qemu_cmd)


def qemu_option_virtio_blk(
    file: Path,
    aio: str = "native",
    direct: bool = True,
    iothread: bool = True,
    iommu_option: bool = False,
) -> List[str]:
    driver = "host_device" if file.is_block_device() else "file"
    cache_direct = "on" if direct else "off"
    iommu = (
        ",iommu_platform=on,disable-modern=off,disable-legacy=on"
        if iommu_option
        else ""
    )

    if iothread:
        option = f"""
            -blockdev node-name=q1,driver=raw,file.driver={driver},file.filename={file},file.aio={aio},cache.direct={cache_direct},cache.no-flush=off
            -device virtio-blk-pci,drive=q1,iothread=iothread0{iommu}
            -object iothread,id=iothread0
        """
    else:
        option = f"""
            -blockdev node-name=q1,driver=raw,file.driver={driver},file.filename={file},file.aio={aio},cache.direct={cache_direct},cache.no-flush=off
            -device virtio-blk-pci,drive=q1{iommu}
        """

    return shlex.split(option)


def qemu_option_nvme(
    size: str = "500G",
    bps_rd: str = "10G",
    bps_wr: str = "2.5G",
) -> Tuple[List[str], str]:
    tmpfile = tempfile.NamedTemporaryFile(
        mode="wb", prefix="nvme_", suffix=".img", delete=False
    )
    tmpfile_path = tmpfile.name
    tmpfile.close()

    subprocess.run(
        ["truncate", "-s", str(parse_size_to_bytes(size)), tmpfile_path], check=True
    )

    bps_rd_bytes = parse_size_to_bytes(bps_rd)
    bps_wr_bytes = parse_size_to_bytes(bps_wr)

    drive_opts = f"file={tmpfile_path},if=none,id=nvme0n1,format=raw,cache=writethrough"
    if bps_rd_bytes > 0:
        drive_opts += f",throttling.bps-read={bps_rd_bytes}"
    if bps_wr_bytes > 0:
        drive_opts += f",throttling.bps-write={bps_wr_bytes}"

    option = f"""
        -drive {drive_opts}
        -device nvme,id=nvme0,serial=deadbeef
        -device nvme-ns,drive=nvme0n1,bus=nvme0,nsid=1
    """

    return shlex.split(option), tmpfile_path


def qemu_option_virtio_nic(
    tap: str = "tap0",
    mtap: str = "mtap0",
    vhost: bool = False,
    mq: bool = False,
    config: Optional[dict] = None,
) -> List[str]:
    if config is None:
        config = {}
    resource: VMResource = config["resource"]
    iommu_option = config.get("virtio_iommu", False)
    vhost_option = "on" if vhost else "off"
    iommu = (
        ",iommu_platform=on,disable-modern=off,disable-legacy=on"
        if iommu_option
        else ""
    )

    if mq:
        option = f"""
        -netdev tap,id=en0,ifname={mtap},script=no,downscript=no,vhost={vhost_option},queues={resource.cpu}
        -device virtio-net-pci,netdev=en0,mq=on,vectors=18{iommu}
        """
    else:
        option = f"""
        -netdev tap,id=en0,ifname={tap},script=no,downscript=no,vhost={vhost_option}
        -device virtio-net-pci,netdev=en0,mq=off,vectors=18{iommu}
        """

    return shlex.split(option)
