#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from invoke import task
import subprocess
import time
import os
import sys


ITERATION_PRINT_INTERVAL = 1000


def setup_spdk(pci_ids, silent=False):
    """Bind devices to vfio-pci using spdk-setup."""
    if not silent:
        print("Running spdk-setup...")
    env = os.environ.copy()
    for pci_id in pci_ids:
        if not silent:
            print(f"  Setting up {pci_id}...")
        env["PCI_ALLOWED"] = pci_id
        try:
            subprocess.run(
                ["spdk-setup"],
                check=True,
                env=env,
                stdout=subprocess.DEVNULL if silent else None,
                stderr=subprocess.DEVNULL if silent else None,
            )
        except subprocess.CalledProcessError as e:
            if not silent:
                print(f"Error running spdk-setup for {pci_id}: {e}")
            raise


def reset_spdk(pci_ids, silent=False):
    """Unbind devices and reset drivers using spdk-setup."""
    if not silent:
        print("\nCleaning up...")
    env = os.environ.copy()
    for pci_id in pci_ids:
        if not silent:
            print(f"  Resetting {pci_id}...")
        env["PCI_ALLOWED"] = pci_id
        try:
            subprocess.run(
                ["spdk-setup", "reset"],
                check=False,
                env=env,
                stdout=subprocess.DEVNULL if silent else None,
                stderr=subprocess.DEVNULL if silent else None,
            )
        except Exception as e:
            if not silent:
                print(f"Error executing spdk-setup reset for {pci_id}: {e}")


def get_link_speeds(pci_ids):
    """Read current link speeds for the given PCI IDs."""
    speeds = {}
    driver_path_template = "/sys/bus/pci/drivers/vfio-pci/{}/current_link_speed"

    for pci_id in pci_ids:
        link_speed_path = driver_path_template.format(pci_id)
        if not os.path.exists(link_speed_path):
            raise RuntimeError(
                f"Device {pci_id} is not bound to vfio-pci or does not support link check. "
                f"Path: {link_speed_path}"
            )

        try:
            with open(link_speed_path, "r") as f:
                speeds[pci_id] = f.read().strip()
        except Exception as e:
            raise RuntimeError(f"Error reading speed for {pci_id}: {e}")

    return speeds


def verify_link_speeds(pci_ids, initial_speeds):
    """Verify that current link speeds match initial speeds."""
    current_speeds = get_link_speeds(pci_ids)
    for pci_id in pci_ids:
        if current_speeds[pci_id] != initial_speeds[pci_id]:
            print(f"\n\nFATAL: Link speed degradation detected for {pci_id}!")
            print(f"  Initial: {initial_speeds[pci_id]}")
            print(f"  Current: {current_speeds[pci_id]}")
            sys.exit(1)


@task
def link_reset(ctx, pci_ids, rebind=False):
    """Stress test PCI link stability by repeatedly resetting devices.

    Args:
        pci_ids: Comma-separated list of PCI IDs (e.g., "0000:01:00.0,0000:02:00.0")
        rebind: If True, run spdk-setup reset/setup on each iteration (default: False)
    """
    ids = [pci_id.strip() for pci_id in pci_ids.split(",")]

    print(f"Starting PCI link stress test for IDs: {ids}")

    try:
        setup_spdk(ids)
    except subprocess.CalledProcessError:
        return

    try:
        print("Verifying initial link speeds...")
        initial_speeds = get_link_speeds(ids)
        for pci_id, speed in initial_speeds.items():
            print(f"  {pci_id}: {speed}")
    except Exception as e:
        print(f"Error checking initial speeds: {e}")
        reset_spdk(ids)
        return

    iteration = 0
    reset_path_template = "/sys/bus/pci/drivers/vfio-pci/{}/reset"

    try:
        while True:
            iteration += 1
            if iteration % ITERATION_PRINT_INTERVAL == 0:
                print(f"Iteration {iteration}...")

            if rebind:
                reset_spdk(ids, silent=True)
                setup_spdk(ids, silent=True)

            for pci_id in ids:
                reset_path = reset_path_template.format(pci_id)
                # Reset device
                try:
                    with open(reset_path, "w") as f:
                        f.write("1")
                except Exception as e:
                    print(f"\nError resetting {pci_id}: {e}")
                    return

            try:
                verify_link_speeds(ids, initial_speeds)
            except Exception as e:
                print(f"\nError verifying speeds: {e}")
                return

    except KeyboardInterrupt:
        print("\n\nStress test stopped by user.")
    finally:
        reset_spdk(ids)


@task
def nvme_perf(ctx, pci_ids, rebind=False):
    """Stress test NVMe devices using spdk_nvme_perf.

    Args:
        pci_ids: Comma-separated list of PCI IDs (e.g., "0000:01:00.0,0000:02:00.0")
        rebind: If True, run spdk-setup reset/setup on each iteration (default: False)
    """
    ids = [pci_id.strip() for pci_id in pci_ids.split(",")]

    print(f"Starting SPDK NVMe perf stress test for IDs: {ids}")

    try:
        setup_spdk(ids)
    except subprocess.CalledProcessError:
        return

    try:
        print("Verifying initial link speeds...")
        initial_speeds = get_link_speeds(ids)
        for pci_id, speed in initial_speeds.items():
            print(f"  {pci_id}: {speed}")
    except Exception as e:
        print(f"Error checking initial speeds: {e}")
        reset_spdk(ids)
        return

    cmd_base = [
        "spdk_nvme_perf",
        "-o",
        "128k",
        "-w",
        "randwrite",
        "-q",
        "128",
        "-c",
        "0xf",
        "-t",
        "5",
    ]
    for pci_id in ids:
        cmd_base.extend(["-r", f"trtype:PCIe traddr:{pci_id}"])

    iteration = 0
    try:
        while True:
            iteration += 1
            if iteration % ITERATION_PRINT_INTERVAL == 0:
                print(f"Iteration {iteration}...")

            if rebind:
                reset_spdk(ids, silent=True)
                setup_spdk(ids, silent=True)

            try:
                subprocess.run(cmd_base, check=True)
            except subprocess.CalledProcessError as e:
                print(f"\nError running spdk_nvme_perf: {e}")
                return

            try:
                verify_link_speeds(ids, initial_speeds)
            except (
                RuntimeError
            ) as e:  # verify_link_speeds might raise RuntimeError from get_link_speeds
                print(f"\nError verifying speeds: {e}")
                return

    except KeyboardInterrupt:
        print("\n\nStress test stopped by user.")
    finally:
        reset_spdk(ids)
