"""nvme module tasks — NVMe utilities; no user-visible invoke tasks."""


def register(ns):
    """Register nvme module into the given Collection (side-effect imports only)."""
    from core.tasks.actions.module import register_module

    register_module(ns, actions="modules.nvme.tasks.actions")
