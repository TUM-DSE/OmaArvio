#!/usr/bin/env python3
"""Benchmark action execution and monitoring orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from core.tasks.actions.registry import Action, ActionContext, get_action
from core.tasks.qemu import spawn_runner
from core.tasks.utils.monitoring import monitor_with_perf_kvm, monitor_with_sar
from core.tasks.utils.utils import get_benchmark_output_path

ACTION_TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M-%S"


def ensure_action_timestamp(config: dict) -> str:
    timestamp = config.get("action_timestamp")
    if timestamp is None:
        timestamp = datetime.now().strftime(ACTION_TIMESTAMP_FORMAT)
        config["action_timestamp"] = timestamp
    return timestamp


def prepare_action_output(
    action_type: str,
    name: str,
    config: dict,
    *,
    create_dirs: bool = True,
) -> tuple[Action, Path, Path, str]:
    action = get_action(action_type)
    action_config = config.get("action_config") or {}
    timestamp = ensure_action_timestamp(config)
    outputdir_host, outputdir_guest, _ = get_benchmark_output_path(
        *action.path_fn(name, action_config),
        timestamp=timestamp,
        create_dirs=create_dirs,
    )
    return action, outputdir_host, outputdir_guest, timestamp


def configure_vfio_trace(
    config: dict,
    vm_name: str,
    action_name: str,
) -> None:
    if not config.get("vfio_trace", False):
        return
    if config.get("vfio_trace_file") is not None:
        print(f"VFIO tracing enabled -> {config['vfio_trace_file']}")
        return

    timestamp = ensure_action_timestamp(config)
    try:
        _, outputdir_host, _, timestamp = prepare_action_output(
            action_name,
            vm_name,
            config,
            create_dirs=True,
        )
        trace_filename = config.get(
            "vfio_trace_filename", f"{timestamp}_vfio_trace.log"
        )
        trace_file = outputdir_host / trace_filename
    except ValueError:
        trace_file = Path(f"./vfio_trace_{vm_name}_{timestamp}.log")

    config["vfio_trace_file"] = str(trace_file)
    print(f"VFIO tracing enabled -> {trace_file}")


def run_benchmark_action(action_type: str, **kwargs: Any) -> None:
    config = kwargs["config"]
    action_config = config.get("action_config") or {}
    name = kwargs["name"]
    qemu_cmd = kwargs.get("qemu_cmd")
    pin = kwargs.get("pin", True)

    action, outputdir_host, outputdir_guest, timestamp = prepare_action_output(
        action_type,
        name,
        config,
    )

    with spawn_runner(qemu_cmd, config=config, pin=pin) as runner:
        runner.wait_for_ssh()

        action_ctx = ActionContext(
            name=name,
            vm=runner,
            timestamp=timestamp,
            outputdir_host=outputdir_host,
            outputdir_guest=outputdir_guest,
            is_host=config.get("type") == "host",
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
