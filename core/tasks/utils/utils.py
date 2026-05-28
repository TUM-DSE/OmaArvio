#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path
from typing import Optional

from invoke import task

import core.tasks.config as config
from core.tasks.config import PROJECT_ROOT


def get_benchmark_output_path(
    *path_components, timestamp: Optional[str] = None, create_dirs: bool = True
) -> tuple[Path, Path, str]:
    """Generate standardized benchmark output paths.

    Creates paths following: bench-result/{component1}/{component2}/.../

    This function ensures that both vm.py and action functions generate identical
    paths, which is critical for VFIO trace file placement. When vfio_trace is
    enabled, vm.py uses the outputdir_host returned by this function to place
    trace files in the same directory as benchmark outputs.

    Args:
        *path_components: Variable path components (e.g., 'fio', name, job)
        timestamp: Optional timestamp string (default: auto-generate)
                  IMPORTANT: Pass the same timestamp to both vm.py and action
                  calls to ensure paths match exactly
        create_dirs: Whether to create directories on host (default: True)

    Returns:
        tuple: (outputdir_host, outputdir_guest, timestamp)

    Example:
        # FIO
        host, guest, ts = get_benchmark_output_path("fio", name, job_dir, timestamp=ts)

        # OpenSSL
        host, guest, ts = get_benchmark_output_path("openssl", name, timestamp=ts)
    """
    date = timestamp if timestamp else datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    # Build path from components
    path_str = "/".join(str(c) for c in path_components)
    outputdir = Path(f"./bench-result/{path_str}/")

    outputdir_host = PROJECT_ROOT / outputdir
    if create_dirs:
        outputdir_host.mkdir(parents=True, exist_ok=True)

    outputdir_guest = Path("/share") / outputdir

    return outputdir_host, outputdir_guest, date


@task
def show_config(ctx):
    """Show script configuration"""
    print(f"SCRIPT_ROOT: {config.SCRIPT_ROOT}")
    print(f"PROJECT_ROOT: {config.PROJECT_ROOT}")
    print(f"BUILD_DIR: {config.BUILD_DIR}")
