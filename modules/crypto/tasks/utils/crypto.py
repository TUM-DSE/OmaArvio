#!/usr/bin/env python3
"""Crypto benchmark utility functions for parsing and analyzing benchmark results."""

import json
from pathlib import Path
from typing import Dict, List, Optional


# Algorithm configuration for crypto benchmarks
ALGORITHMS = [
    # Algorithms with both kernel and openssl
    {
        "name": "aes-256-gcm",
        "tcrypt": ["gcm(aes)", "rfc4106(gcm(aes))"],  # Pick fastest
        "openssl": "aes-256-gcm",
    },
    {
        "name": "aes-256-xts",
        "tcrypt": ["xts(aes)"],
        "openssl": "aes-256-xts",
    },
    {
        "name": "chacha20",
        "tcrypt": ["chacha20"],
        "openssl": "chacha20",
    },
    {
        "name": "chacha20-poly1305",
        "tcrypt": ["rfc7539esp(chacha20,poly1305)"],
        "openssl": "chacha20-poly1305",
    },
    {
        "name": "sha3-256",
        "tcrypt": ["sha3-256"],
        "openssl": "sha3-256",
    },
    {
        "name": "sha-256",
        "tcrypt": ["sha256"],
        "openssl": "sha-256",
    },
    # Kernel-only algorithms
    {
        "name": "aegis128",
        "tcrypt": ["aegis128"],
        "openssl": None,
    },
    # OpenSSL-only algorithms
    {
        "name": "aegis-128l",
        "tcrypt": None,
        "openssl": "aegis-128l",
    },
    {
        "name": "aegis-128x2",
        "tcrypt": None,
        "openssl": "aegis-128x2",
    },
    {
        "name": "aegis-128x4",
        "tcrypt": None,
        "openssl": "aegis-128x4",
    },
]


def find_latest_json(
    base_path: Path, setup: str, benchmark_type: str
) -> Optional[Path]:
    """Find the latest JSON file for a given setup and benchmark type.

    Args:
        base_path: Base directory (e.g., PROJECT_ROOT / "bench-result")
        setup: VM setup type (e.g., "amd-disk-medium", "snp-disk-medium", "host-medium")
        benchmark_type: Benchmark type ("tcrypt" or "openssl")

    Returns:
        Path to latest JSON file, or None if not found
    """
    search_dir = base_path / benchmark_type / setup
    if not search_dir.exists():
        return None

    json_files = list(search_dir.glob("*.json"))
    if not json_files:
        return None

    # Sort by modification time, return latest
    return max(json_files, key=lambda p: p.stat().st_mtime)


def parse_openssl_results(json_path: Path, algorithms: List[str]) -> Dict[str, float]:
    """Parse OpenSSL results and extract throughput for specified algorithms.

    Args:
        json_path: Path to OpenSSL JSON results file
        algorithms: List of algorithm names to extract

    Returns:
        Dict mapping algorithm name to throughput in GiB/s
    """
    if not json_path or not json_path.exists():
        return {}

    with open(json_path) as f:
        data = json.load(f)

    results = {}
    for result in data.get("results", []):
        algo = result.get("algorithm")
        if algo in algorithms and result.get("block_size") == 4096:
            # The OpenSSL results already carry throughput in GiB/s
            results[algo] = result.get("throughput_gb_per_sec", 0.0)

    return results


def parse_tcrypt_results(
    json_path: Path, algorithm_specs: List[Dict]
) -> Dict[str, float]:
    """Parse tcrypt results and extract throughput for specified algorithms.

    Args:
        json_path: Path to tcrypt JSON results file
        algorithm_specs: List of algorithm specs from ALGORITHMS config

    Returns:
        Dict mapping tcrypt algorithm name to throughput in GiB/s
    """
    if not json_path or not json_path.exists():
        return {}

    with open(json_path) as f:
        data = json.load(f)

    results = {}

    # Build a mapping of tcrypt algorithm names to search for
    tcrypt_algos = {}
    for spec in algorithm_specs:
        if spec.get("tcrypt"):
            for tcrypt_name in spec["tcrypt"]:
                tcrypt_algos[tcrypt_name] = spec["name"]

    # Parse all test results
    for mode_result in data.get("results", []):
        for test in mode_result.get("tests", []):
            algo = test.get("algorithm")
            if algo not in tcrypt_algos:
                continue

            # Check block size
            if test.get("block_size") != 4096:
                continue

            # For encryption algorithms: filter for "encryption" operation and 256-bit key
            operation = test.get("operation")
            if operation == "encryption":
                # Check for 256-bit key (only for AES algorithms)
                if "aes" in algo:
                    if test.get("key_size") != 256:
                        continue
            elif operation == "hash":
                # For hash algorithms: check bytes_per_update == 4096
                if test.get("bytes_per_update") != 4096:
                    continue
            else:
                # Skip decryption or other operations
                continue

            # Convert throughput from MiB/s to GiB/s
            throughput_mibs = test.get("throughput_mbps", 0.0)
            throughput_gibs = throughput_mibs / 1024.0

            # Keep the best (highest) throughput for this algorithm
            if algo not in results or throughput_gibs > results[algo]:
                results[algo] = throughput_gibs

    return results
