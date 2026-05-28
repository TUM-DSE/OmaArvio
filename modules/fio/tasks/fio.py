#!/usr/bin/env python3
"""FIO benchmark commands."""

import contextlib
import itertools
import time

from invoke import task

from core.tasks.plotting import plot_sar
from core.tasks import vm as vm_tasks
from core.tasks.utils.device import Devices
from core.tasks.utils.pci import check_speed
from modules.fio.tasks.actions.storage import FioJobConfig
from modules.fio.tasks.plotting import plot_fio

FIO_JOBS = [
    "spdk",
    "io_uring_cmd",
    "libaio",
    "libaio-ext4",
    "libaio-f2fs",
    "libaio-luks-ext4-aes",
    "libaio-luks-f2fs-aes",
    "libaio-luks-ext4-aegis128l",
    "libaio-luks-f2fs-aegis128l",
    "libaio-luks-ext4-aes-xts",
    "libaio-luks-f2fs-aes-xts",
    "libaio-dmverity-ext4",
    "libaio-dmverity-f2fs",
    "libaio-fsverity-ext4",
    "libaio-fsverity-f2fs",
]


@task(name="run-tests")
def run_tests(
    ctx,
    setup: str,
    size: str,
    hostname: str = None,
    sar_enabled: bool = True,
    sar_interval: int = 1,
    sar_options: str = "-u -r -n DEV",
    jobs: str = None,
    device_size: str = "10G",
    block_size: int = None,
    force_mps: bool = False,
    continue_on_error: bool = True,
    qemu_nvme: bool = False,
    nvme_size: str = "500G",
    kvm_perf_enabled: bool = False,
    vfio_trace: bool = False,
    precondition_scheme: str = None,
):
    """Run multiple FIO jobs for a VM or host configuration."""
    job_list = [j.strip() for j in jobs.split(",")] if jobs else FIO_JOBS
    devices = Devices(hostname)

    results = []
    for job in job_list:
        cfg = FioJobConfig.from_job_name(job)
        target = devices.storage_target(
            setup,
            qemu_nvme=qemu_nvme,
            spdk=cfg.engine == "spdk",
        )
        filename = target.filename
        bs_str = f" block_size={block_size}" if block_size else ""
        print(f"Running: {setup}-{size} job={job} filename={filename}{bs_str}")
        start_time = time.time()

        try:
            speed_ctx = (
                contextlib.nullcontext()
                if qemu_nvme
                else check_speed(
                    target.pci_dev,
                    verbose=True,
                    valid_speeds=target.valid_pcie_speeds,
                )
            )
            with speed_ctx:
                action_cfg = {
                    "job": job,
                    "filename": filename,
                    "dev_path": target.dev_path,
                    "device_size": device_size,
                    "pci_dev": target.pci_dev,
                    "force_mps": force_mps,
                    "skip_precondition": qemu_nvme,
                    "precondition_scheme": precondition_scheme,
                }
                if block_size is not None:
                    action_cfg["block_size"] = int(block_size)

                vm_tasks.start(
                    ctx,
                    type=setup,
                    size=size,
                    hostname=hostname,
                    direct=False,
                    action="run-fio",
                    sar_enabled=sar_enabled,
                    sar_interval=sar_interval,
                    sar_options=sar_options,
                    kvm_perf_enabled=kvm_perf_enabled,
                    vfio_trace=vfio_trace,
                    nvme=qemu_nvme,
                    nvme_size=nvme_size,
                    vfio_pcie=[target.vfio_device] if target.vfio_device else [],
                    action_config=action_cfg,
                )

            results.append(
                {
                    "config": f"{setup}-{size}-{job}",
                    "status": "SUCCESS",
                    "duration": time.time() - start_time,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "config": f"{setup}-{size}-{job}",
                    "status": "FAILED",
                    "duration": time.time() - start_time,
                    "error": str(exc),
                }
            )
            print(f"ERROR: {exc}")
            if not continue_on_error:
                raise

    return results


@task(name="run-all")
def run_all(
    ctx,
    setups: str = "amd,snp,host",
    sizes: str = "large",
    hostname: str = None,
    sar_enabled: bool = True,
    sar_interval: int = 1,
    sar_options: str = "-u -r -n DEV",
    jobs: str = None,
    device_size: str = "10G",
    block_sizes: str = "512",
    force_mps: bool = False,
    continue_on_error: bool = True,
    qemu_nvme: bool = False,
    nvme_size: str = "500G",
    kvm_perf_enabled: bool = False,
    vfio_trace: bool = False,
    precondition_scheme: str = None,
    iterations: int = 1,
):
    """Run FIO benchmarks across multiple VM configurations and jobs."""
    setup_list = [s.strip() for s in setups.split(",") if s.strip()]
    size_list = [s.strip() for s in sizes.split(",") if s.strip()]
    block_size_list = [int(bs.strip()) for bs in block_sizes.split(",") if bs.strip()]
    combinations = list(itertools.product(setup_list, size_list, block_size_list))

    print(
        f"Starting benchmark suite: {len(combinations)} configurations x {iterations} iteration(s)"
    )

    all_results = []
    overall_start = time.time()
    for iteration in range(iterations):
        if iterations > 1:
            print(f"\n=== Iteration {iteration + 1}/{iterations} ===")

        for setup, size, block_size in combinations:
            try:
                results = run_tests(
                    ctx,
                    setup=setup,
                    size=size,
                    hostname=hostname,
                    sar_enabled=sar_enabled,
                    sar_interval=sar_interval,
                    sar_options=sar_options,
                    jobs=jobs,
                    device_size=device_size,
                    block_size=block_size,
                    force_mps=force_mps,
                    continue_on_error=continue_on_error,
                    qemu_nvme=qemu_nvme,
                    nvme_size=nvme_size,
                    kvm_perf_enabled=kvm_perf_enabled,
                    vfio_trace=vfio_trace,
                    precondition_scheme=precondition_scheme,
                )
                all_results.extend(results)
            except Exception as exc:
                print(f"ERROR in {setup}-{size}-bs{block_size}: {exc}")
                if not continue_on_error:
                    break

    total_duration = time.time() - overall_start
    success_count = sum(1 for r in all_results if r["status"] == "SUCCESS")
    failed_count = sum(1 for r in all_results if r["status"] == "FAILED")
    print(f"\nCompleted in {total_duration/60:.1f} minutes")
    print(f"Results: {success_count} success, {failed_count} failed")


@task(name="plot-results")
def plot_results(ctx, bench_dir="bench-result/fio"):
    """Generate interactive HTML plots from FIO benchmark data."""
    print(f"Generating FIO benchmark plots from: {bench_dir}")
    plot_fio.generate_all_plots(bench_dir)


@task(name="plot-sar-results")
def plot_sar_results(ctx, bench_dir="bench-result/fio"):
    """Generate interactive HTML plots from SAR data."""
    print(f"Generating SAR analysis plots from: {bench_dir}")
    plot_sar.generate_all_plots(bench_dir)
