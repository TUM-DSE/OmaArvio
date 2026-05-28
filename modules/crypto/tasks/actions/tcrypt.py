#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""tcrypt kernel module action for crypto performance measurements."""

import json
import re
from pathlib import Path
from typing import List, Optional

from core.tasks.actions import ActionContext
from core.tasks.actions.registry import register_action


# Default test modes matching OpenSSL algorithms
# Format: (mode_number, description, parser_type)
# parser_type: "cipher" for encryption/decryption tests, "hash" for hash algorithms
DEFAULT_MODES = [
    (200, "AES (ecb, cbc, lrw, xts, cts, ctr)", "cipher"),
    (211, "AES-GCM (rfc4106, gcm)", "cipher"),
    (213, "ChaCha20-Poly1305 (rfc7539esp)", "cipher"),
    (214, "ChaCha20", "cipher"),
    (221, "AEGIS-128", "cipher"),
    (323, "SHA3-256", "hash"),
    (404, "SHA-256", "hash"),
]

RUNTIME = 10  # in seconds


def parse_tcrypt_hash_output(dmesg_output: str, mode: int) -> dict:
    """Parse tcrypt kernel module dmesg output for hash algorithms (SHA3, etc.).

    Hash algorithm output format:
        tcrypt: testing speed of async sha3-256 (sha3-256-generic)
        tcrypt: test  0 (   16 byte blocks,   16 bytes per update,   1 updates): 903452 opers/sec,  14455232 bytes/sec
        tcrypt: test  1 (   64 byte blocks,   16 bytes per update,   4 updates): 862240 opers/sec,  55183360 bytes/sec
        ...

    Args:
        dmesg_output: Raw dmesg output containing tcrypt results
        mode: Test mode number

    Returns:
        Dict with parsed test results
    """
    # Regex patterns for hash algorithms
    header_pattern = re.compile(
        r"tcrypt: testing speed of (sync |async )?(\S+) \((.+?)\)"
    )
    test_pattern = re.compile(
        r"tcrypt: test\s+(\d+) \(\s*(\d+) byte blocks,\s*(\d+) bytes per update,\s*(\d+) updates\):\s*"
        r"(\d+) opers/sec,\s*(\d+) bytes/sec"
    )

    tests = []
    current_algo = None
    current_impl = None
    current_sync_type = None

    for line in dmesg_output.split("\n"):
        # Check for header line
        header_match = header_pattern.search(line)
        if header_match:
            current_sync_type = (
                header_match.group(1).strip() if header_match.group(1) else None
            )
            current_algo = header_match.group(2)
            current_impl = header_match.group(3)
            continue

        # Check for test result line
        test_match = test_pattern.search(line)
        if test_match and current_algo:
            test_num = int(test_match.group(1))
            block_size = int(test_match.group(2))
            bytes_per_update = int(test_match.group(3))
            updates = int(test_match.group(4))
            opers_per_sec = int(test_match.group(5))
            bytes_per_sec = int(test_match.group(6))

            # Calculate throughput in MB/s
            throughput_mbps = bytes_per_sec / (1024 * 1024)

            test_result = {
                "algorithm": current_algo,
                "implementation": current_impl,
                "operation": "hash",
                "test_number": test_num,
                "block_size": block_size,
                "bytes_per_update": bytes_per_update,
                "updates": updates,
                "opers_per_sec": opers_per_sec,
                "throughput_bytes_per_sec": bytes_per_sec,
                "throughput_mbps": round(throughput_mbps, 2),
            }
            if current_sync_type:
                test_result["sync_type"] = current_sync_type
            tests.append(test_result)

    return {"mode": mode, "tests": tests}


def parse_tcrypt_dmesg_output(dmesg_output: str, mode: int) -> dict:
    """Parse tcrypt kernel module dmesg output into structured data.

    tcrypt output format:
        tcrypt: testing speed of [sync|async] aegis128 (aegis128-generic) encryption
        tcrypt: test 0 (128 bit key, 16 byte blocks): 4297609 operations in 5 seconds (68761744 bytes)
        tcrypt: test 1 (128 bit key, 64 byte blocks): 3783407 operations in 5 seconds (242138048 bytes)
        ...

    The sync/async prefix is optional and will be captured in the sync_type field if present.

    Args:
        dmesg_output: Raw dmesg output containing tcrypt results
        mode: Test mode number

    Returns:
        Dict with parsed test results including optional sync_type field
    """
    # Regex patterns
    header_pattern = re.compile(
        r"tcrypt: testing speed of (sync |async )?(\S+) \((.+?)\) (encryption|decryption)"
    )
    test_pattern = re.compile(
        r"tcrypt: test (\d+) \((\d+) bit key, (\d+) byte blocks\): "
        r"(\d+) operations in (\d+) seconds \((\d+) bytes\)"
    )

    tests = []
    current_algo = None
    current_impl = None
    current_op = None
    current_sync_type = None

    for line in dmesg_output.split("\n"):
        # Check for header line
        header_match = header_pattern.search(line)
        if header_match:
            current_sync_type = (
                header_match.group(1).strip() if header_match.group(1) else None
            )
            current_algo = header_match.group(2)
            current_impl = header_match.group(3)
            current_op = header_match.group(4)
            continue

        # Check for test result line
        test_match = test_pattern.search(line)
        if test_match and current_algo:
            test_num = int(test_match.group(1))
            key_size = int(test_match.group(2))
            block_size = int(test_match.group(3))
            operations = int(test_match.group(4))
            duration = int(test_match.group(5))
            total_bytes = int(test_match.group(6))

            # Calculate throughput
            throughput_bytes_per_sec = total_bytes / duration if duration > 0 else 0
            throughput_mbps = throughput_bytes_per_sec / (1024 * 1024)

            test_result = {
                "algorithm": current_algo,
                "implementation": current_impl,
                "operation": current_op,
                "test_number": test_num,
                "key_size": key_size,
                "block_size": block_size,
                "operations": operations,
                "duration": duration,
                "total_bytes": total_bytes,
                "throughput_bytes_per_sec": throughput_bytes_per_sec,
                "throughput_mbps": round(throughput_mbps, 2),
            }
            if current_sync_type:
                test_result["sync_type"] = current_sync_type
            tests.append(test_result)

    return {"mode": mode, "tests": tests}


@register_action("tcrypt")
def run_tcrypt_benchmark(
    ctx: ActionContext,
    modes: Optional[List[tuple]] = None,
) -> Path:
    """Run tcrypt kernel module benchmark on VM or host.

    Args:
        name: Benchmark configuration name (used in output path)
        vm: QemuVm or HostRunner instance to run the benchmark on
        timestamp: Timestamp for output file (auto-generated if not provided)
        modes: List of (mode_number, description, parser_type) tuples (default: DEFAULT_MODES)
               parser_type can be "cipher" or "hash"

    Returns:
        Path to the JSON output file

    Output structure:
        ./bench-result/tcrypt/{name}/{timestamp}.json
    """
    vm = ctx.vm
    date = ctx.timestamp
    outputdir_host = ctx.outputdir_host
    output_file = outputdir_host / f"{date}.json"

    # Use default modes if not specified
    if modes is None:
        modes = DEFAULT_MODES

    all_results = []

    # Run each test mode
    for mode_tuple in modes:
        # Support both old (mode_num, desc) and new (mode_num, desc, parser_type) formats
        if len(mode_tuple) == 3:
            mode_num, mode_desc, parser_type = mode_tuple
        else:
            mode_num, mode_desc = mode_tuple
            parser_type = "cipher"  # Default to cipher for backwards compatibility

        print(f"  Testing mode {mode_num}: {mode_desc}...")

        try:
            # Clear dmesg buffer (best effort, may require root)
            vm.ssh_cmd(["dmesg", "-C"], check=False)

            # Load tcrypt module with test parameters
            # Note: tcrypt runs benchmarks during init and fails after completion, so we ignore errors here
            # Expected error: "modprobe: ERROR: could not insert 'tcrypt': Resource temporarily unavailable"
            modprobe_cmd = ["modprobe", "tcrypt", f"mode={mode_num}", f"sec={RUNTIME}"]
            vm.ssh_cmd(modprobe_cmd, check=False)

            # Capture dmesg output
            dmesg_result = vm.ssh_cmd(["dmesg"], check=True)
            dmesg_output = dmesg_result.stdout

            # Parse results using the specified parser type
            if parser_type == "hash":
                parsed = parse_tcrypt_hash_output(dmesg_output, mode_num)
            else:
                parsed = parse_tcrypt_dmesg_output(dmesg_output, mode_num)

            parsed["description"] = mode_desc

            if parsed["tests"]:
                all_results.append(parsed)
            else:
                print(f"    Warning: No test results found for mode {mode_num}")

        except Exception as e:
            print(f"    Warning: Failed to run mode {mode_num}: {e}")
            # Continue with other modes

    # Build output data
    output_data = {
        "results": all_results,
        "_metadata": {
            "name": ctx.name,
            "timestamp": date,
            "modes": [m[0] for m in modes],  # Extract mode numbers
            "duration_per_test": RUNTIME,
        },
    }

    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Results saved to: {output_file}")
    return output_file
