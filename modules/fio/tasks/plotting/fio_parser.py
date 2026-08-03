"""
FIO Benchmark Parsing Module

Reusable parsing logic for FIO benchmark results.
Used by both interactive HTML plots (plot_fio.py) and paper plots (paper_fio.py).

This module provides:
- FIOData dataclass for storing parsed metrics
- Core JSON parsing functions
- File discovery with timestamp handling
- Color and styling utilities for consistent visualization
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import json


# ============================
# Data Structures
# ============================


@dataclass
class FIOData:
    """Parsed FIO benchmark metrics for a single job."""

    jobname: str
    # Read metrics - bandwidth (in KiB/s, as reported by FIO)
    read_bw_mean: float
    read_bw_stddev: float
    read_bw_min: float
    read_bw_max: float
    # Read metrics - IOPS
    read_iops_mean: float
    read_iops_stddev: float
    read_iops_min: float
    read_iops_max: float
    # Read metrics - latency (in nanoseconds)
    read_lat_mean: float
    read_lat_stddev: float
    read_lat_min: float
    read_lat_max: float
    # Write metrics - bandwidth (in KiB/s, as reported by FIO)
    write_bw_mean: float
    write_bw_stddev: float
    write_bw_min: float
    write_bw_max: float
    # Write metrics - IOPS
    write_iops_mean: float
    write_iops_stddev: float
    write_iops_min: float
    write_iops_max: float
    # Write metrics - latency (in nanoseconds)
    write_lat_mean: float
    write_lat_stddev: float
    write_lat_min: float
    write_lat_max: float
    # Timing (populated from FIO JSON job_start / elapsed fields)
    job_start_ms: Optional[int] = None  # unix ms when this job started
    job_end_ms: Optional[int] = None  # unix ms when this job ended
    # SAR correlation (populated by correlate_with_sar())
    cpu_used_pct: Optional[float] = None
    mem_used_kb: Optional[float] = None
    sar_sample_count: int = 0


# ============================
# Constants
# ============================


# Job type constants for filtering
BW_JOBS = ["bw read", "bw write"]
IOPS_JOBS = ["iops randread", "iops randwrite", "iops rwmixread", "iops rwmixwrite"]
LAT_JOBS = ["alat read", "alat write", "alat randread", "alat randwrite"]


# ============================
# Core Parsing Functions
# ============================


def read_fio_json(filepath: Path) -> dict:
    """Read FIO JSON file with error handling.

    Skips any error messages before the JSON starts (lines before '{').
    This handles malformed FIO output that sometimes includes preamble text.

    Args:
        filepath: Path to FIO JSON output file

    Returns:
        Parsed JSON data as dict
    """
    lines = []
    with open(filepath) as f:
        # Skip lines until we find the opening brace
        for line in f:
            if line.strip() == "{":
                lines.append(line)
                break
        # Read the rest of the file
        for line in f:
            lines.append(line)

    data = json.loads("".join(lines))
    return data


def parse_fio_file(filepath: Path) -> Dict[str, FIOData]:
    """Parse a FIO JSON file and extract metrics for all jobs.

    Also populates job_start_ms and job_end_ms from FIO timing fields:
    - job_start_ms: from job["job_start"] (unix ms)
    - job_end_ms: first job's job_start + job["elapsed"] * 1000
      (elapsed = seconds from FIO process start to end of this job)

    Args:
        filepath: Path to FIO JSON output file

    Returns:
        Dict mapping job names to FIOData objects
    """
    data = read_fio_json(filepath)
    result = {}

    jobs = data["jobs"]
    # FIO process start ≈ first job's job_start (ms)
    fio_start_ms = jobs[0]["job_start"] if jobs else None

    for job in jobs:
        jobname = job["jobname"]

        job_start_ms = job.get("job_start")
        elapsed = job.get("elapsed")
        job_end_ms = None
        if fio_start_ms is not None and elapsed is not None:
            job_end_ms = fio_start_ms + int(elapsed) * 1000

        fio_data = FIOData(
            jobname=jobname,
            # Read bandwidth metrics (FIO reports these in KiB/s)
            read_bw_mean=float(job["read"]["bw_mean"]),
            read_bw_stddev=float(job["read"]["bw_dev"]),
            read_bw_min=float(job["read"]["bw_min"]),
            read_bw_max=float(job["read"]["bw_max"]),
            # Read IOPS metrics
            read_iops_mean=float(job["read"]["iops_mean"]),
            read_iops_stddev=float(job["read"]["iops_stddev"]),
            read_iops_min=float(job["read"]["iops_min"]),
            read_iops_max=float(job["read"]["iops_max"]),
            # Read latency metrics
            read_lat_mean=float(job["read"]["lat_ns"]["mean"]),
            read_lat_stddev=float(job["read"]["lat_ns"]["stddev"]),
            read_lat_min=float(job["read"]["lat_ns"]["min"]),
            read_lat_max=float(job["read"]["lat_ns"]["max"]),
            # Write bandwidth metrics (FIO reports these in KiB/s)
            write_bw_mean=float(job["write"]["bw_mean"]),
            write_bw_stddev=float(job["write"]["bw_dev"]),
            write_bw_min=float(job["write"]["bw_min"]),
            write_bw_max=float(job["write"]["bw_max"]),
            # Write IOPS metrics
            write_iops_mean=float(job["write"]["iops_mean"]),
            write_iops_stddev=float(job["write"]["iops_stddev"]),
            write_iops_min=float(job["write"]["iops_min"]),
            write_iops_max=float(job["write"]["iops_max"]),
            # Write latency metrics
            write_lat_mean=float(job["write"]["lat_ns"]["mean"]),
            write_lat_stddev=float(job["write"]["lat_ns"]["stddev"]),
            write_lat_min=float(job["write"]["lat_ns"]["min"]),
            write_lat_max=float(job["write"]["lat_ns"]["max"]),
            # Timing
            job_start_ms=job_start_ms,
            job_end_ms=job_end_ms,
        )

        result[jobname] = fio_data

    return result


# ============================
# File Discovery Functions
# ============================


def parse_config_from_path(json_path: Path) -> Tuple[str, str, str, str]:
    """Parse configuration information from FIO JSON file path.

    Extracts VM type, size, job name, and timestamp from standardized path structure.
    Handles both path formats:
    - bench-result/fio/{type}-disk-{size}/{job}/timestamp.json
    - bench-result/fio/{type}-{size}/{job}/timestamp.json

    Args:
        json_path: Path to FIO JSON file

    Returns:
        Tuple of (vm_type, size, job_name, timestamp)
        Example: ("snp", "large", "spdk", "2026-01-28-10-43-58")
    """
    # Extract components from path
    job_name = json_path.parent.name
    config_dir = json_path.parent.parent.name
    timestamp = json_path.stem

    # Parse config directory name
    dir_parts = config_dir.split("-")
    if len(dir_parts) < 2:
        return ("unknown", "unknown", job_name, timestamp)

    # Handle both "snp-disk-small" and "host-small" formats
    vm_type = dir_parts[0]
    if dir_parts[1] == "disk":
        # Format: snp-disk-small
        size = dir_parts[2] if len(dir_parts) > 2 else "unknown"
    else:
        # Format: host-small
        size = dir_parts[1]

    return (vm_type, size, job_name, timestamp)


def find_fio_files(bench_result_dir: Path) -> Dict[str, Dict[str, Path]]:
    """Find all FIO JSON files in the benchmark results directory.

    For configurations with multiple timestamps, returns only the latest.

    Args:
        bench_result_dir: Path to benchmark results directory

    Returns:
        Nested dict: {job_name: {config_name: Path}}
        Config names format: 'snp-small', 'amd-medium', 'host-large', etc.
    """
    bench_path = Path(bench_result_dir)

    # First, collect all files grouped by (job, config)
    grouped_files: Dict[Tuple[str, str], List[Path]] = defaultdict(list)

    for json_file in bench_path.rglob("*.json"):
        # Skip files not in the expected structure
        if len(json_file.parts) < 4:
            continue

        # Parse config from path
        vm_type, size, job_name, _ = parse_config_from_path(json_file)

        config_name = f"{vm_type}-{size}"
        grouped_files[(job_name, config_name)].append(json_file)

    # Now select only the latest file for each (job, config) pair
    latest_files: Dict[str, Dict[str, Path]] = defaultdict(dict)

    for (job_name, config_name), file_list in grouped_files.items():
        # Sort by filename (timestamp) and take the latest
        latest_file = max(file_list, key=lambda f: f.stem)
        latest_files[job_name][config_name] = latest_file

    return dict(latest_files)


def find_fio_files_with_history(
    bench_result_dir: Path, max_history: int = 10
) -> Dict[str, Dict[str, List[Tuple[str, Path]]]]:
    """Find all FIO JSON files including historical runs.

    Returns all timestamps for each (job, config) pair, sorted newest first.

    Args:
        bench_result_dir: Path to benchmark results directory
        max_history: Maximum number of historical runs to keep per (job, config)

    Returns:
        Nested dict: {job_name: {config_name: [(timestamp, Path), ...]}}
        List is sorted by timestamp descending (newest first).
    """
    bench_path = Path(bench_result_dir)

    # Collect all files grouped by (job, config)
    grouped_files: Dict[Tuple[str, str], List[Path]] = defaultdict(list)

    for json_file in bench_path.rglob("*.json"):
        if len(json_file.parts) < 4:
            continue

        # Parse config from path
        vm_type, size, job_name, _ = parse_config_from_path(json_file)

        config_name = f"{vm_type}-{size}"
        grouped_files[(job_name, config_name)].append(json_file)

    # Build result with all timestamps, sorted descending
    result: Dict[str, Dict[str, List[Tuple[str, Path]]]] = defaultdict(dict)

    for (job_name, config_name), file_list in grouped_files.items():
        # Sort by filename (timestamp) descending and limit to max_history
        sorted_files = sorted(file_list, key=lambda f: f.stem, reverse=True)[
            :max_history
        ]
        # Create list of (timestamp, path) tuples
        result[job_name][config_name] = [(f.stem, f) for f in sorted_files]

    return dict(result)


# ============================
# SAR Correlation
# ============================


def correlate_with_sar(
    fio_jobs: Dict[str, "FIOData"], guest_sar_path: Path
) -> Dict[str, "FIOData"]:
    """Populate cpu_used_pct / mem_used_kb on each FIOData using guest SAR data.

    Uses job_start_ms / job_end_ms to query the SAR window. Jobs without
    timing info are left unchanged.

    Args:
        fio_jobs: Dict mapping job names to FIOData (with timing fields set)
        guest_sar_path: Path to the guest SAR text file

    Returns:
        The same dict with cpu_used_pct, mem_used_kb, sar_sample_count populated
        for jobs that have a valid time window and matching SAR samples.
    """
    from core.tasks.plotting.sar_parser import parse_sar_file, get_window_average

    sar_data = parse_sar_file(guest_sar_path)

    for job_data in fio_jobs.values():
        if job_data.job_start_ms is None or job_data.job_end_ms is None:
            continue
        avg = get_window_average(sar_data, job_data.job_start_ms, job_data.job_end_ms)
        if avg is not None:
            job_data.cpu_used_pct = avg["cpu_used_pct"]
            job_data.mem_used_kb = avg["mem_used_kb"]
            job_data.sar_sample_count = avg["sample_count"]

    return fio_jobs


def find_bandwidth_fio_files(bench_result_dir: Path) -> Dict[str, Dict[str, Path]]:
    """Find the latest bandwidth-suffix FIO JSON files.

    Like find_fio_files() but only includes job directories whose name contains
    '_bandwidth' (e.g., libaio_bandwidth_bs512, libaio-ext4_bandwidth_bs512).

    Args:
        bench_result_dir: Path to benchmark results directory

    Returns:
        Nested dict: {config_name: {job_dir: Path}} for latest file per config.
        config_name format: "snp-medium", "amd-medium", etc.
    """
    bench_path = Path(bench_result_dir)

    grouped_files: Dict[Tuple[str, str], List[Path]] = defaultdict(list)

    for json_file in bench_path.rglob("*.json"):
        if len(json_file.parts) < 4:
            continue

        job_dir_name = json_file.parent.name
        if "_bandwidth" not in job_dir_name:
            continue

        vm_type, size, job_name, _ = parse_config_from_path(json_file)
        config_name = f"{vm_type}-{size}"
        grouped_files[(job_name, config_name)].append(json_file)

    latest_files: Dict[str, Dict[str, Path]] = defaultdict(dict)

    for (job_name, config_name), file_list in grouped_files.items():
        latest_file = max(file_list, key=lambda f: f.stem)
        latest_files[config_name][job_name] = latest_file

    return dict(latest_files)


# ============================
# Color and Styling Utilities
# ============================


def get_color_for_config(config_name: str) -> str:
    """Get color for a configuration based on VM type.

    Args:
        config_name: Configuration name (e.g., "snp-large", "amd-small", "host-medium")

    Returns:
        Hex color string
    """
    color_map = {
        "snp": "#1f77b4",  # blue
        "amd": "#2ca02c",  # green
        "host": "#9467bd",  # purple
    }

    vm_type = config_name.split("-")[0]
    return color_map.get(vm_type, "#7f7f7f")  # gray as fallback


def lighten_color(hex_color: str, factor: float = 0.2) -> str:
    """Lighten a hex color by blending it with white.

    Args:
        hex_color: Hex color string (e.g., "#1f77b4")
        factor: Lightening factor (0.0 = no change, 1.0 = white)

    Returns:
        Lightened hex color string
    """
    # Remove '#' if present
    hex_color = hex_color.lstrip("#")

    # Convert to RGB
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    # Lighten by blending with white (255, 255, 255)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)

    # Convert back to hex
    return f"#{r:02x}{g:02x}{b:02x}"


def adjust_color_for_filesystem(base_color: str, job_name: str) -> str:
    """Adjust color based on filesystem type in job name.

    ext4 variants are lightened, f2fs variants use base color.

    Args:
        base_color: Base color from VM config
        job_name: Job name (e.g., "luks-ext4-aes", "luks-f2fs-aes")

    Returns:
        Adjusted color string
    """
    if "ext4" in job_name:
        return lighten_color(base_color, factor=0.4)
    else:
        return base_color


def get_legend_group_info(config_name: str) -> Tuple[str, str]:
    """Return (legendgroup, title) for organizing legend by type.

    Args:
        config_name: Configuration name (e.g., "snp-large")

    Returns:
        Tuple of (group_id, group_title)
    """
    vm_type = config_name.split("-")[0]

    if vm_type == "snp":
        return ("snp", "SNP")
    elif vm_type == "amd":
        return ("amd", "AMD")
    elif vm_type == "host":
        return ("host", "HOST")
    else:
        return ("other", "Other")


def get_job_hatch_pattern(job_name: str) -> str:
    """Get hatch pattern for a job type.

    Plotly supports patterns: "", "/", "\\", "x", "-", "|", "+", "."

    ext4 and f2fs variants of the same job use the same pattern,
    differentiated by color instead (ext4 is lighter).

    Args:
        job_name: Job name (e.g., "libaio", "spdk", "ext4", "luks-ext4-aes")

    Returns:
        Pattern string for Plotly marker pattern
    """
    # Map job names to hatch patterns
    # ext4/f2fs pairs use same pattern (differentiated by color)
    hatch_map = {
        "libaio": "/",
        "spdk": "",  # solid (no hatch)
        "ext4": "\\",
        "f2fs": "\\",  # same as ext4
        "luks-ext4-aes": "-",
        "luks-f2fs-aes": "-",  # same as ext4 variant
        "luks-ext4-aegis128l": "+",
        "luks-f2fs-aegis128l": "+",  # same as ext4 variant
        "luks-ext4-aes-xts": "x",
        "luks-f2fs-aes-xts": "x",  # same as ext4 variant
        "dmverity-ext4": "\\",
        "dmverity-f2fs": "\\",  # same as ext4 variant
        "fsverity-ext4": "+",
        "fsverity-f2fs": "+",  # same as ext4 variant
    }

    return hatch_map.get(job_name, "")


# ============================
# Utility Functions
# ============================


def format_timestamp(timestamp: str) -> str:
    """Format timestamp for display.

    Args:
        timestamp: Raw timestamp string (e.g., "2026-01-28-10-43-58")

    Returns:
        Formatted timestamp (e.g., "2026-01-28 10:43")
    """
    parts = timestamp.split("-")
    if len(parts) >= 5:
        return f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:{parts[4]}"
    return timestamp
