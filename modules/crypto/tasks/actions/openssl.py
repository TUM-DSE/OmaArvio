#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""OpenSSL speed action for crypto performance measurements."""

import json
from pathlib import Path
from typing import List, Optional

from core.tasks.actions import ActionContext
from core.tasks.actions.registry import register_action


# Default algorithms from the reference script + AEGIS variants
DEFAULT_ALGORITHMS = [
    "aes-256-gcm",
    "aes-256-xts",
    "chacha20",
    "chacha20-poly1305",
    "sha3-256",
    "sha-256",
    "aegis-128l",
    "aegis-128x2",
    "aegis-128x4",
]

DEFAULT_BLOCK_SIZE = 4096  # 4KB block size for testing

RUNTIME = 10


def parse_openssl_speed_output(output: str, algorithm: str, block_size: int) -> dict:
    """Parse openssl speed -mr output into structured data.

    OpenSSL speed -mr output format (machine-readable):
    +DT:AES-256-GCM:3:8192
    +R:1493953:AES-256-GCM:3.000000
    +H:8192
    +F:25:AES-256-GCM:4079487658.67

    Args:
        output: Raw stdout from openssl speed -mr command
        algorithm: Algorithm name being tested (from caller)
        block_size: Block size used (from caller)

    Returns:
        Dict with algorithm, block_size, throughput, and raw output
    """
    throughput = None

    # Extract throughput from +F:25: line
    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("+F:"):
            parts = line.split(":")
            if len(parts) >= 4:
                throughput = float(parts[3])
                break

    if throughput is None:
        raise ValueError(f"No throughput found in output for {algorithm}")

    return {
        "algorithm": algorithm,
        "block_size": block_size,
        "throughput_bytes_per_sec": throughput,
        "throughput_gb_per_sec": round(throughput / (1024**3), 2),
        "raw_output": output,
    }


@register_action("openssl")
def run_openssl_speed(
    ctx: ActionContext,
    algorithms: Optional[List[str]] = None,
    block_size: int = DEFAULT_BLOCK_SIZE,
    cpu_pin: int = 0,
) -> Path:
    """Run OpenSSL speed benchmark on VM or host.

    Args:
        name: Benchmark configuration name (used in output path)
        vm: QemuVm or HostRunner instance to run the benchmark on
        timestamp: Timestamp for output file (auto-generated if not provided)
        algorithms: List of algorithms to test (default: DEFAULT_ALGORITHMS)
        block_size: Block size in bytes for testing (default: 8192)
        cpu_pin: CPU core to pin to (default: 0)

    Returns:
        Path to the JSON output file

    Output structure:
        ./bench-result/openssl/{name}/{timestamp}.json
    """
    vm = ctx.vm
    date = ctx.timestamp
    outputdir_host = ctx.outputdir_host
    output_file = outputdir_host / f"{date}.json"

    # Use default algorithms if not specified
    if algorithms is None:
        algorithms = DEFAULT_ALGORITHMS

    all_results = []
    failed = {}

    # Run each algorithm
    for alg_entry in algorithms:
        # openssl-aegis, not openssl: the bare name may resolve to a stock
        # build, which has no AEGIS EVP algorithms.
        cmd = [
            "taskset",
            "-c",
            str(cpu_pin),
            "openssl-aegis",
            "speed",
            "-mr",
            "-elapsed",
            "-evp",
            alg_entry,
            "-seconds",
            str(RUNTIME),
            "-mlock",
            "-bytes",
            str(block_size),
        ]
        try:
            result = vm.ssh_cmd(cmd, check=True)
            parsed = parse_openssl_speed_output(result.stdout, alg_entry, block_size)
            all_results.append(parsed)
        except Exception as e:
            print(f"    Warning: Failed to run {alg_entry}: {e}")
            failed[alg_entry] = str(e)
            # Continue with other algorithms

    # Name the failures: absent from "results" alone reads as never requested.
    output_data = {
        "results": all_results,
        "_metadata": {
            "name": ctx.name,
            "timestamp": date,
            "algorithms": algorithms,
            "failed_algorithms": failed,
            "block_size": block_size,
            "cpu_pin": cpu_pin,
        },
    }

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Results saved to: {output_file}")
    return output_file
