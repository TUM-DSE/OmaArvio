"""nvbandwidth module tasks — NVLink bandwidth benchmarking actions."""


def register(ns):
    """Register nvbandwidth module into the given Collection (side-effect imports only)."""
    from core.tasks.actions.module import register_module

    register_module(ns, actions="modules.nvbandwidth.tasks.actions")
