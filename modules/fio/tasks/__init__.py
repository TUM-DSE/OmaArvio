"""fio module tasks — FIO benchmarking actions."""


def register(ns):
    """Register fio module into the given Collection."""
    from core.tasks.actions.module import register_module
    from modules.fio.tasks import fio

    register_module(ns, actions="modules.fio.tasks.actions", commands=fio)
