"""nvidia_cc module tasks — NVIDIA Confidential Compute mode management."""


def register(ns):
    """Register nvidia_cc module into the given Collection."""
    from core.tasks.actions.module import register_module
    from modules.nvidia_cc.tasks import gpu

    register_module(ns, commands=gpu)
