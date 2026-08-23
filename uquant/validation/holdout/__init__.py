"""Immutable future-holdout boundary and exact post-checkout evidence."""

# ruff: noqa: F401, F405, RUF022 - frozen compatibility exports and seams

from __future__ import annotations

import shutil
from pathlib import Path

from ..ai_era import AI_ERA_WINDOWS
from .contract import *  # noqa: F403
from .manifest import *  # noqa: F403
from .service import *  # noqa: F403
from .source_identity import *  # noqa: F403

# Preserve callers that derive the repository root from the historical facade path.
__file__ = str(Path(__file__).resolve().parent.parent / "holdout.py")

__all__ = (
    "FutureHoldoutContract",
    "HOLDOUT_DATA_DIRECTORY",
    "HOLDOUT_START",
    "HoldoutBinding",
    "LAST_IN_SAMPLE_DATE",
    "PRIOR_CLOSE_ACCOUNT_SHA256",
    "REQUIRED_FUTURE_HOLDOUT_SHA256",
    "REVIEWED_PHASE1_WINDOWS",
    "REVIEW_CALENDAR_SOURCE",
    "REVIEW_MILESTONES",
    "REVIEW_SESSIONS",
    "SCORE_FIELDS",
    "STRATEGY_ACCOUNT_CODE_SHA256",
    "STRATEGY_ANCHOR_COMMIT",
    "STRATEGY_CLI_SHA256",
    "STRATEGY_CONFIG_SHA256",
    "STRATEGY_SOURCE_SHA256",
    "_ACCOUNT_EXECUTION_FIELDS",
    "_CLI_OPERATIONAL_COMMANDS",
    "_COMMIT",
    "_CONTRACT_FIELDS",
    "_MANIFEST_FIELDS",
    "_SHA256",
    "_STRATEGY_FIXED_RELATIVES",
    "_STRATEGY_OPERATIONAL_RELATIVES",
    "build_future_holdout_manifest",
    "current_holdout_binding",
    "generate_future_holdout_manifest",
    "holdout_data_identity",
    "holdout_source_sha256",
    "load_future_holdout_contract",
    "maximum_observed_market_date",
    "validate_future_holdout_manifest",
    "validate_holdout_layout",
    "validate_prior_close_account",
)

for _name in __all__:
    _value = globals()[_name]
    if callable(_value):
        _value.__module__ = __name__
        _value.__qualname__ = _name
