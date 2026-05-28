#!/usr/bin/env python3
from pathlib import Path
from typing import Optional, List

from core.tasks.actions import ActionContext
from core.tasks.qemu import QemuVm, HostRunner
from core.tasks.utils.utils import parse_size_to_mb
from core.tasks.actions.registry import register_action
from modules.nvme.tasks.utils.storage import (
    format_plain_device,
    cleanup_encrypted_device,
)

GDS_IMAGE = "gds-base"
GDSIO_MOUNT = "/mnt/encrypted"


def _gdsio_jobs_dir() -> Path:
    return Path("/shared/modules/gdsio")


DEFAULT_XFER_TYPES = [0, 1, 2]

XFER_TYPE_NAMES = {0: "gpu-direct", 1: "cpu-only", 2: "cpu-gpu"}


def discover_gdsio_jobs() -> list:
    """Return sorted list of *.gdsio job config files."""
    return sorted(_gdsio_jobs_dir().glob("*.gdsio"))


def _to_gdsio_size(file_size: str) -> str:
    """Convert file_size string to gdsio-compatible K|M|G format.

    Converts T to G since gdsio doesn't accept T suffix.
    """
    s = file_size.upper().strip()
    if s.endswith("T"):
        return f"{int(s[:-1]) * 1024}G"
    return s


def _gdsio_path(name: str, config: dict) -> tuple:
    return ("gdsio", name)


@register_action("gdsio", path_fn=_gdsio_path)
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
    partition_size = f"{int(file_size_mb * 1.1)}m"
    format_plain_device(
        vm,
        dev_path,
        "ext4",
        size=partition_size,
        file_size=file_size,
        mount_options=["data=ordered"],
    )

    gdsio_file_size = _to_gdsio_size(file_size)

    jobs_host_path = str(_gdsio_jobs_dir())

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
        f"--volume={jobs_host_path}:/jobs:ro",
    ]

    job_files = discover_gdsio_jobs()
    print(f"gdsio jobs dir: {_gdsio_jobs_dir()} (exists={_gdsio_jobs_dir().exists()})")
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
            vm.ssh_cmd(cmd, check=True, bypass=True)

            dest = str(copy_dest_dir / f"{date}-{job_name}-xfer{xfer_type}.txt")
            vm.ssh_cmd(["cp", result_path, dest], check=True, bypass=True)
            print(f"  result saved: {dest}")

    cleanup_encrypted_device(vm, "plain", dev_path)
