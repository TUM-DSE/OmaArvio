#!/usr/bin/env python3

import csv
import io
import re
import sys
from pathlib import Path

from invoke import task

from core.tasks import vm as vm_tasks
from core.tasks.utils.iommu import iommu_label
from core.tasks.utils.device import Devices
from core.tasks.utils.pci import check_speed

_METRIC_FILE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})-(.+)-xfer(\d)\.txt$"
)
_GDSIO_RE = re.compile(
    r"Throughput:\s+([\d.]+)\s+GiB/sec,\s+Avg_Latency:\s+([\d.]+)\s+usecs"
    r"\s+ops:\s+(\d+)\s+total_time\s+([\d.]+)\s+secs"
)

_JOB_METRIC = {
    "bw-read": "bw_r",
    "bw-write": "bw_w",
    "iops-randread": "iops_r",
    "iops-randwrite": "iops_w",
    "lat-read": "lat_r",
    "lat-write": "lat_w",
}

_COLUMNS = [
    "setup",
    "xfer_type",
    "bw_r_gib",
    "bw_w_gib",
    "iops_r",
    "iops_w",
    "lat_r_avg_us",
    "lat_w_avg_us",
]


def _parse_metric_file(path: Path) -> dict:
    """Return parsed fields from one gdsio output line."""
    text = path.read_text().strip()
    m = _GDSIO_RE.search(text)
    if not m:
        return {}
    throughput, avg_lat, ops, total_time = m.groups()
    iops = int(ops) / float(total_time)
    return {
        "throughput_gib": float(throughput),
        "avg_latency_us": float(avg_lat),
        "iops": iops,
    }


def _latest_timestamp(setup_dir: Path) -> str | None:
    """Return the lexicographically latest timestamp prefix with metric files."""
    timestamps = set()
    for f in setup_dir.iterdir():
        m = _METRIC_FILE_RE.match(f.name)
        if m:
            timestamps.add(m.group(1))
    return max(timestamps) if timestamps else None


@task
def plot(
    ctx,
    bench_dir: str = "bench-result/gdsio",
    output_dir: str = "bench-result/gdsio-plots-paper",
    formats: str = "pdf",
):
    """Generate paper-quality average latency plot from gdsio results.

    Examples:
        inv gds.plot
        inv gds.plot --formats pdf,png
        inv gds.plot --bench-dir bench-result/gdsio --output-dir /tmp/plots
    """
    raise RuntimeError(
        "gds.plot still needs a modular plotting implementation. "
        "Use gds.results-csv for current result summarization."
    )


@task
def results_csv(ctx, base_dir: str = "bench-result/gdsio", output: str = "-"):
    """Print CSV table of the latest gdsio results per setup.

    Rows: one per (setup-directory, xfer_type) combination found in the latest run.
    Columns: bw_r_gib, bw_w_gib, iops_r, iops_w, lat_r_avg_us, lat_w_avg_us.

    output='-' writes to stdout; any other value is treated as a file path.
    """
    base = Path(base_dir)
    rows = []

    for setup_dir in sorted(base.iterdir()):
        if not setup_dir.is_dir():
            continue
        ts = _latest_timestamp(setup_dir)
        if ts is None:
            continue

        # Collect all xfer_type numbers present for this timestamp.
        xfer_jobs: dict[int, dict[str, dict]] = {}
        for f in setup_dir.iterdir():
            m = _METRIC_FILE_RE.match(f.name)
            if not m or m.group(1) != ts:
                continue
            job_name = m.group(2)
            xfer_n = int(m.group(3))
            if job_name not in _JOB_METRIC:
                continue
            parsed = _parse_metric_file(f)
            if parsed:
                xfer_jobs.setdefault(xfer_n, {})[job_name] = parsed

        for xfer_n in sorted(xfer_jobs):
            jobs = xfer_jobs[xfer_n]
            row = {"setup": setup_dir.name, "xfer_type": xfer_n}

            def _bw(job):
                return jobs[job]["throughput_gib"] if job in jobs else ""

            def _iops(job):
                return jobs[job]["iops"] if job in jobs else ""

            def _lat(job):
                return jobs[job]["avg_latency_us"] if job in jobs else ""

            row["bw_r_gib"] = _bw("bw-read")
            row["bw_w_gib"] = _bw("bw-write")
            row["iops_r"] = _iops("iops-randread")
            row["iops_w"] = _iops("iops-randwrite")
            row["lat_r_avg_us"] = _lat("lat-read")
            row["lat_w_avg_us"] = _lat("lat-write")
            rows.append(row)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    csv_text = buf.getvalue()

    if output == "-":
        sys.stdout.write(csv_text)
    else:
        Path(output).write_text(csv_text)
        print(f"Written to {output}")


@task
def run_elbencho(
    ctx,
    setup: str = "host",
    no_iommu: bool = False,
    gds: bool = False,
    size: str = "large",
    hostname: str = None,
    file_size: str = "2t",
    sar_enabled: bool = True,
):
    """Run elbencho benchmark for one variant.

    The no_iommu flag is explicit — toggling IOMMU requires a server reboot.
    Default assumes IOMMU is enabled in BIOS. Pass --no-iommu when the
    server is booted with IOMMU disabled.

    For host variants, run load-elbencho-docker once before benchmarking to
    seed the Docker daemon with the pinned image from the Nix store.

    Examples:
        inv gds.run-elbencho --setup host
        inv gds.run-elbencho --setup host --no-iommu
        inv gds.run-elbencho --setup host --gds
        inv gds.run-elbencho --setup host --no-iommu --gds
        inv gds.run-elbencho --setup snp
    """
    devices = Devices(hostname)
    nvme_device = devices.nvme_short
    dev_path = devices.dev_path
    vfio_nvme = None if setup == "host" else nvme_device

    # name_extra carries IOMMU/GDS state into the output path for host variants.
    # vm.py produces: host-{size}{name_extra} or {type}-disk-{size}{name_extra}
    if setup == "host":
        name_extra = f"-{iommu_label(no_iommu)}"
        if gds:
            name_extra += "-gds"
    else:
        name_extra = ""

    action_cfg = {
        "dev_path": dev_path,
        "file_size": file_size,
        "gds": gds,
    }

    vm_tasks.start(
        ctx,
        type=setup,
        size=size,
        hostname=hostname,
        direct=False,
        action="run-elbencho",
        sar_enabled=sar_enabled,
        name_extra=name_extra,
        vfio_pcie=[nvme_device] if vfio_nvme else [],
        action_config=action_cfg,
    )


@task
def run_gdsio(
    ctx,
    setup: str = "host",
    no_iommu: bool = False,
    size: str = "large",
    hostname: str = None,
    file_size: str = "100G",
    sar_enabled: bool = True,
    xfer_types: str = "0,1,2",
    gpu_id: int = 0,
    numa_node: int = 0,
):
    """Run gdsio benchmark for one variant.

    xfer_types is a comma-separated list of transfer types to benchmark:
      0 = GPU_DIRECT (Storage→GPU via GDS)
      1 = CPU_ONLY   (Storage→CPU)
      2 = CPU_GPU    (Storage→CPU→GPU)

    The no_iommu flag is explicit — toggling IOMMU requires a server reboot.
    Default assumes IOMMU is enabled in BIOS. Pass --no-iommu when the
    server is booted with IOMMU disabled.

    The gds-base Docker image is loaded automatically if not already present
    (host runs only). For VM runs it is pre-loaded by the systemd service.

    Examples:
        inv gds.run-gdsio --setup host
        inv gds.run-gdsio --setup host --no-iommu
        inv gds.run-gdsio --setup host --xfer-types 0,1
        inv gds.run-gdsio --setup snp
    """
    devices = Devices(hostname)
    nvme_device = devices.nvme_short
    dev_path = devices.dev_path

    if setup == "host":
        name_extra = f"-{iommu_label(no_iommu)}"
    else:
        name_extra = ""

    xfer_list = [int(x.strip()) for x in xfer_types.split(",")]

    action_cfg = {
        "dev_path": dev_path,
        "file_size": file_size,
        "xfer_types": xfer_list,
        "gpu_id": gpu_id,
        "numa_node": numa_node,
    }

    vfio_devices = []
    if setup != "host":
        vfio_devices.append(nvme_device)

        gpu_pci = devices.gpu_pci
        if gpu_pci is None or devices.gpu_short is None:
            raise ValueError(
                f"No GPU configured for host '{devices.hostname}'. "
                "Please add gpu_pci to config.toml."
            )
        vfio_devices.append(devices.gpu_short)
        print(f"GPU passthrough enabled: {gpu_pci}")

    with check_speed(
        devices.nvme_pci,
        verbose=True,
        valid_speeds=devices.valid_pcie_speeds,
    ):
        vm_tasks.start(
            ctx,
            type=setup,
            size=size,
            hostname=hostname,
            direct=False,
            action="run-gdsio",
            sar_enabled=sar_enabled,
            name_extra=name_extra,
            vfio_pcie=vfio_devices,
            vfio_pcie_legacy=setup == "amd",
            action_config=action_cfg,
        )
