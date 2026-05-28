"""Register openssl and tcrypt actions into the core ACTIONS registry."""

from . import openssl, tcrypt  # noqa: F401 — side-effect: @register_action calls
