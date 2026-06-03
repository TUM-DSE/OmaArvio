#!/usr/bin/env python3
import os
from pathlib import Path
from typing import Optional, List

from core.tasks.actions import ActionContext
from core.tasks.utils.utils import parse_size_to_bytes, parse_size_to_mb
from core.tasks.actions.registry import register_action
from modules.nvme.tasks.utils.storage import (
    format_plain_device,
    cleanup_encrypted_device,
)

GDS_IMAGE = "gds-base"
GDSIO_MOUNT = "/mnt/encrypted"
GDSIO_JOBS_DIR = "/shared/modules/gdsio"
PARTITION_HEADROOM = 1024  # in MB


DEFAULT_XFER_TYPES = [0, 1, 2]
XFER_TYPE_NAMES = {0: "gpu-direct", 1: "cpu-only", 2: "cpu-gpu"}


def discover_gdsio_jobs(vm) -> list:
    """Return sorted list of *.gdsio job config files by querying the VM."""
    result = vm.ssh_cmd(
        ["sh", "-c", f"ls {GDSIO_JOBS_DIR}/*.gdsio 2>/dev/null | sort"],
        check=False,
    )
    return [Path(p.strip()) for p in result.stdout.splitlines() if p.strip()]


@register_action("gdsio")
def run_gdsio(
    ctx: ActionContext,
    dev_path: str = None,
    file_size: str = "100G",
    xfer_types: Optional[List[int]] = None,
    gpu_id: int = 0,
    numa_node: int = 0,
    **kwargs,
):
    """Run gdsio benchmark on an ext4 test file for each xfer_type.

    Sets up an ext4 partition on dev_path, creates a test file, then runs
    each job from gdsio/jobs/*.gdsio via the gds-base Docker container for
    every requested xfer_type. Results are saved as text files in the
    benchmark output directory.

    xfer_types: 0=GPU_DIRECT, 1=CPU_ONLY, 2=CPU_GPU (default: all three)
    """
    if not dev_path:
        raise ValueError("dev_path is required for gdsio benchmark")
    if xfer_types is None:
        xfer_types = DEFAULT_XFER_TYPES

    vm = ctx.vm
    # Build the Docker image if it is not already present.
    is_host = ctx.is_host
    result = vm.ssh_cmd(
        ["docker", "image", "inspect", GDS_IMAGE], check=False, bypass=True
    )
    if result.returncode != 0:
        print(f"{GDS_IMAGE} Docker image not found, building...")
        vm.ssh_cmd(["build-gds-base-docker"], check=True, bypass=True)

    outputdir_host = ctx.outputdir_host
    outputdir_guest = ctx.outputdir_guest
    date = ctx.timestamp

    file_size_mb = int(parse_size_to_mb(file_size))
    partition_size = f"{int(file_size_mb + PARTITION_HEADROOM)}m"
    format_plain_device(
        vm,
        dev_path,
        "ext4",
        size=partition_size,
        file_size=file_size,
        mount_options=["data=ordered"],
    )

    gdsio_file_size = f"{parse_size_to_mb(file_size)}M"

    # TODO: bind mount of /shared/modules/gdsio doesn't work on the host runner
    # because the path does not get correctly rebind to the container.
    # Find a proper solution for exposing shared module data on the host runner.
    if is_host:
        jobs_src = f"{os.getenv("MODULE_SHARED_DATA")}/modules/gdsio"
    else:
        jobs_src = GDSIO_JOBS_DIR

    docker_flags = [
        "--rm",
        "--privileged",
        f"--volume={GDSIO_MOUNT}:/data",
        "--volume=/tmp:/tmp",
        "--device=nvidia.com/gpu=all",
        "--volume=/run/udev:/run/udev:ro",
        "--ipc=host",
        "--env=CUFILE_USE_PCIP2PDMA=true",
        "--env=CUFILE_ALLOW_COMPAT_MODE=false",
        f"--mount=type=bind,source={jobs_src},target=/jobs,readonly",
    ]

    job_files = discover_gdsio_jobs(vm)
    print(f"gdsio jobs dir: {GDSIO_JOBS_DIR}")
    print(f"gdsio jobs found: {[j.name for j in job_files]}")
    print(f"Output dir (host): {outputdir_host}")
    print(f"Output dir (guest): {outputdir_guest}")

    copy_dest_dir = outputdir_host if is_host else outputdir_guest

    for xfer_type in xfer_types:
        xfer_name = XFER_TYPE_NAMES.get(xfer_type, str(xfer_type))
        for job_file in job_files:
            job_name = job_file.stem
            result_path = f"/tmp/gdsio-{job_name}-xfer{xfer_type}-{date}.txt"

            inner_cmd = (
                f"GDSIO_XFER_TYPE={xfer_type} "
                f"GDSIO_FILE_SIZE={gdsio_file_size} "
                f"envsubst < /jobs/{job_file.name} > /tmp/gdsio-job.gdsio "
                f"&& gdsio /tmp/gdsio-job.gdsio > {result_path} 2>&1"
            )
            cmd = ["docker", "run"] + docker_flags + [GDS_IMAGE, "sh", "-c", inner_cmd]

            print(f"Running gdsio {job_name} (xfer={xfer_name})")
            vm.ssh_cmd(cmd, check=True)

            dest = str(copy_dest_dir / f"{date}-{job_name}-xfer{xfer_type}.txt")
            vm.ssh_cmd(["cp", result_path, dest], check=True, bypass=True)
            print(f"  result saved: {dest}")

    cleanup_encrypted_device(vm, "plain", dev_path)
