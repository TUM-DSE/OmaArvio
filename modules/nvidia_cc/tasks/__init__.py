"""nvidia_cc module tasks — NVIDIA Confidential Compute mode management."""


def register(ns):
    """Register nvidia_cc module into the given Collection."""
    from invoke import Collection
    from modules.nvidia_cc.tasks import gpu

    ns.add_collection(Collection.from_module(gpu))
