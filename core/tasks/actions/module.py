#!/usr/bin/env python3
"""Helpers for module task registration."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Optional

from invoke import Collection


def register_module(
    ns: Collection,
    *,
    actions: Optional[str] = None,
    commands: Optional[ModuleType] = None,
) -> None:
    """Register a module's action package and optional Invoke command module."""
    if actions:
        importlib.import_module(actions)
    if commands is not None:
        ns.add_collection(Collection.from_module(commands))
