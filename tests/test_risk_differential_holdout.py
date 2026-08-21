from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from research.risk_differential import append_observation
from research.risk_differential_models import canonical_sha256

ROOT = Path(__file__).parents[1]
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "future_holdout_risk_differential_under_test",
    ROOT / "scripts/future_holdout.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT)


def _payload() -> dict[str, object]:
    return {
        "trade_only_axes": ["market_velocity"],
        "sentinel_only_axes": [],
        "base_only_axes": [],
        "all_agree_axes": ["breadth_structure"],
        "trade_and_sentinel_not_base_axes": [],
        "trade_risk_level": 1,
        "base_risk_level": 0,
        "sentinel_risk_level": 0,
        "trade_block_new_entries": True,
        "base_freeze_new_risk": False,
        "sentinel_freeze_authorized": False,
        "actionable_buy_intents": 1,
        "actionable_pyramid_intents": 0,
    }


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


def test_observation_payload_rejects_loose_types_and_overlapping_axes() -> None:
    required = set(_payload())
    malformed = _payload()
    malformed["base_only_axes"] = ["market_velocity"]
    with pytest.raises(ValueError, match="disjoint"):
        _SCRIPT._validate_risk_differential_payload(
            malformed,
            required=required,
            allowed_axes=frozenset({"market_velocity", "breadth_structure"}),
        )
    malformed = _payload()
    malformed["trade_risk_level"] = True
    with pytest.raises(ValueError, match="integer ranks"):
        _SCRIPT._validate_risk_differential_payload(
            malformed,
            required=required,
            allowed_axes=frozenset({"market_velocity", "breadth_structure"}),
        )


def test_append_cli_does_not_accept_caller_authored_risk_facts() -> None:
    parsed = _SCRIPT._parser().parse_args(
        [
            "append-risk-differential",
            "--trade-root",
            "/tmp/trade",
            "--date",
            "2026-08-24",
        ]
    )
    assert not hasattr(parsed, "payload")
    with pytest.raises(SystemExit):
        _SCRIPT._parser().parse_args(
            [
                "append-risk-differential",
                "--trade-root",
                "/tmp/trade",
                "--date",
                "2026-08-24",
                "--payload",
                "fabricated.json",
            ]
        )


def test_holdout_payload_is_derived_from_both_engine_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fields = {
        "market_velocity": False,
        "breadth_structure": False,
        "covariance_stress": False,
        "leadership_damage": False,
        "live_book_damage": False,
        "capital_damage": False,
        "concentration_damage": False,
        "block_new_entries": False,
        "block_pyramiding": False,
        "recommended_gross_cap": 1.0,
        "severity_rank": 0,
    }
    trade = {**fields, "market_velocity": True, "severity_rank": 1}
    uquant = {
        "dates": ["2026-08-24"],
        "base": [fields],
        "sentinel": [fields],
        "actionability": {"2026-08-24": {"buy": 2, "pyramid": 1}},
        "decision_digest_sha256": "d" * 64,
    }
    monkeypatch.setattr(_SCRIPT, "run_uquant_cell", lambda *_: uquant)
    monkeypatch.setattr(
        _SCRIPT,
        "run_trade_cell",
        lambda *_: {"dates": ["2026-08-24"], "trade": [trade]},
    )
    raw, envelope = _SCRIPT._compute_risk_differential_payload(
        root=ROOT,
        trade_root=tmp_path,
        data_directory=tmp_path,
        date="2026-08-24",
        data_sha256="a" * 64,
        source_registry={
            "trade": {"commit": "b" * 40, "python_source_sha256": "c" * 64}
        },
    )
    assert raw["trade_only_axes"] == ["market_velocity"]
    assert raw["trade_risk_level"] == 1
    assert raw["actionable_buy_intents"] == 2
    assert raw["actionable_pyramid_intents"] == 1
    assert envelope["derived_payload_sha256"] == canonical_sha256(raw)
    assert envelope["payload_sha256"] == canonical_sha256(envelope)


def test_formal_scores_activate_only_after_twenty_real_rows() -> None:
    rows = [{"date": f"2026-09-{index:02d}", **_payload()} for index in range(1, 21)]
    assert _SCRIPT._differential_formal_scores(rows[:19]) is None
    scores = _SCRIPT._differential_formal_scores(rows)
    assert scores is not None
    assert scores["observed_sessions"] == 20
    assert scores["review_milestone"] == 20


def test_observation_session_must_be_reviewed_and_present_in_holdout_data() -> None:
    with pytest.raises(ValueError, match="reviewed market session"):
        _SCRIPT._validate_differential_session(
            "2026-08-29",
            activation="2026-08-24",
            reviewed_sessions=("2026-08-24",),
            observed_sessions=("2026-08-24",),
        )
    with pytest.raises(ValueError, match="source-bound holdout data"):
        _SCRIPT._validate_differential_session(
            "2026-08-24",
            activation="2026-08-24",
            reviewed_sessions=("2026-08-24",),
            observed_sessions=(),
        )


def test_differential_lane_is_bound_to_immutable_source_identity() -> None:
    identity = json.loads((ROOT / "benchmarks/risk_differential_holdout_identity.json").read_text())
    registry = json.loads((ROOT / "benchmarks/future_holdout_lane_registry.json").read_text())
    lane = next(item for item in registry["lanes"] if item["lane_id"] == "risk_differential_shadow")
    assert identity["payload_sha256"] == canonical_sha256(identity)
    assert lane["sentinel_source_sha256"] == identity["payload_sha256"]
    assert identity["parameter_changes_from_observation"] is False
    assert identity["production_authority_changes_from_observation"] is False


def test_observation_append_does_not_change_decision_digest(tmp_path: Path) -> None:
    production = ROOT / "artifacts/sentinel/risk_differential/production_economic_equivalence.json"
    proof = json.loads(production.read_text())
    assert proof["passed"] is True
    assert proof["exact_dimensions"]["decision_digest"] is True
    before = production.read_bytes()
    journal = tmp_path / "observations.jsonl"
    append_observation(
        journal,
        {"date": "2026-08-24", "formal_scores": None},
        activation="2026-08-24",
    )
    assert production.read_bytes() == before


def test_trade_source_identity_is_immutable_after_activation() -> None:
    item = {
        "lane_id": "risk_differential_shadow",
        "lane_identity_sha256": "a" * 64,
        "trade_source_commit": "b" * 40,
        "trade_source_sha256": "c" * 64,
    }
    _SCRIPT._validate_prior_differential_source_identity(
        item,
        lane_id="risk_differential_shadow",
        lane_identity_sha256="a" * 64,
        trade_commit="b" * 40,
        trade_source_sha256="c" * 64,
    )
    with pytest.raises(ValueError, match="source identity changed"):
        _SCRIPT._validate_prior_differential_source_identity(
            item,
            lane_id="risk_differential_shadow",
            lane_identity_sha256="a" * 64,
            trade_commit="d" * 40,
            trade_source_sha256="c" * 64,
        )
    with pytest.raises(ValueError, match="source identity changed"):
        _SCRIPT._validate_prior_differential_source_identity(
            item,
            lane_id="risk_differential_shadow",
            lane_identity_sha256="a" * 64,
            trade_commit="b" * 40,
            trade_source_sha256="e" * 64,
        )
