"""fio module tasks — FIO benchmarking actions."""


def register(ns):
    """Register fio module into the given Collection."""
    from invoke import Collection

    import modules.fio.tasks.actions  # noqa: F401 — registers "fio" action
    from modules.fio.tasks import fio

    ns.add_collection(Collection.from_module(fio))
