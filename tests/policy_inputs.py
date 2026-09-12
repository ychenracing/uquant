"""Isolated domain-test inputs; never a native or production configuration."""

from types import SimpleNamespace
from typing import Any

from uquant.config import DEFAULT_CONFIG, SystemConfig


class PolicyInputs(SimpleNamespace):
    """A domain fixture exposing the full effective input record for hashing."""

    def to_dict(self) -> dict[str, Any]:
        return vars(self).copy()


def policy_inputs(**changes: Any) -> PolicyInputs:
    """Keep isolated mechanism tests independent of production tuning inputs."""
    value = PolicyInputs(**(DEFAULT_CONFIG.to_dict() | changes))
    SystemConfig.__post_init__(value)
    return value
