"""Stable public facade for the authoritative flat configuration contract."""

# ``SystemConfig.__module__`` stays here, so runtime hint resolution needs this
# historical annotation name in the facade namespace.
from typing import Literal as Literal

from .model import (
    DEFAULT_CONFIG,
    SystemConfig,
    canonical_control_float,
    config_fingerprint,
)

SystemConfig.__module__ = __name__
SystemConfig.override.__module__ = __name__
SystemConfig.to_dict.__module__ = __name__
canonical_control_float.__module__ = __name__
config_fingerprint.__module__ = __name__

__all__ = (
    "DEFAULT_CONFIG",
    "SystemConfig",
    "canonical_control_float",
    "config_fingerprint",
)
