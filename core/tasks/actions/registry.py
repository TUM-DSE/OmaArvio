from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Tuple


@dataclass(frozen=True)
class ActionContext:
    """Runtime context shared by the benchmark action runner and actions."""

    name: str
    vm: object
    timestamp: str
    outputdir_host: Path
    outputdir_guest: Path
    is_host: bool
    config: dict
    action_config: dict


@dataclass
class Action:
    """Registered action with its handler and output path builder."""

    fn: Callable
    path_fn: Callable[[str, dict], Tuple[str, ...]]


# Registry to hold all available actions
# Key: action name (e.g., "fio")
# Value: Action dataclass
ACTIONS: Dict[str, Action] = {}


def register_action(name: str, path_fn: Callable[[str, dict], Tuple[str, ...]] = None):
    """Decorator to register an action function.

    Args:
        name: Action name used in CLI (e.g., "fio" -> "run-fio").
        path_fn: Callable(name, action_config) -> tuple of path components for output directory.
                 Defaults to (action_name, name) if not provided.
    """

    def decorator(func):
        _path_fn = path_fn if path_fn else lambda n, _cfg: (name, n)
        ACTIONS[name] = Action(fn=func, path_fn=_path_fn)
        return func

    return decorator
