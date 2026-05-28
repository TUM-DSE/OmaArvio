#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import functools
import json
from pathlib import Path
from typing import Any, Optional
import shutil

from invoke import task

from core.tasks.config import PROJECT_ROOT, BUILD_DIR, LINUX_DIR
from core.tasks.procs import run


@functools.lru_cache(maxsize=None)
def nix_build(what: str, build_dir: Path = BUILD_DIR) -> Any:
    """Run nix build and return the result as json."""
    build_dir.mkdir(parents=True, exist_ok=True)
    outpath = build_dir / what.lstrip(".#")
    cmd = ["nix", "build", "--out-link", str(outpath), "--json", what]
    result = run(cmd, cwd=PROJECT_ROOT)
    return json.loads(result.stdout)


@task
def build_ovmf_snp(c: Any) -> None:
    """Build OVMF with AMD SEV-SNP support.

    Output path is ./build/ovmf-amd-sev-snp-fd
    """
    nix_build(".#ovmf-amd-sev-snp")


@task
def build_ovmf_upstream(c: Any) -> None:
    """Build upstream OVMF (from nixpkgs).

    Output path is ./build/ovmf-upstream
    """
    nix_build(".#ovmf-upstream")


@task
def build_qemu_upstream(_c: Any) -> None:
    """Build upstream QEMU.

    Output path is ./build/qemu-upstream
    """
    nix_build(".#qemu-upstream")


@task
def build_qemu_amd(_c: Any) -> None:
    """Build AMDs QEMU fork for certificate enablement required for attestation.
    Currently device passthrough is broken on that version.

    Output path is ./build/qemu-amd
    """
    nix_build(".#qemu-amd")


def build_guest_image(
    c: Any, target: str, force: bool = False, dst: Path = Path(f"{BUILD_DIR}/image")
) -> None:
    guest_image_name = f"{target}.raw"
    result = nix_build(f".#{target}")

    if dst:
        dst.mkdir(parents=True, exist_ok=True)
        dst_file = dst / guest_image_name
        if not force and dst_file.exists():
            print(f"{dst_file} already exists. Skipping build.")
            return

        script = result[0]["outputs"]["out"]
        run([script], cwd=dst)
        shutil.move(dst / "main.raw", dst_file)


@task
def build_snp_guest_image(c: Any, force: bool = False) -> None:
    """Build a guest image with AMD SEV-SNP support.

    Output path is ./build/snp-guest-image

    The build result is a read-only. Copy the image to
    ./build/image/snp-guest-image.qcow2
    """

    build_guest_image(c, "snp-guest-image", force=force)


@task
def build_direct_guest_image(c: Any, force: bool = False) -> None:
    """Build a guest image for direct kernel boot (no bootloader).

    Output path is ./build/direct-guest-image

    The build result is a read-only. Copy the image to
    ./build/image/direct-guest-image.qcow2

    This image is optimized for use with QEMU's -kernel option and doesn't
    include a bootloader, making it smaller and faster to build.
    """

    build_guest_image(c, "direct-guest-image", force=force)


@task
def build_kernel(
    c: Any,
    config: Optional[str] = None,
    output_dir: Optional[str] = None,
    jobs: int = 0,
    target: str = "all",
) -> None:
    """Build Linux kernel from LINUX_DIR.

    Args:
        config: Path to kernel config file (relative to PROJECT_ROOT)
        output_dir: Output directory (defaults to LINUX_DIR/build)
        jobs: Number of parallel jobs (0 for auto-detect with nproc)
        target: Make target (default: all, e.g., bzImage, modules, etc.)

    Examples:
        inv build.build-kernel
        inv build.build-kernel --config config/snp-kernel.config
        inv build.build-kernel --config config/snp-kernel.config --jobs 8
        inv build.build-kernel --target bzImage
    """
    # Check if LINUX_DIR exists
    if not LINUX_DIR.exists():
        raise FileNotFoundError(f"LINUX_DIR does not exist: {LINUX_DIR}")

    # Set output directory
    if output_dir:
        out_path = Path(output_dir).resolve()
    else:
        out_path = LINUX_DIR / "build"

    # Create output directory
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"Building kernel from: {LINUX_DIR}")
    print(f"Output directory: {out_path}")

    # Copy config if specified
    if config:
        config_path = PROJECT_ROOT / config
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        dest_config = out_path / ".config"
        print(f"Copying config: {config_path} -> {dest_config}")
        shutil.copy2(config_path, dest_config)

        # Run olddefconfig to update config with defaults for new options
        print("Running make olddefconfig...")
        cmd = [
            "nix",
            "develop",
            f"{PROJECT_ROOT}#kernel-build",
            "--command",
            "make",
            f"O={out_path}",
            "olddefconfig",
        ]
        run(cmd, cwd=LINUX_DIR, stdout=None, stderr=None)

    # Determine number of jobs
    if jobs == 0:
        # Auto-detect using nproc
        import subprocess

        result = subprocess.run(["nproc"], capture_output=True, text=True)
        jobs = int(result.stdout.strip())

    # Build kernel
    print(f"Building kernel target '{target}' with {jobs} parallel jobs...")
    cmd = [
        "nix",
        "develop",
        f"{PROJECT_ROOT}#kernel-build",
        "--command",
        "make",
        f"O={out_path}",
        f"-j{jobs}",
        target,
    ]
    run(cmd, cwd=LINUX_DIR, stdout=None, stderr=None)

    print(f"\nKernel build complete!")
    print(f"Output directory: {out_path}")


@task
def clone_kernel(c: Any, branch: str = "master", force: bool = False) -> None:
    """Clone the Linux kernel repository and checkout a specific branch.

    Args:
        branch: Git branch or tag to checkout (default: master)
        force: Force re-clone even if directory exists

    Examples:
        inv build.clone-kernel --branch v6.17
        inv build.clone-kernel --branch sev-snp-devel
        inv build.clone-kernel --force
    """
    if LINUX_DIR.exists():
        if not force:
            print(f"LINUX_DIR already exists: {LINUX_DIR}")
            print("Use --force to re-clone")
            return
        else:
            print(f"Removing existing LINUX_DIR: {LINUX_DIR}")
            shutil.rmtree(LINUX_DIR)

    # Clone the kernel
    kernel_repo = "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"
    print(f"Cloning Linux kernel from: {kernel_repo}")
    print(f"Destination: {LINUX_DIR}")

    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        kernel_repo,
        str(LINUX_DIR),
    ]
    run(cmd, cwd=PROJECT_ROOT.parent, stdout=None, stderr=None)

    print(f"\nKernel cloned successfully!")
    print(f"Branch: {branch}")
    print(f"Location: {LINUX_DIR}")


@task
def clean_kernel(c: Any, build_only: bool = False) -> None:
    """Clean the kernel directory.

    Args:
        build_only: Only clean the build directory, keep source

    Examples:
        inv build.clean-kernel
        inv build.clean-kernel --build-only
    """
    if not LINUX_DIR.exists():
        print(f"LINUX_DIR does not exist: {LINUX_DIR}")
        return

    if build_only:
        # Only clean build directory
        build_path = LINUX_DIR / "build"
        if build_path.exists():
            print(f"Cleaning build directory: {build_path}")
            shutil.rmtree(build_path)
            print("Build directory cleaned!")
        else:
            print(f"Build directory does not exist: {build_path}")
    else:
        # Run make clean in kernel directory
        print(f"Cleaning kernel directory: {LINUX_DIR}")
        cmd = ["make", "clean"]
        run(cmd, cwd=LINUX_DIR, stdout=None, stderr=None)

        # Also remove build directory if it exists
        build_path = LINUX_DIR / "build"
        if build_path.exists():
            print(f"Removing build directory: {build_path}")
            shutil.rmtree(build_path)

        print("Kernel directory cleaned!")


@task
def all(c: Any, force: bool = False) -> None:
    """Build all components needed for benchmarking: OVMF, QEMU, SNP guest image"""
    build_ovmf_upstream(c)
    build_qemu_upstream(c)
    build_qemu_amd(c)
    build_snp_guest_image(c, force=force)
