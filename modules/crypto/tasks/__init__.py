"""crypto module tasks — cryptography benchmarking actions."""


def register(ns):
    """Register crypto module into the given Collection (side-effect imports only)."""
    from core.tasks.actions.module import register_module

    register_module(ns, actions="modules.crypto.tasks.actions")
