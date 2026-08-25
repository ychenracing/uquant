"""Compatibility facade for append-only future-holdout lanes."""

# ruff: noqa: F401, RUF022 - frozen compatibility exports

from .holdout.lanes import (
    BEHAVIORS as _BEHAVIORS,
)
from .holdout.lanes import (
    COMMIT_PATTERN as _COMMIT,
)
from .holdout.lanes import (
    LANE_FIELDS as _LANE_FIELDS,
)
from .holdout.lanes import (
    LANE_ID_PATTERN as _LANE_ID,
)
from .holdout.lanes import (
    LEGACY_LANE_ID as _LEGACY_LANE_ID,
)
from .holdout.lanes import (
    REGISTRY_FIELDS as _REGISTRY_FIELDS,
)
from .holdout.lanes import (
    RUNTIME_FIELDS as _RUNTIME_FIELDS,
)
from .holdout.lanes import (
    SHA256_PATTERN as _SHA256,
)
from .holdout.lanes import (
    STATUSES as _STATUSES,
)
from .holdout.lanes import (
    HoldoutLane,
    build_lane_validation_report,
    lane_binding_payload,
    load_lane_registry,
    validate_lane_registry,
    validate_lane_registry_transition,
)
from .holdout.lanes import (
    canonical_bytes as _canonical_bytes,
)
from .holdout.lanes import (
    canonical_sha256 as _canonical_sha256,
)
from .holdout.lanes import (
    decode_lane as _decode_lane,
)
from .holdout.lanes import (
    identity as _identity,
)
from .holdout.lanes import (
    reject_duplicate_keys as _reject_duplicate_keys,
)
from .holdout.lanes import (
    reject_nonstandard_constant as _reject_nonstandard_constant,
)
from .holdout.lanes import (
    validate_hash as _validate_hash,
)

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

for _name, _value in (
    ("HoldoutLane", HoldoutLane),
    ("build_lane_validation_report", build_lane_validation_report),
    ("lane_binding_payload", lane_binding_payload),
    ("load_lane_registry", load_lane_registry),
    ("validate_lane_registry", validate_lane_registry),
    ("validate_lane_registry_transition", validate_lane_registry_transition),
):
    _value.__module__ = __name__
    _value.__qualname__ = _name
