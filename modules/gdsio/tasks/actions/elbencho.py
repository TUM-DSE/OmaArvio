#!/usr/bin/env python3
from pathlib import Path
from typing import Optional

from core.tasks.actions import ActionContext
from core.tasks.qemu import QemuVm, HostRunner
from core.tasks.utils.utils import parse_size_to_mb
from core.tasks.actions.registry import register_action
from modules.nvme.tasks.utils.storage import (
    format_plain_device,
    cleanup_encrypted_device,
)

ELBENCHO_IMAGE = "breuner/elbencho:master-ubuntu-cuda-multiarch"
ELBENCHO_JOBS_DIR = Path("/shared/modules/gdsio/elbencho")
ELBENCHO_MOUNT = "/mnt/encrypted"


def discover_elbencho_jobs() -> list:
    """Return sorted list of *.elbencho job config files."""
    return sorted(ELBENCHO_JOBS_DIR.glob("*.elbencho"))


# Elbencho flags that are boolean (no value argument on CLI).
# All other key=value entries are passed as --key value.
_ELBENCHO_BOOL_FLAGS = {
    "read",
    "write",
    "rand",
    "lat",
    "direct",
    "trunc",
    "mkdirs",
    "deldirs",
    "delfiles",
    "nolive",
    "nodialog",
}


def _parse_job_file(job_file: Path) -> list:
    """Parse a native .elbencho config file into a list of CLI arguments.

    Format: key=value per line, # comments ignored.
    Known boolean flags with value=1 become --flag; all others become --key value.
    """
    args = []
    for line in job_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key in _ELBENCHO_BOOL_FLAGS and value == "1":
            args.append(f"--{key}")
        else:
            args.extend([f"--{key}", value])
    return args


def _elbencho_path(name: str, config: dict) -> tuple:
    return ("elbencho", name)


@register_action("elbencho", path_fn=_elbencho_path)
def run_elbencho(
    ctx: ActionContext,
    dev_path: str = None,
    file_size: str = "2t",
    gds: bool = False,
    **kwargs,
):
    """Run elbencho benchmark on an ext4 test file.

    Sets up an ext4 partition on dev_path, creates a test file, then runs
    each job from elbencho/jobs/*.elbencho via Docker. Results are saved
    as JSON in the benchmark output directory.
    """
    if not dev_path:
        raise ValueError("dev_path is required for elbencho benchmark")

    vm = ctx.vm
    outputdir_host = ctx.outputdir_host
    outputdir_guest = ctx.outputdir_guest
    date = ctx.timestamp

    # 1. Format ext4 and create test file at /mnt/encrypted/testfile
    # Partition is 10% larger than the testfile to accommodate filesystem overhead.
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

    # 2. Build Docker base flags
    docker_flags = [
        "--rm",
        "--privileged",
        f"--volume={ELBENCHO_MOUNT}:/data",
        "--volume=/tmp:/tmp",
    ]
    if gds:
        docker_flags += [
            "--device=nvidia.com/gpu=all",
            "--volume",
            "/run/udev:/run/udev:ro",
            "--ipc=host",
            "--env=CUFILE_USE_PCIP2PDMA=true",
            "--env=CUFILE_ALLOW_COMPAT_MODE=false",
        ]

    gds_extra = ["--gpuids", "all", "--gds"] if gds else []

    # 3. Run each job
    job_files = discover_elbencho_jobs()
    print(
        f"Elbencho jobs dir: {ELBENCHO_JOBS_DIR} (exists={ELBENCHO_JOBS_DIR.exists()})"
    )
    print(f"Elbencho jobs found: {[j.name for j in job_files]}")
    print(f"Output dir (host): {outputdir_host}")
    print(f"Output dir (guest): {outputdir_guest}")

    # On the host runner /share is only mounted inside systemd-run, not under bypass=True.
    # Use the real host path for copy; in the VM use the /share guest path.
    is_host = ctx.is_host
    copy_dest_dir = outputdir_host if is_host else outputdir_guest

    for job_file in job_files:
        job_name = job_file.stem
        result_path = f"/tmp/elbencho-{job_name}-{date}.json"

        job_args = _parse_job_file(job_file)

        # cuFile does not support IO depth > 1; clamp iodepth to 1 for GDS runs.
        if gds and "--iodepth" in job_args:
            idx = job_args.index("--iodepth")
            job_args[idx + 1] = "1"

        cmd = (
            ["docker", "run"]
            + docker_flags
            + [ELBENCHO_IMAGE]
            + job_args
            + ["--size", file_size]
            + gds_extra
            + ["--jsonfile", result_path, "/data/testfile"]
        )

        print(f"Running elbencho job: {job_name}")
        print(f"  cmd: {' '.join(cmd)}")
        vm.ssh_cmd(cmd, check=True, bypass=True)

        dest = str(copy_dest_dir / f"{date}-{job_name}.json")
        print(f"  copying result {result_path} -> {dest}")
        vm.ssh_cmd(["cp", result_path, dest], check=True, bypass=True)
        print(f"  result saved: {dest}")

    # 4. Cleanup
    cleanup_encrypted_device(vm, "plain", dev_path)
