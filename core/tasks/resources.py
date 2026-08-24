#!/usr/bin/env python3
"""VM resource configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.tasks.config import load_config

_NODE_CPULIST = "/sys/devices/system/node/node{}/cpulist"


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


def parse_cpulist(text: str) -> list[int]:
    """Parse the sysfs cpulist syntax ("0-7", "96-191,288-383", "3") into ids."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(part))
    return cpus


def host_node_cpus(node: int) -> list[int]:
    """The host CPUs of a NUMA node, in the order the kernel lists them."""
    path = _NODE_CPULIST.format(node)
    try:
        with open(path) as f:
            text = f.read()
    except OSError as e:
        raise RuntimeError(f"Cannot read host NUMA topology from {path}: {e}") from e
    return parse_cpulist(text)


def vcpu_pin_map(
    resource: VMResource, pin_base: int = 0, num_vcpus: int | None = None
) -> list[int]:
    """The host CPU each vCPU index should be pinned to.

    The mapping is cell-consistent with ``GuestNumaFeature``: the vCPUs are split
    evenly over ``resource.numa_node`` in listed order, so guest cell ``i`` (the
    one ``-numa node,nodeid=i,cpus=...`` declares and whose memory is bound to
    ``numa_node[i]``) lands on the CPUs of host node ``numa_node[i]``. Taking the
    CPUs from each node's own cpulist keeps this correct on hosts whose CPU
    numbering is not node-major contiguous, e.g. SMT siblings enumerated in a
    second block (``96-191,288-383``).

    ``pin_base`` is an offset **into each node's cpulist**, not a raw host CPU
    number: it reserves the first ``pin_base`` CPUs of every listed node for the
    host, and cell ``i`` gets ``host_node_cpus(numa_node[i])[pin_base:
    pin_base + per_cell]``. Configurations that used to give ``pin_base`` as a
    host CPU number have to be updated.
    """
    nodes = resource.numa_node
    if not nodes:
        raise ValueError(
            "vcpu_pin_map requires resource.numa_node to list at least one host node"
        )

    vcpus = resource.cpu if num_vcpus is None else num_vcpus
    if vcpus % len(nodes) != 0:
        raise ValueError(
            f"Cannot split {vcpus} vCPUs evenly over {len(nodes)} NUMA "
            f"node(s) {nodes}: pick a CPU count that is a multiple of {len(nodes)}"
        )
    per_cell = vcpus // len(nodes)

    pin_map: list[int] = []
    for cell, node in enumerate(nodes):
        cpus = host_node_cpus(node)
        if len(cpus) < pin_base + per_cell:
            raise ValueError(
                f"Host NUMA node {node} (cell {cell}) has {len(cpus)} CPUs, but "
                f"pinning needs {pin_base + per_cell} of them "
                f"(pin_base={pin_base} + {per_cell} vCPUs per cell)"
            )
        pin_map.extend(cpus[pin_base : pin_base + per_cell])
    return pin_map


def host_nodes_spare_cpus(numa_node: list[int], used: set[int]) -> list[int]:
    """The CPUs of the listed host nodes, in node order, that are not in ``used``."""
    spare: list[int] = []
    for node in numa_node:
        for cpu in host_node_cpus(node):
            if cpu not in used:
                spare.append(cpu)
    return spare


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
