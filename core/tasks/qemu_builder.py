#!/usr/bin/env python3
"""QEMU command-line construction builder pattern."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import uuid
from abc import ABC
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple, Dict, Any

from core.tasks.config import PROJECT_ROOT, BUILD_DIR, LINUX_DIR
from core.tasks.resources import VMResource
from core.tasks.utils.utils import parse_size_to_bytes
from core.tasks.utils.pci import short_bdf
from core.tasks.utils.vfio import (
    get_pci_ids,
    bind_device_to_vfio,
    unbind_device_from_vfio,
)
from core.tasks.qemu import spawn_qemu, QemuVm

_HUGEPAGE_1G_GLOBAL = Path("/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages")
_HUGEPAGE_1G_NODE = (
    "/sys/devices/system/node/node{}/hugepages/hugepages-1048576kB/nr_hugepages"
)


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


class QemuVmBuilder:
    """
    Orchestrates the construction and execution of a QEMU Virtual Machine.

    The builder collects a series of `QemuFeature` objects, each responsible
    for a specific logical component of the VM (e.g., networking, storage, CPU).
    When `run()` is called, the builder:
    1. Executes `setup()` on all features (e.g., to create temporary files, bind VFIO devices).
    2. Collects the command line arguments via `qemu_args()` from all features.
    3. Spawns the QEMU process.
    4. Executes `teardown()` on all features (e.g., to unbind devices) when finished.
    """

    def __init__(self, qemu_bin: Path, resource: VMResource):
        self.qemu_bin = qemu_bin
        self.resource = resource
        self.features: List[QemuFeature] = []

    def add_feature(self, feature: QemuFeature) -> "QemuVmBuilder":
        self.features.append(feature)
        return self

    def build_command(self) -> List[str]:
        cmd = [str(self.qemu_bin)]
        for feature in self.features:
            cmd.extend(feature.qemu_args())
        return cmd

    def setup(self) -> None:
        for feature in self.features:
            feature.setup(self)

    def teardown(self) -> None:
        for feature in reversed(self.features):
            try:
                feature.teardown(self)
            except Exception as e:
                print(f"Failed to teardown {feature.__class__.__name__}: {e}")

    @contextmanager
    def run(
        self, config: dict, pin: bool = True, pin_base: Optional[int] = None
    ) -> Iterator[QemuVm]:
        """Convenience method to setup, run, and teardown."""
        self.setup()
        try:
            cmd = self.build_command()
            with spawn_qemu(
                cmd, config=config, numa_node=self.resource.numa_node
            ) as runner:
                if pin:
                    actual_pin_base = (
                        pin_base
                        if pin_base is not None
                        else getattr(self.resource, "pin_base", None)
                    )
                    if actual_pin_base is not None:
                        runner.pin_vcpu(actual_pin_base)
                    else:
                        runner.pin_vcpu()
                yield runner
        finally:
            self.teardown()


class QemuFeature(ABC):
    """
    Base class for all QEMU configuration features.

    To extend the builder with new capabilities:
    1. Subclass `QemuFeature`.
    2. Override `qemu_args()` to return a list of QEMU command-line string arguments.
    3. (Optional) Override `setup()` to perform pre-launch tasks (e.g., create files, bind devices).
    4. (Optional) Override `teardown()` to perform post-launch cleanup. Teardown is guaranteed
       to execute even if QEMU fails.

    Example:
        class MyCustomFeature(QemuFeature):
            def qemu_args(self) -> List[str]:
                return ["-device", "my-custom-device"]
    """

    def setup(self, builder: QemuVmBuilder) -> None:
        """Called before the QEMU process is spawned."""
        pass

    def teardown(self, builder: QemuVmBuilder) -> None:
        """Called after the QEMU process is terminated."""
        pass

    def qemu_args(self) -> List[str]:
        """Returns the list of QEMU command-line arguments for this feature."""
        return []


class CpuFeature(QemuFeature):
    """Configures the CPU architecture, core count, and total memory size."""

    def __init__(self, resource: VMResource):
        self.resource = resource

    def qemu_args(self) -> List[str]:
        return [
            "-enable-kvm",
            "-cpu",
            "EPYC-Genoa-v1,host-phys-bits=true",
            "-smp",
            str(self.resource.cpu),
            "-m",
            f"{self.resource.memory}G",
        ]


class FlatMemoryFeature(QemuFeature):
    """Single memory backend for VMs without guest NUMA topology."""

    def __init__(
        self, resource: VMResource, prealloc: bool = True, hugepages: bool = False
    ):
        self.resource = resource
        self.prealloc = prealloc
        self.hugepages = hugepages

    def _hugepage_paths(self) -> List[Path]:
        # When NUMA binding is active, allocate per-node so pages land on the
        # correct node(s); QEMU's prealloc with policy=bind will otherwise fail
        # with EFAULT if the global pool is distributed across the wrong nodes.
        if self.resource.numa_node:
            return [Path(_HUGEPAGE_1G_NODE.format(n)) for n in self.resource.numa_node]
        return [_HUGEPAGE_1G_GLOBAL]

    def setup(self, builder: QemuVmBuilder) -> None:
        if not self.hugepages:
            return
        paths = self._hugepage_paths()
        required_per_path = self.resource.memory // len(paths)
        self._prev_hugepages: Dict[Path, int] = {}
        for path in paths:
            prev = int(path.read_text().strip())
            self._prev_hugepages[path] = prev
            if prev < required_per_path:
                path.write_text(str(required_per_path))
                actual = int(path.read_text().strip())
                if actual < required_per_path:
                    raise RuntimeError(
                        f"Could only allocate {actual} of {required_per_path} 1 GB hugepages at {path}"
                    )

    def teardown(self, builder: QemuVmBuilder) -> None:
        if not self.hugepages or not hasattr(self, "_prev_hugepages"):
            return
        for path, prev in self._prev_hugepages.items():
            try:
                path.write_text(str(prev))
            except Exception as exc:
                print(
                    f"Warning: failed to restore nr_hugepages to {prev} at {path}: {exc}"
                )

    def qemu_args(self) -> List[str]:
        prealloc_str = "on" if self.prealloc else "off"
        # Legacy VFIO requires hugepages: 4K pages exhaust the iommu_type1 DMA
        # mapping limit (65535) for any reasonable memory size.
        hugepage_opts = ",hugetlb=on,hugetlbsize=1073741824" if self.hugepages else ""
        numa_opts = ""
        if self.resource.numa_node:
            nodes = ",".join(map(str, self.resource.numa_node))
            numa_opts = f",host-nodes={nodes},policy=bind"
        return [
            "-object",
            f"memory-backend-memfd,id=ram1,size={self.resource.memory}G,share=true,prealloc={prealloc_str}{hugepage_opts}{numa_opts}",
        ]


class GuestNumaFeature(QemuFeature):
    """
    Guest NUMA topology: per-cell memory backends with strict host-node binding
    and corresponding -numa node assignments.

    Equivalent to libvirt's <numatune> (strict per-cell binding) combined with
    <cpu><numa> (cell id/cpus/memory layout). CPUs and memory are split evenly
    across the host NUMA nodes listed in resource.numa_node.
    """

    def __init__(
        self, resource: VMResource, prealloc: bool = True, hugepages: bool = False
    ):
        if not resource.numa_node:
            raise ValueError("GuestNumaFeature requires at least one NUMA node")
        self.resource = resource
        self.prealloc = prealloc
        self.hugepages = hugepages

    def setup(self, builder: QemuVmBuilder) -> None:
        if not self.hugepages:
            return
        n = len(self.resource.numa_node)
        mem_per_cell = self.resource.memory // n
        self._prev_hugepages: Dict[Path, int] = {}
        for host_node in self.resource.numa_node:
            path = Path(_HUGEPAGE_1G_NODE.format(host_node))
            prev = int(path.read_text().strip())
            self._prev_hugepages[path] = prev
            if prev < mem_per_cell:
                path.write_text(str(mem_per_cell))
                actual = int(path.read_text().strip())
                if actual < mem_per_cell:
                    raise RuntimeError(
                        f"Could only allocate {actual} of {mem_per_cell} 1 GB hugepages at {path}"
                    )

    def teardown(self, builder: QemuVmBuilder) -> None:
        if not self.hugepages or not hasattr(self, "_prev_hugepages"):
            return
        for path, prev in self._prev_hugepages.items():
            try:
                path.write_text(str(prev))
            except Exception as exc:
                print(
                    f"Warning: failed to restore nr_hugepages to {prev} at {path}: {exc}"
                )

    def qemu_args(self) -> List[str]:
        n = len(self.resource.numa_node)
        cpus_per_cell = self.resource.cpu // n
        mem_per_cell = self.resource.memory // n
        prealloc_str = "on" if self.prealloc else "off"
        hugepage_opts = ",hugetlb=on,hugetlbsize=1073741824" if self.hugepages else ""

        args = []
        for i, host_node in enumerate(self.resource.numa_node):
            args.extend(
                [
                    "-object",
                    f"memory-backend-memfd,id=numa_ram{i},size={mem_per_cell}G,share=true"
                    f",prealloc={prealloc_str}{hugepage_opts},host-nodes={host_node},policy=bind",
                ]
            )
        for i in range(n):
            cpu_start = i * cpus_per_cell
            cpu_end = (i + 1) * cpus_per_cell - 1
            args.extend(
                [
                    "-numa",
                    f"node,nodeid={i},cpus={cpu_start}-{cpu_end},memdev=numa_ram{i}",
                ]
            )
        return args


class AmdMachineFeature(QemuFeature):
    """
    Configures the base QEMU machine type (q35) and optionally enables
    AMD SEV-SNP confidential computing capabilities.
    """

    def __init__(
        self,
        confidential: bool = False,
        hostname: Optional[str] = None,
        attestation: bool = False,
        memory_backend: Optional[str] = None,
    ):
        self.confidential = confidential
        self.hostname = hostname
        self.attestation = attestation
        self.memory_backend = memory_backend
        self.cert_bundle_path: Optional[Path] = None

    def setup(self, builder: QemuVmBuilder) -> None:
        if self.confidential:
            if not os.access("/dev/sev", os.R_OK):
                raise PermissionError(
                    "Cannot access /dev/sev. Likely need to run as root!"
                )
            sev_snp_param = Path("/sys/module/kvm_amd/parameters/sev_snp")
            if not sev_snp_param.exists() or sev_snp_param.read_text().strip() != "Y":
                raise RuntimeError(
                    "SEV-SNP is likely not enabled on this host. Check dmesg for details."
                )
            if self.attestation and self.hostname:
                self.cert_bundle_path = ensure_snp_certificates(self.hostname)

    def qemu_args(self) -> List[str]:
        mem_backend = (
            f",memory-backend={self.memory_backend}" if self.memory_backend else ""
        )
        if self.confidential:
            machine_arg = [
                "-machine",
                f"q35{mem_backend},memory-encryption=sev0,vmport=off,kernel_irqchip=split",
            ]
            sev_guest_config = "id=sev0,cbitpos=51,reduced-phys-bits=1,policy=0x30000"
            if self.attestation and self.cert_bundle_path:
                sev_guest_config += f",certs-filename={self.cert_bundle_path}"
            return machine_arg + ["-object", f"sev-snp-guest,{sev_guest_config}"]
        else:
            return [
                "-machine",
                f"q35{mem_backend},vmport=off,kernel_irqchip=split",
            ]


class BootFeature(QemuFeature):
    """
    Configures the boot process and main OS image.
    Supports either direct kernel booting (`-kernel` and `-append`) or OVMF UEFI booting (`-bios`).
    """

    def __init__(self, vmconfig: Any, direct: bool = False, extra_cmdline: str = ""):
        self.vmconfig = vmconfig
        self.direct = direct
        self.extra_cmdline = extra_cmdline

    def qemu_args(self) -> List[str]:
        args = [
            "-blockdev",
            f"raw,node-name=q2,file.driver=file,file.filename={self.vmconfig.image}",
        ]

        if self.direct:
            cmdline = f"{self.vmconfig.cmdline}{self.extra_cmdline}"
            args.extend(
                [
                    "-device",
                    "virtio-blk-pci,drive=q2",
                    "-kernel",
                    str(self.vmconfig.kernel),
                    "-append",
                    cmdline,
                ]
            )
        else:
            args.extend(
                [
                    "-device",
                    "virtio-blk-pci,drive=q2,bootindex=0",
                    "-bios",
                    str(self.vmconfig.ovmf),
                ]
            )

        return args


class UserNetFeature(QemuFeature):
    """Configures user-mode networking (SLIRP) and SSH port forwarding."""

    def __init__(self, ssh_port: int):
        self.ssh_port = ssh_port

    def qemu_args(self) -> List[str]:
        return [
            "-device",
            "virtio-net-pci,netdev=net0",
            "-netdev",
            f"user,id=net0,hostfwd=tcp::{self.ssh_port}-:22,hostfwd=tcp::8895-:8895",
        ]


class ConsoleFeature(QemuFeature):
    """Configures serial and virtio consoles for nographic execution."""

    def qemu_args(self) -> List[str]:
        return [
            "-nographic",
            "-serial",
            "null",
            "-device",
            "virtio-serial",
            "-chardev",
            "stdio,mux=on,id=char0,signal=off",
            "-mon",
            "chardev=char0,mode=readline",
            "-device",
            "virtconsole,chardev=char0,id=vc0,nr=0",
        ]


class SharedFolderFeature(QemuFeature):
    """
    Mounts a host directory into the guest using virtfs (9pfs).
    The directory is mounted without a security model and is identified by a mount tag.
    """

    def __init__(self, host_path: str, mount_tag: str):
        self.host_path = host_path
        self.mount_tag = mount_tag

    def qemu_args(self) -> List[str]:
        return [
            "-virtfs",
            f"local,path={self.host_path},security_model=none,mount_tag={self.mount_tag}",
        ]


class VirtioBlkFeature(QemuFeature):
    """
    Attaches a block device or image file via virtio-blk-pci.
    Automatically assigns a unique ID to support attaching multiple drives.
    Optionally configures dedicated iothreads and raw/host_device caching.
    """

    _id_counter = 0

    def __init__(
        self,
        file_path: Path,
        aio: str = "native",
        direct: bool = True,
        iothread: bool = True,
        iommu_option: bool = False,
    ):
        self.file_path = file_path
        self.aio = aio
        self.direct = direct
        self.iothread = iothread
        self.iommu_option = iommu_option
        VirtioBlkFeature._id_counter += 1
        self.idx = VirtioBlkFeature._id_counter

    def qemu_args(self) -> List[str]:
        driver = "host_device" if self.file_path.is_block_device() else "file"
        cache_direct = "on" if self.direct else "off"
        iommu = (
            ",iommu_platform=on,disable-modern=off,disable-legacy=on"
            if self.iommu_option
            else ""
        )
        node_name = f"qblk{self.idx}"
        iothread_id = f"iothread_blk{self.idx}"

        args = [
            "-blockdev",
            f"node-name={node_name},driver=raw,file.driver={driver},file.filename={self.file_path},file.aio={self.aio},cache.direct={cache_direct},cache.no-flush=off",
        ]

        if self.iothread:
            args.extend(
                [
                    "-device",
                    f"virtio-blk-pci,drive={node_name},iothread={iothread_id}{iommu}",
                    "-object",
                    f"iothread,id={iothread_id}",
                ]
            )
        else:
            args.extend(["-device", f"virtio-blk-pci,drive={node_name}{iommu}"])
        return args


class VirtioNicFeature(QemuFeature):
    """
    Attaches a network interface via virtio-net-pci using a pre-configured TAP device.
    Automatically assigns a unique ID. Can enable vhost-net and multiqueue support.
    """

    _id_counter = 0

    def __init__(
        self,
        tap: str = "tap0",
        mtap: str = "mtap0",
        vhost: bool = False,
        mq: bool = False,
        cpu_count: int = 1,
        iommu_option: bool = False,
    ):
        self.tap = tap
        self.mtap = mtap
        self.vhost = vhost
        self.mq = mq
        self.cpu_count = cpu_count
        self.iommu_option = iommu_option
        VirtioNicFeature._id_counter += 1
        self.idx = VirtioNicFeature._id_counter

    def qemu_args(self) -> List[str]:
        vhost_option = "on" if self.vhost else "off"
        iommu = (
            ",iommu_platform=on,disable-modern=off,disable-legacy=on"
            if self.iommu_option
            else ""
        )
        netdev_id = f"en{self.idx}"

        if self.mq:
            return [
                "-netdev",
                f"tap,id={netdev_id},ifname={self.mtap},script=no,downscript=no,vhost={vhost_option},queues={self.cpu_count}",
                "-device",
                f"virtio-net-pci,netdev={netdev_id},mq=on,vectors=18{iommu}",
            ]
        else:
            return [
                "-netdev",
                f"tap,id={netdev_id},ifname={self.tap},script=no,downscript=no,vhost={vhost_option}",
                "-device",
                f"virtio-net-pci,netdev={netdev_id},mq=off,vectors=18{iommu}",
            ]


class NvmeEmulationFeature(QemuFeature):
    """
    Provides an emulated NVMe device backed by a dynamically created temporary file.
    Creates the temporary block file using `truncate` during `setup()`, and ensures
    it is securely deleted during `teardown()`.
    Supports throttling read and write bandwidth.
    """

    _id_counter = 0

    def __init__(
        self,
        size: str = "500G",
        bps_rd: str = "10G",
        bps_wr: str = "2.5G",
    ):
        self.size = size
        self.bps_rd = bps_rd
        self.bps_wr = bps_wr
        self.tmpfile_path: Optional[str] = None
        NvmeEmulationFeature._id_counter += 1
        self.idx = NvmeEmulationFeature._id_counter

    def setup(self, builder: QemuVmBuilder) -> None:
        print(
            f"Use emulated NVMe: size={self.size}, bps_rd={self.bps_rd}, bps_wr={self.bps_wr}"
        )
        tmpfile = tempfile.NamedTemporaryFile(
            mode="wb", prefix=f"nvme_{self.idx}_", suffix=".img", delete=False
        )
        self.tmpfile_path = tmpfile.name
        tmpfile.close()
        subprocess.run(
            ["truncate", "-s", str(parse_size_to_bytes(self.size)), self.tmpfile_path],
            check=True,
        )

    def teardown(self, builder: QemuVmBuilder) -> None:
        if self.tmpfile_path and os.path.exists(self.tmpfile_path):
            try:
                os.unlink(self.tmpfile_path)
                print(f"Cleaned up NVMe backing file: {self.tmpfile_path}")
            except OSError as exc:
                print(
                    f"Warning: Failed to clean up NVMe backing file {self.tmpfile_path}: {exc}"
                )

    def qemu_args(self) -> List[str]:
        if not self.tmpfile_path:
            return []

        bps_rd_bytes = parse_size_to_bytes(self.bps_rd)
        bps_wr_bytes = parse_size_to_bytes(self.bps_wr)

        drive_opts = f"file={self.tmpfile_path},if=none,id=nvme{self.idx}n1,format=raw,cache=writethrough"
        if bps_rd_bytes > 0:
            drive_opts += f",throttling.bps-read={bps_rd_bytes}"
        if bps_wr_bytes > 0:
            drive_opts += f",throttling.bps-write={bps_wr_bytes}"

        return [
            "-drive",
            drive_opts,
            "-device",
            f"nvme,id=nvme{self.idx},serial=deadbeef{self.idx}",
            "-device",
            f"nvme-ns,drive=nvme{self.idx}n1,bus=nvme{self.idx},nsid=1",
        ]


class VfioGroupFeature(QemuFeature):
    """
    Passthroughs a list of host PCI devices to the guest via VFIO.
    During `setup()`, it binds all requested host devices to the `vfio-pci` driver.
    During `teardown()`, it safely restores the devices to their original drivers.
    Optionally enables VFIO region tracing to a specified log file.
    """

    def __init__(self, pci_ids: List[str], trace_file: Optional[Path] = None):
        self.pci_ids = [short_bdf(d) for d in pci_ids]
        self.trace_file = trace_file
        self.original_drivers: Dict[str, Optional[str]] = {}

    def setup(self, builder: QemuVmBuilder) -> None:
        for device in self.pci_ids:
            self.original_drivers[device] = bind_device_to_vfio(device)

    def teardown(self, builder: QemuVmBuilder) -> None:
        for device in reversed(self.pci_ids):
            if device in self.original_drivers:
                try:
                    unbind_device_from_vfio(device, self.original_drivers[device])
                except Exception as exc:
                    print(f"Warning: Failed to restore {device}: {exc}")

    def qemu_args(self) -> List[str]:
        if not self.pci_ids:
            return []

        args = ["-object", "iommufd,id=iommufd0"]
        if self.trace_file:
            args.extend(
                [
                    "-trace",
                    f"enable=vfio_region_write,enable=vfio_region_read,file={self.trace_file}",
                ]
            )

        tracing = ",x-no-mmap=true" if self.trace_file else ""
        for idx, device in enumerate(self.pci_ids):
            vendor_id, device_id = get_pci_ids(device)
            args.extend(
                [
                    "-device",
                    f"pcie-root-port,id=rp{idx},bus=pcie.0,chassis=0,slot={idx},multifunction=off",
                    "-device",
                    f"vfio-pci,host=0000:{device},x-pci-vendor-id={vendor_id},x-pci-device-id={device_id},bus=rp{idx},iommufd=iommufd0{tracing}",
                ]
            )

        return args


class VfioGroupLegacyFeature(QemuFeature):
    """
    Passthroughs host PCI devices via the legacy VFIO container API (no iommufd).

    Omits -object iommufd and iommufd= on each vfio-pci device so QEMU uses
    /dev/vfio/<group>, placing all devices in a shared IOMMU domain.
    This is required for P2P DMA between passed-through devices inside the VM.
    """

    def __init__(self, pci_ids: List[str], trace_file: Optional[Path] = None):
        self.pci_ids = [short_bdf(d) for d in pci_ids]
        self.trace_file = trace_file
        self.original_drivers: Dict[str, Optional[str]] = {}

    def setup(self, builder: QemuVmBuilder) -> None:
        for device in self.pci_ids:
            self.original_drivers[device] = bind_device_to_vfio(device)

    def teardown(self, builder: QemuVmBuilder) -> None:
        for device in reversed(self.pci_ids):
            if device in self.original_drivers:
                try:
                    unbind_device_from_vfio(device, self.original_drivers[device])
                except Exception as exc:
                    print(f"Warning: Failed to restore {device}: {exc}")

    def qemu_args(self) -> List[str]:
        if not self.pci_ids:
            return []

        args = []
        if self.trace_file:
            args.extend(
                [
                    "-trace",
                    f"enable=vfio_region_write,enable=vfio_region_read,file={self.trace_file}",
                ]
            )

        tracing = ",x-no-mmap=true" if self.trace_file else ""

        # All passthrough devices go behind ONE emulated PCIe switch so they
        # share a common upstream bridge. Linux p2pdma only permits P2P DMA
        # between endpoints under a shared switch, so this is what lets the
        # NVMe DMA straight into the GPU BAR instead of bouncing through RAM.
        # pxb-pcie pins the entire hierarchy to NUMA node 0 so the guest ACPI
        # SRAT table reflects the correct memory affinity for direct DMA.
        #
        #   pcie.0
        #     └─ pxb-pcie (pxb0, numa_node=0)
        #          └─ pcie-root-port (sw_rp)
        #               └─ x3130-upstream (sw_up)
        #                    ├─ xio3130-downstream (sw_ds0) ─ vfio-pci dev0
        #                    └─ xio3130-downstream (sw_ds1) ─ vfio-pci dev1
        args.extend(
            [
                "-device",
                "pxb-pcie,id=pxb0,bus_nr=64,numa_node=0",
                "-device",
                "pcie-root-port,id=sw_rp,bus=pxb0,chassis=100,slot=0,multifunction=off",
                "-device",
                "x3130-upstream,id=sw_up,bus=sw_rp",
            ]
        )

        for idx, device in enumerate(self.pci_ids):
            vendor_id, device_id = get_pci_ids(device)
            ds_id = f"sw_ds{idx}"
            args.extend(
                [
                    "-device",
                    f"xio3130-downstream,id={ds_id},bus=sw_up,"
                    f"chassis={200 + idx},slot={idx}",
                    "-device",
                    f"vfio-pci,host=0000:{device},"
                    f"x-pci-vendor-id={vendor_id},x-pci-device-id={device_id},"
                    f"bus={ds_id}{tracing}",
                ]
            )

        return args


class EduFeature(QemuFeature):
    """Attaches the QEMU educational device (edu) for testing DMA and interrupts."""

    def qemu_args(self) -> List[str]:
        return ["-device", "edu,dma_mask=0xffffffffffffffff"]


class ExtraCmdFeature(QemuFeature):
    """Injects a raw string of arbitrary QEMU arguments directly into the command line."""

    def __init__(self, cmd: str):
        self.cmd = cmd

    def qemu_args(self) -> List[str]:
        return shlex.split(self.cmd)
