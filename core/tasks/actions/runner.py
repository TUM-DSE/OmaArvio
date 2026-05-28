#!/usr/bin/env python3
"""Benchmark action execution and monitoring orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from core.tasks.actions.registry import ACTIONS, ActionContext
from core.tasks.qemu import HostRunner, spawn_host_runner, spawn_qemu
from core.tasks.utils.monitoring import monitor_with_perf_kvm, monitor_with_sar
from core.tasks.utils.utils import get_benchmark_output_path


def _configure_vfio_trace(config: dict, outputdir_host: Path, timestamp: str) -> None:
    if not config.get("vfio_trace", False):
        return
    if config.get("vfio_trace_file") is None:
        trace_filename = config.get(
            "vfio_trace_filename", f"{timestamp}_vfio_trace.log"
        )
        vfio_trace_file = outputdir_host / trace_filename
        config["vfio_trace_file"] = str(vfio_trace_file)
        print(f"VFIO tracing enabled -> {vfio_trace_file}")
    else:
        print(f"VFIO tracing enabled -> {config['vfio_trace_file']}")


def run_benchmark_action(action_type: str, **kwargs: Any) -> None:
    config = kwargs["config"]
    action_config = config.get("action_config") or {}
    is_host = config.get("type") == "host"
    name = kwargs["name"]
    resource = config["resource"]
    pin_base = config.get("pin_base", resource.pin_base)
    qemu_cmd = kwargs.get("qemu_cmd")
    pin = kwargs.get("pin", True)
    timestamp = config.get("action_timestamp") or datetime.now().strftime(
        "%Y-%m-%d-%H-%M-%S"
    )

    if action_type not in ACTIONS:
        raise ValueError(
            f"Unknown benchmark action type: {action_type}. "
            f"Available: {list(ACTIONS.keys())}"
        )

    action = ACTIONS[action_type]
    outputdir_host, outputdir_guest, _ = get_benchmark_output_path(
        *action.path_fn(name, action_config),
        timestamp=timestamp,
        create_dirs=True,
    )
    _configure_vfio_trace(config, outputdir_host, timestamp)

    spawn_runner = (
        spawn_host_runner(config=config)
        if is_host
        else spawn_qemu(qemu_cmd, numa_node=resource.numa_node, config=config)
    )

    with spawn_runner as runner:
        if pin:
            runner.pin_vcpu(pin_base)

        runner.wait_for_ssh()

        action_ctx = ActionContext(
            name=name,
            vm=runner,
            timestamp=timestamp,
            outputdir_host=outputdir_host,
            outputdir_guest=outputdir_guest,
            is_host=is_host,
            config=config,
            action_config=action_config,
        )

        with monitor_with_sar(runner, outputdir_host, timestamp, config):
            if action_ctx.is_host:
                action.fn(ctx=action_ctx, **action_config)
            else:
                with monitor_with_perf_kvm(
                    outputdir_host, timestamp, config, vm=runner
                ):
                    action.fn(ctx=action_ctx, **action_config)

        runner.shutdown()
