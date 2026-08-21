from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.risk_differential import append_observation
from research.risk_differential_models import canonical_sha256

ROOT = Path(__file__).parents[1]


def test_differential_lane_is_observing_and_cannot_backfill(tmp_path: Path) -> None:
    registry = json.loads((ROOT / "benchmarks/future_holdout_lane_registry.json").read_text())
    lane = next(item for item in registry["lanes"] if item["lane_id"] == "risk_differential_shadow")
    assert lane["status"] == "OBSERVING"
    assert lane["economic_behavior"] == "IDENTICAL"
    journal = tmp_path / "observations.jsonl"
    with pytest.raises(ValueError, match="activation"):
        append_observation(journal, {"date": "2026-08-21"}, activation=lane["activation_session"])


def test_scores_remain_null_before_twenty_sessions() -> None:
    closure = json.loads((ROOT / "artifacts/sentinel/risk_differential/closure.json").read_text())
    holdout = closure["future_holdout"]
    assert holdout["status"] == "OBSERVING"
    assert holdout["review_status"] == "NON_REVIEWABLE"
    assert holdout["formal_scores"] is None
    assert holdout["parameter_changes_from_observation"] is False


def test_differential_lane_is_bound_to_immutable_source_identity() -> None:
    identity = json.loads((ROOT / "benchmarks/risk_differential_holdout_identity.json").read_text())
    registry = json.loads((ROOT / "benchmarks/future_holdout_lane_registry.json").read_text())
    lane = next(item for item in registry["lanes"] if item["lane_id"] == "risk_differential_shadow")
    assert identity["payload_sha256"] == canonical_sha256(identity)
    assert lane["sentinel_source_sha256"] == identity["payload_sha256"]
    assert identity["parameter_changes_from_observation"] is False
    assert identity["production_authority_changes_from_observation"] is False


def test_observation_append_does_not_change_production_evidence(tmp_path: Path) -> None:
    production = ROOT / "artifacts/sentinel/evidence_closure/economic_equivalence.json"
    before = production.read_bytes()
    journal = tmp_path / "observations.jsonl"
    append_observation(
        journal,
        {"date": "2026-08-24", "formal_scores": None},
        activation="2026-08-24",
    )
    assert production.read_bytes() == before
