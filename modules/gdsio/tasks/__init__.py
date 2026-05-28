"""gdsio module tasks — GPU Direct Storage I/O tasks."""


def register(ns):
    """Register gdsio module into the given Collection."""
    from invoke import Collection

    import modules.gdsio.tasks.actions  # noqa: F401 — registers gdsio/elbencho actions
    from modules.gdsio.tasks import gds

    ns.add_collection(Collection.from_module(gds))
