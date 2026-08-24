"""Future-holdout sealed manifest schema, assembly, and readback."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from typing import Any, cast

from .contract import (
    MANIFEST_FIELDS as _MANIFEST_FIELDS,
)
from .contract import (
    SHA256_PATTERN as _SHA256,
)
from .contract import (
    FutureHoldoutContract,
)
from .contract import (
    canonical_sha256 as _canonical_sha256,
)
from .contract import (
    session_dates as _session_dates,
)
from .source_identity import HoldoutBinding
from .source_identity import state_hashes as _state_hashes


def _normalized_scores(
    scores: Mapping[str, float | int | None] | None,
    *,
    sessions: Sequence[str],
    contract: FutureHoldoutContract,
) -> dict[str, float | int | None]:
    supplied = {} if scores is None else dict(scores)
    unknown = set(supplied) - set(contract.score_fields)
    if unknown:
        raise ValueError(f"unknown holdout scores: {sorted(unknown)}")
    normalized = {name: supplied.get(name) for name in contract.score_fields}
    if len(sessions) < contract.review_milestones[0]:
        if any(value is not None for value in normalized.values()):
            raise ValueError("formal holdout scores must be null before the first review milestone")
        if scores is not None and set(supplied) != set(contract.score_fields):
            raise ValueError("provided holdout scores require every score field")
        return normalized
    if scores is not None and set(supplied) != set(contract.score_fields):
        raise ValueError("provided holdout scores require every score field")
    if any(value is None for value in normalized.values()):
        raise ValueError("reviewable holdout sessions require every formal score")
    return _validated_score_values(normalized)


def _validated_score_values(
    scores: Mapping[str, float | int | None],
) -> dict[str, float | int | None]:
    """Validate complete replay metrics independently of review eligibility."""

    normalized = dict(scores)
    for name, value in normalized.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"holdout score must be finite: {name}")
    if not isinstance(normalized["account_orders"], int):
        raise ValueError("holdout account_orders must be an integer")
    final_wealth = cast(float | int, normalized["final_wealth"])
    gross_turnover = cast(float | int, normalized["gross_turnover"])
    if float(final_wealth) <= 0:
        raise ValueError("holdout final_wealth must be positive")
    if int(normalized["account_orders"]) < 0:
        raise ValueError("holdout account_orders must be nonnegative")
    if float(gross_turnover) < 0:
        raise ValueError("holdout gross_turnover must be nonnegative")
    for name in ("max_drawdown", "top1_concentration", "top3_concentration", "pnl_hhi"):
        value = cast(float | int, normalized[name])
        if not 0 <= float(value) <= 1:
            raise ValueError(f"holdout {name} must be between zero and one")
    top1 = cast(float | int, normalized["top1_concentration"])
    top3 = cast(float | int, normalized["top3_concentration"])
    if float(top3) < float(top1):
        raise ValueError("holdout top3_concentration must not be below top1_concentration")
    return normalized


def _binding_payload(binding: HoldoutBinding) -> dict[str, Any]:
    raw = asdict(binding)
    return {
        "production": {
            "commit": raw.pop("production_commit"),
            "source_sha256": raw.pop("production_source_sha256"),
        },
        "strategy_source_sha256": raw.pop("strategy_source_sha256"),
        "strategy_cli_sha256": raw.pop("strategy_cli_sha256"),
        "effective_config_sha256": raw.pop("effective_config_sha256"),
        "universe_sha256": raw.pop("universe_sha256"),
        "industry_sha256": raw.pop("industry_sha256"),
        "environment": raw,
    }


def _assemble_future_holdout_manifest(
    *,
    contract: FutureHoldoutContract,
    binding: HoldoutBinding,
    account_payload: Mapping[str, Any],
    holdout_sessions: Iterable[str],
    scores: Mapping[str, float | int | None] | None = None,
    holdout_data_sha256: str,
    metrics_sha256: str | None = None,
) -> dict[str, Any]:
    """Assemble already-read authoritative inputs into the sealed schema."""

    sessions = _session_dates(holdout_sessions, contract=contract)
    if (
        binding.strategy_source_sha256 != contract.strategy_source_sha256
        or binding.strategy_cli_sha256 != contract.strategy_cli_sha256
        or binding.effective_config_sha256 != contract.strategy_config_sha256
    ):
        raise ValueError("current strategy decision path or config drifted from the observation anchor")
    normalized_scores = _normalized_scores(scores, sessions=sessions, contract=contract)
    if not _SHA256.fullmatch(holdout_data_sha256):
        raise ValueError("holdout data identity must be SHA-256")
    if metrics_sha256 is not None and not _SHA256.fullmatch(metrics_sha256):
        raise ValueError("holdout metrics identity must be SHA-256")
    if bool(sessions) != (metrics_sha256 is not None):
        raise ValueError("observed sessions and independent metrics evidence must agree")
    binding_payload = _binding_payload(binding)
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "manifest_id": "phase2-future-holdout-manifest-v2",
        "contract_sha256": contract.sha256,
        "production": binding_payload["production"],
        "strategy_anchor": {
            "candidate_commit": contract.strategy_anchor_commit,
            "decision_source_sha256": contract.strategy_source_sha256,
            "cli_decision_sha256": contract.strategy_cli_sha256,
            "account_code_sha256": contract.strategy_account_code_sha256,
            "prior_close_account_sha256": contract.prior_close_account_sha256,
            "effective_config_sha256": contract.strategy_config_sha256,
        },
        "effective_config_sha256": binding_payload["effective_config_sha256"],
        "universe_sha256": binding_payload["universe_sha256"],
        "industry_sha256": binding_payload["industry_sha256"],
        "environment": binding_payload["environment"],
        "dates": {
            "last_in_sample": contract.last_in_sample_date,
            "first_holdout": contract.first_holdout_date,
        },
        "data": {
            "directory": contract.data_directory,
            "sha256": holdout_data_sha256,
        },
        "prior_close_state": _state_hashes(
            account_payload,
            as_of=contract.last_in_sample_date,
        ),
        "review_milestones": list(contract.review_milestones),
        "observation": {
            "session_count": len(sessions),
            "first_session": sessions[0] if sessions else None,
            "last_session": sessions[-1] if sessions else None,
            "metrics_sha256": metrics_sha256,
            "parameter_changes_from_observation": False,
        },
        "scores": normalized_scores,
    }
    manifest["canonical_sha256"] = _canonical_sha256(manifest, omit_seal=True)
    return manifest


def _validate_future_holdout_manifest_payload(
    manifest: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
) -> None:
    """Reject a stale, weakened, resealed, or inconsistent readback payload."""

    raw = dict(manifest)
    observation = raw.get("observation")
    if (
        isinstance(observation, Mapping)
        and observation.get("parameter_changes_from_observation") is not False
    ):
        raise ValueError("holdout parameter changes from observation are prohibited")
    if set(raw) != _MANIFEST_FIELDS:
        raise ValueError("future holdout manifest schema is malformed")
    seal = raw.get("canonical_sha256")
    if not isinstance(seal, str) or seal != _canonical_sha256(raw, omit_seal=True):
        raise ValueError("future holdout manifest hash is invalid")
    if raw != dict(expected):
        raise ValueError("future holdout manifest is stale")


assemble_future_holdout_manifest = _assemble_future_holdout_manifest
binding_payload = _binding_payload
normalized_scores = _normalized_scores
validate_future_holdout_manifest_payload = _validate_future_holdout_manifest_payload
validated_score_values = _validated_score_values

__all__ = (
    "_normalized_scores",
    "_validated_score_values",
    "_binding_payload",
    "_assemble_future_holdout_manifest",
    "_validate_future_holdout_manifest_payload",
)
