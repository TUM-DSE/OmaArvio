"""Core actions registry.

This module only exports the registry symbols.  Module-level action
implementations (storage, precondition, nvbandwidth, …) are registered
by importing their containing modules' actions packages at the root
tasks/__init__.py level.
"""

from core.tasks.actions.registry import ACTIONS, register_action

__all__ = ["ACTIONS", "register_action"]
