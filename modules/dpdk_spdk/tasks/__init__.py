"""dpdk_spdk module tasks — DPDK/SPDK stress tasks."""


def register(ns):
    """Register dpdk_spdk module into the given Collection."""
    from invoke import Collection

    import modules.dpdk_spdk.tasks.actions  # noqa: F401 — loads dpdk_spdk utilities
    from modules.dpdk_spdk.tasks import stress

    ns.add_collection(Collection.from_module(stress))
