"""dpdk_spdk module tasks — DPDK/SPDK stress tasks."""


def register(ns):
    """Register dpdk_spdk module into the given Collection."""
    from core.tasks.actions.module import register_module
    from modules.dpdk_spdk.tasks import stress

    register_module(ns, actions="modules.dpdk_spdk.tasks.actions", commands=stress)
