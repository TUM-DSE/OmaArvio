#!/usr/bin/env python3
"""VM resource configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.tasks.config import load_config


@dataclass
class NodeInfo:
    cpus: str
    mem: int
    dist: list[int]


@dataclass
class VMResource:
    cpu: int
    memory: int  # GB
    pin_base: int
    numa_node: list[int] | None = None
    vnuma: Optional[NodeInfo] = None


def _vmresource_from_config(rc) -> VMResource:
    return VMResource(
        cpu=rc.cpu,
        memory=rc.memory,
        numa_node=rc.numa_node,
        pin_base=rc.pin_base,
    )


def get_vm_resource(hostname: str, name: str) -> VMResource:
    cfg = load_config()

    host = cfg.hosts.get(hostname)
    if host and name in host.vm_resources:
        return _vmresource_from_config(host.vm_resources[name])

    if name in cfg.default_vm_resources:
        if host is None:
            print(
                f"Warning: No VM resource config found for hostname '{hostname}', "
                "falling back to defaults"
            )
        return _vmresource_from_config(cfg.default_vm_resources[name])

    available = list(cfg.default_vm_resources.keys())
    raise ValueError(f"Unknown VM size: {name!r}. Available sizes: {available}")
