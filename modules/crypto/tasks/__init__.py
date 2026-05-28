"""crypto module tasks — cryptography benchmarking actions."""


def register(ns):
    """Register crypto module into the given Collection (side-effect imports only)."""
    import modules.crypto.tasks.actions  # noqa: F401 — registers openssl/tcrypt actions
