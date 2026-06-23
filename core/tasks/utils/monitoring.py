import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Union

from core.tasks.config import PROJECT_ROOT
from core.tasks.qemu import HostRunner, QemuVm


@contextmanager
def monitor_with_sar(
    vm: Union[QemuVm, HostRunner],
    output_dir: Path,
    timestamp: str,
    config: dict,
) -> Iterator[None]:
    """Run sar on host and guest while a benchmark action executes."""
    sar_enabled = config.get("sar_enabled", True)
    if not sar_enabled:
        yield
        return

    sar_interval = config.get("sar_interval", 1)
    sar_options = config.get("sar_options", "-u -r -n DEV")

    output_dir.mkdir(parents=True, exist_ok=True)
    host_sar_file = output_dir / f"{timestamp}_host_sar.txt"
    host_sar_proc = None

    # Persist the pinned physical-CPU set (vCPU + iothread cores) so analysis can
    # scope CPU utilization to exactly the cores this run uses. Only present for
    # pinned VM runs; host runs leave it absent (whole-machine accounting).
    pinned_cpus = getattr(vm, "pinned_cpus", None)
    if pinned_cpus:
        (output_dir / f"{timestamp}_pinned_cpus.txt").write_text(
            ",".join(str(c) for c in pinned_cpus)
        )

    try:
        print(f"Starting host sar monitoring -> {host_sar_file}")
        host_sar_proc = subprocess.Popen(
            ["sar"] + sar_options.split() + [str(sar_interval)],
            stdout=open(host_sar_file, "w"),
            stderr=subprocess.DEVNULL,
        )

        if vm and not isinstance(vm, HostRunner):
            guest_sar_relpath = output_dir.relative_to(PROJECT_ROOT)
            guest_sar_path = f"/share/{guest_sar_relpath}/{timestamp}_guest_sar.txt"
            print(f"Starting guest sar monitoring -> {guest_sar_path}")
            vm.ssh_cmd(
                [
                    "sh",
                    "-c",
                    f"nohup sar {sar_options} {sar_interval} > {guest_sar_path} 2>&1 &",
                ],
                check=False,
                verbose=True,
            )

        time.sleep(2)
        yield
    finally:
        print("Stopping sar monitoring...")
        if host_sar_proc:
            host_sar_proc.terminate()
            try:
                host_sar_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                host_sar_proc.kill()
                host_sar_proc.wait()

        if vm and not isinstance(vm, HostRunner):
            vm.ssh_cmd(["pkill", "-TERM", "sar"], check=False, verbose=False)
            time.sleep(1)

        print("SAR monitoring stopped and data saved")


@contextmanager
def monitor_with_perf_kvm(
    output_dir: Path,
    timestamp: str,
    config: dict,
    vm: QemuVm,
) -> Iterator[None]:
    """Context manager to run perf kvm on host to capture KVM events during benchmarks.

    Args:
        output_dir: Directory to save perf output files
        timestamp: Consistent timestamp for file naming
        config: Configuration dict with kvm_perf_enabled
        vm: Optional QemuVm or HostRunner instance to capture guest /proc/iomem

    Yields:
        None - monitoring runs in background during context

    Example:
        with monitor_with_perf(output_dir, timestamp, config, vm):
            run_fio(...)  # perf collects data during this call
    """
    perf_enabled = config.get("kvm_perf_enabled", True)
    if not perf_enabled or isinstance(vm, HostRunner):
        yield  # No monitoring, just pass through
        return

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    perf_data_file = output_dir / f"{timestamp}_perf.data"
    perf_proc = None

    try:
        # Capture /proc/iomem at the start of monitoring
        iomem_file = output_dir / f"{timestamp}_iomem.txt"
        print(f"Capturing /proc/iomem -> {iomem_file}")
        try:
            result = vm.ssh_cmd(
                ["cat", "/proc/iomem"],
                check=True,
                verbose=False,
            )
            with open(iomem_file, "w") as f:
                f.write(result.stdout)
        except Exception as e:
            print(f"Warning: Failed to capture /proc/iomem: {e}")

        # Start perf kvm recording
        print(f"Starting perf kvm recording -> {perf_data_file}")
        perf_proc = subprocess.Popen(
            ["perf", "kvm", "--host", "stat", "record", "-o", str(perf_data_file)]
        )

        # Give perf time to initialize
        time.sleep(1)

        # Yield control back - benchmark runs here
        yield

    finally:
        # Cleanup: stop perf process
        print("Stopping perf monitoring...")

        if perf_proc:
            perf_proc.send_signal(signal.SIGINT)
            try:
                perf_proc.wait(timeout=60 * 5)
            except subprocess.TimeoutExpired:
                print("Perf process did not terminate in time, killing...")
                perf_proc.kill()
                perf_proc.wait()

        # Generate perf kvm report
        if perf_data_file.exists():
            perf_report_file = output_dir / f"{timestamp}_perf_report.txt"
            print(f"Generating perf kvm report -> {perf_report_file}")
            try:
                with open(perf_report_file, "w") as f:
                    subprocess.run(
                        [
                            "perf",
                            "kvm",
                            "-i",
                            str(perf_data_file),
                            "stat",
                            "report",
                            "--event=mmio",
                        ],
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        check=False,
                    )
            except Exception as e:
                print(f"Warning: Failed to generate perf kvm report: {e}")

        print("Perf monitoring stopped and data saved")
