"""
SAR (System Activity Reporter) Parser

Pure parsing module for SAR output files. Extracted from plot_sar.py to allow
reuse in bandwidth reporting and other analysis pipelines.
"""

import re
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class SARData:
    """Structured SAR data for a single benchmark run."""

    timestamps: List[float] = field(default_factory=list)  # Relative seconds from start
    abs_timestamps: List[datetime] = field(default_factory=list)  # Absolute datetimes
    cpu_user: List[float] = field(default_factory=list)
    cpu_nice: List[float] = field(default_factory=list)
    cpu_system: List[float] = field(default_factory=list)
    cpu_iowait: List[float] = field(default_factory=list)
    cpu_steal: List[float] = field(default_factory=list)
    cpu_idle: List[float] = field(default_factory=list)

    mem_free_kb: List[float] = field(default_factory=list)
    mem_avail_kb: List[float] = field(default_factory=list)
    mem_used_kb: List[float] = field(default_factory=list)
    mem_used_pct: List[float] = field(default_factory=list)
    mem_buffers_kb: List[float] = field(default_factory=list)
    mem_cached_kb: List[float] = field(default_factory=list)


def parse_timestamp(time_str: str, date_str: str = None) -> datetime:
    """Parse SAR timestamp format (e.g., '02:08:01 PM') to datetime.

    Args:
        time_str: Time string in format 'HH:MM:SS AM/PM'
        date_str: Optional date string in format 'MM/DD/YYYY' from SAR header

    Returns:
        datetime object
    """
    time_obj = datetime.strptime(time_str, "%I:%M:%S %p")

    if date_str:
        date_obj = datetime.strptime(date_str, "%m/%d/%Y")
        return datetime.combine(date_obj.date(), time_obj.time())
    else:
        return datetime.combine(datetime.today().date(), time_obj.time())


def parse_sar_file(filepath: Path) -> SARData:
    """Parse a SAR text file and extract CPU and memory metrics.

    Args:
        filepath: Path to SAR text file (e.g., *_host_sar.txt or *_guest_sar.txt)

    Returns:
        SARData object with parsed metrics, including abs_timestamps for window queries.
    """
    data = SARData()

    with open(filepath, "r") as f:
        lines = f.readlines()

    # Extract date from header (line 0: "Linux 6.17.2 (nixos) 	10/22/2025 	_x86_64_	(8 CPU)")
    date_str = None
    if lines:
        header_match = re.search(r"(\d{2}/\d{2}/\d{4})", lines[0])
        if header_match:
            date_str = header_match.group(1)

    # current_date advances by 1 day each time timestamps wrap past midnight
    current_date: Optional[date] = (
        datetime.strptime(date_str, "%m/%d/%Y").date() if date_str else None
    )

    start_time = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines and headers
        if not line or line.startswith("Linux") or "CPU" in line and "%user" in line:
            i += 1
            continue

        # Parse CPU line (format: "02:08:02 PM     all      0.00 ...")
        if "all" in line:
            parts = line.split()
            if len(parts) >= 8:
                try:
                    time_str = f"{parts[0]} {parts[1]}"
                    time_obj = datetime.strptime(time_str, "%I:%M:%S %p").time()

                    if current_date is None:
                        current_date = datetime.today().date()

                    candidate = datetime.combine(current_date, time_obj)

                    # Detect midnight rollover: new time is >12h before last sample
                    if data.abs_timestamps and candidate < data.abs_timestamps[
                        -1
                    ] - timedelta(hours=12):
                        current_date += timedelta(days=1)
                        candidate = datetime.combine(current_date, time_obj)

                    timestamp = candidate

                    if start_time is None:
                        start_time = timestamp

                    relative_time = (timestamp - start_time).total_seconds()

                    data.timestamps.append(relative_time)
                    data.abs_timestamps.append(timestamp)
                    data.cpu_user.append(float(parts[3]))
                    data.cpu_nice.append(float(parts[4]))
                    data.cpu_system.append(float(parts[5]))
                    data.cpu_iowait.append(float(parts[6]))
                    data.cpu_steal.append(float(parts[7]))
                    data.cpu_idle.append(float(parts[8]))
                except (ValueError, IndexError):
                    pass

        # Parse memory line
        elif "kbmemfree" not in line and len(line.split()) >= 11:
            parts = line.split()
            if len(parts) >= 13 and parts[0][0].isdigit():
                try:
                    if len(data.mem_free_kb) < len(data.timestamps):
                        data.mem_free_kb.append(float(parts[2]))
                        data.mem_avail_kb.append(float(parts[3]))
                        data.mem_used_kb.append(float(parts[4]))
                        data.mem_used_pct.append(float(parts[5]))
                        data.mem_buffers_kb.append(float(parts[6]))
                        data.mem_cached_kb.append(float(parts[7]))
                except (ValueError, IndexError):
                    pass

        i += 1

    return data


def get_window_average(
    sar_data: SARData, start_unix_ms: int, end_unix_ms: int
) -> Optional[dict]:
    """Average CPU% and memory over a unix-ms time window.

    Converts each SAR abs_timestamp via .timestamp()*1000 to compare with unix ms.

    Args:
        sar_data: Parsed SAR data with abs_timestamps populated
        start_unix_ms: Window start in unix milliseconds
        end_unix_ms: Window end in unix milliseconds

    Returns:
        {"cpu_used_pct": float, "mem_used_kb": float, "sample_count": int}
        or None if no samples fall in the window.
    """
    cpu_samples = []
    mem_samples = []

    for i, abs_ts in enumerate(sar_data.abs_timestamps):
        ts_ms = int(abs_ts.timestamp() * 1000)
        if start_unix_ms <= ts_ms <= end_unix_ms:
            cpu_used = 100.0 - sar_data.cpu_idle[i]
            cpu_samples.append(cpu_used)
            if i < len(sar_data.mem_used_kb):
                mem_samples.append(sar_data.mem_used_kb[i])

    if not cpu_samples:
        return None

    return {
        "cpu_used_pct": sum(cpu_samples) / len(cpu_samples),
        "mem_used_kb": sum(mem_samples) / len(mem_samples) if mem_samples else 0.0,
        "sample_count": len(cpu_samples),
    }


def find_sar_files(bench_result_dir: Path) -> Dict[str, Dict[str, Path]]:
    """Find all SAR files in the benchmark results directory.

    Args:
        bench_result_dir: Path to bench-result/fio directory

    Returns:
        Nested dict: {job_name: {config_key: Path}}
        Config keys format: "{vm_type}-{size}-{host|guest}"
    """
    result = {}

    for type_dir in bench_result_dir.iterdir():
        if not type_dir.is_dir() or type_dir.name == "plots":
            continue

        dir_parts = type_dir.name.split("-")
        if len(dir_parts) < 2:
            continue

        vm_type = dir_parts[0]
        size = dir_parts[-1]

        for job_dir in type_dir.iterdir():
            if not job_dir.is_dir():
                continue

            job_name = job_dir.name

            if job_name not in result:
                result[job_name] = {}

            for sar_file in job_dir.glob("*_sar.txt"):
                if "_host_sar.txt" in sar_file.name:
                    key = f"{vm_type}-{size}-host"
                    result[job_name][key] = sar_file
                elif "_guest_sar.txt" in sar_file.name:
                    key = f"{vm_type}-{size}-guest"
                    result[job_name][key] = sar_file

    return result
