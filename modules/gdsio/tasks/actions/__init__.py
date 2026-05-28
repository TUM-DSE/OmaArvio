"""Register gdsio and elbencho actions into the core ACTIONS registry."""

from . import gdsio, elbencho  # noqa: F401 — side-effect: @register_action calls
