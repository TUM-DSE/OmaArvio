from core.tasks.qemu import HostRunner, QemuVm


import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


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
