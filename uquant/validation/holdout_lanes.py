"""Compatibility facade for append-only future-holdout lanes."""

# ruff: noqa: F405, RUF022 - frozen compatibility exports

from .holdout.lanes import *  # noqa: F403

__all__ = (
    "HoldoutLane",
    "_BEHAVIORS",
    "_COMMIT",
    "_LANE_FIELDS",
    "_LANE_ID",
    "_LEGACY_LANE_ID",
    "_REGISTRY_FIELDS",
    "_RUNTIME_FIELDS",
    "_SHA256",
    "_STATUSES",
    "build_lane_validation_report",
    "lane_binding_payload",
    "load_lane_registry",
    "validate_lane_registry",
    "validate_lane_registry_transition",
)

for _name in __all__:
    _value = globals()[_name]
    if callable(_value):
        _value.__module__ = __name__
        _value.__qualname__ = _name
