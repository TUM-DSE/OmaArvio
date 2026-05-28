"""nvbandwidth module tasks — NVLink bandwidth benchmarking actions."""


def register(ns):
    """Register nvbandwidth module into the given Collection (side-effect imports only)."""
    import modules.nvbandwidth.tasks.actions  # noqa: F401 — registers nvbandwidth action
