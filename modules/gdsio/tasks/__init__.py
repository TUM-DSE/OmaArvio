"""gdsio module tasks — GPU Direct Storage I/O tasks."""


def register(ns):
    """Register gdsio module into the given Collection."""
    from core.tasks.actions.module import register_module
    from modules.gdsio.tasks import gds

    register_module(ns, actions="modules.gdsio.tasks.actions", commands=gds)
