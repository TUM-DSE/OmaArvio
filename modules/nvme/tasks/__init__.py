"""nvme module tasks — NVMe utilities; no user-visible invoke tasks."""


def register(ns):
    """Register nvme module into the given Collection (side-effect imports only)."""
    import modules.nvme.tasks.actions  # noqa: F401 — loads nvme utilities
