#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""nvbandwidth action for GPU bandwidth measurements."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from core.tasks.config import PROJECT_ROOT
from core.tasks.qemu import QemuVm, HostRunner
from core.tasks.utils.utils import get_benchmark_output_path
from core.tasks.actions.registry import register_action


# nvbandwidth is expected to be available in PATH (from nix devShell / guest image)
NVBANDWIDTH_CMD = "nvbandwidth"

# Default testcases for simplified benchmark
DEFAULT_TESTCASES = ["host_to_device_memcpy_ce", "device_to_host_memcpy_ce"]

# Default buffer sizes: 64 MiB to 4 GiB in powers of 2, values in MiB
# fmt: off
DEFAULT_BUFFER_SIZES = [
    64,    # 64 MiB
    128,   # 128 MiB
    256,   # 256 MiB
    512,   # 512 MiB
    1024,  # 1 GiB
    2048,  # 2 GiB
    4096,  # 4 GiB
]
# fmt: on


@register_action("nvbandwidth")
def run_nvbandwidth(
    name: str,
    vm: Union[QemuVm, HostRunner],
    timestamp: Optional[str] = None,
    verbose: bool = False,
    buffer_sizes: Optional[List[int]] = None,
) -> Path:
    """Run nvbandwidth GPU bandwidth benchmark on VM or host.

    Runs host_to_device_memcpy_ce and device_to_host_memcpy_ce testcases
    with buffer sizes from 512 MiB to 4 GiB (powers of 2).

    Args:
        name: Benchmark configuration name (used in output path)
        vm: QemuVm or HostRunner instance to run the benchmark on
        timestamp: Timestamp for output file (auto-generated if not provided)
        verbose: Enable verbose output

    Returns:
        Path to the JSON output file

    Output structure:
        ./bench-result/nvbandwidth/{name}/{timestamp}.json
    """
    # Use provided timestamp, or generate new one if not provided
    outputdir_host, outputdir_guest, date = get_benchmark_output_path(
        "nvbandwidth", name, timestamp=timestamp
    )
    output_file = outputdir_host / f"{date}.json"

    # Always use hardcoded testcases, allow buffer size override
    testcases = DEFAULT_TESTCASES
    sizes_to_run = buffer_sizes if buffer_sizes else DEFAULT_BUFFER_SIZES

    print(f"Running testcases: {testcases}")
    print(f"Buffer size sweep: {sizes_to_run} MiB")

    all_results = []

    for current_size in sizes_to_run:
        # Build command
        cmd = [NVBANDWIDTH_CMD, "--json"]
        cmd.extend(["--bufferSize", str(current_size)])

        for tc in testcases:
            cmd.extend(["--testcase", tc])

        if verbose:
            cmd.append("--verbose")

        # Run nvbandwidth via SSH on the VM/host runner
        print(f"Running nvbandwidth with buffer_size={current_size} MiB...")
        try:
            result = vm.ssh_cmd(cmd, check=True)

            # Parse output
            try:
                output_data = json.loads(result.stdout)
                # Add metadata for this run
                output_data["_metadata"] = {
                    "name": name,
                    "timestamp": date,
                    "buffer_size_mib": current_size,
                    "testcases": testcases,
                }
                all_results.append(output_data)
            except json.JSONDecodeError:
                # If JSON parsing fails, save raw output
                print(f"Error parsing JSON for buffer_size={current_size}")
                all_results.append(
                    {
                        "raw_output": result.stdout,
                        "_metadata": {
                            "name": name,
                            "timestamp": date,
                            "buffer_size_mib": current_size,
                            "parse_error": True,
                        },
                    }
                )
        except Exception as e:
            print(f"Error running nvbandwidth for buffer_size={current_size}: {e}")
            all_results.append(
                {
                    "error": str(e),
                    "_metadata": {
                        "name": name,
                        "timestamp": date,
                        "buffer_size_mib": current_size,
                        "execution_error": True,
                    },
                }
            )

    # Save aggregated results
    final_output = {
        "results": all_results,
        "suite_metadata": {
            "name": name,
            "timestamp": date,
            "total_runs": len(all_results),
        },
    }

    with open(output_file, "w") as f:
        json.dump(final_output, f, indent=2)

    print(f"Results saved to: {output_file}")
    return output_file
