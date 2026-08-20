from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from research.sentinel_evidence_closure import analyze_evidence_closure, main
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.risk_sentinel.models import (
    BaseMarketRiskRow,
    RiskEvidenceTimeline,
    SentinelLevel,
    SentinelMarketRow,
    WarmupStatus,
)

FAMILIES = (
    "breadth_structure",
    "covariance_stress",
    "market_velocity",
)


def _pairs(*active: str) -> tuple[tuple[str, bool], ...]:
    return tuple((family, family in active) for family in FAMILIES)


def _timeline(
    *,
    sentinel_active: dict[str, tuple[str, ...]],
    base_active: dict[str, tuple[str, ...]],
    sentinel_first: dict[str, str],
    base_first: dict[str, str],
) -> RiskEvidenceTimeline:
    sessions = ("2026-01-02", "2026-01-05", "2026-01-06")
    sentinel_rows = tuple(
        SentinelMarketRow(
            date=session,
            coverage_status=WarmupStatus.READY,
            confidence=0.90,
            level=SentinelLevel.CAUTION,
            freeze_candidate=bool(sentinel_active.get(session)),
            family_active=_pairs(*sentinel_active.get(session, ())),
            reasons=("point-in-time market evidence",),
            weakest_subindustries=("design",),
        )
        for session in sessions
    )
    base_rows = tuple(
        BaseMarketRiskRow(
            date=session,
            family_active=_pairs(*base_active.get(session, ())),
            data_ready=True,
        )
        for session in sessions
    )
    return RiskEvidenceTimeline(
        as_of=sessions[-1],
        sessions=sessions,
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        sentinel_first_family_dates=tuple(sorted(sentinel_first.items())),
        base_first_family_dates=tuple(sorted(base_first.items())),
        incremental_families=(),
        earlier_families=(),
        confirmation_days=1,
        repair_days=0,
        effective_level=SentinelLevel.NORMAL,
        confirmed_since=None,
        confirmation_history_trusted=True,
        trust_reasons=(),
    )


def test_evidence_closure_classifies_duplicate_incremental_and_earlier() -> None:
    timeline = _timeline(
        sentinel_active={
            "2026-01-02": ("breadth_structure", "covariance_stress"),
            "2026-01-05": ("market_velocity",),
        },
        base_active={
            "2026-01-02": ("breadth_structure",),
            "2026-01-06": ("covariance_stress",),
        },
        sentinel_first={
            "breadth_structure": "2026-01-02",
            "covariance_stress": "2026-01-02",
            "market_velocity": "2026-01-05",
        },
        base_first={
            "breadth_structure": "2026-01-02",
            "covariance_stress": "2026-01-06",
        },
    )
    forward = {
        "2026-01-02": {"5d": -0.01, "10d": -0.02, "20d": -0.03},
        "2026-01-05": {"5d": -0.02, "10d": -0.04, "20d": -0.05},
    }

    result = analyze_evidence_closure(timeline, forward_returns=forward)

    assert [
        (event["family"], event["relationship"], event["classification"])
        for event in result["events"]
    ] == [
        ("breadth_structure", "DUPLICATE", "DUPLICATE"),
        ("covariance_stress", "EARLIER", "EARLIER"),
        ("market_velocity", "INCREMENTAL", "INCREMENTAL"),
    ]
    assert result["summary"] == {
        "duplicate": 1,
        "earlier": 1,
        "false_positive": 0,
        "incremental": 1,
        "total_first_family_events": 3,
    }


def test_evidence_closure_labels_positive_20d_sentinel_only_outcome_false_positive() -> None:
    timeline = _timeline(
        sentinel_active={"2026-01-02": ("market_velocity",)},
        base_active={},
        sentinel_first={"market_velocity": "2026-01-02"},
        base_first={},
    )

    result = analyze_evidence_closure(
        timeline,
        forward_returns={
            "2026-01-02": {"5d": -0.01, "10d": 0.02, "20d": 0.08}
        },
    )

    event = result["events"][0]
    assert event["relationship"] == "INCREMENTAL"
    assert event["classification"] == "FALSE_POSITIVE"
    assert event["outcome_status"] == "FALSE_POSITIVE"
    assert event["forward_tech_return_5d"] == -0.01
    assert event["forward_tech_return_10d"] == 0.02
    assert event["forward_tech_return_20d"] == 0.08
    assert event["diagnostic_opportunity_cost_return"] == 0.08
    assert event["production_opportunity_cost"] == 0.0
    assert result["counterfactual_is_accounting_pnl"] is False


def test_evidence_closure_does_not_infer_an_outcome_without_20_future_sessions() -> None:
    timeline = _timeline(
        sentinel_active={"2026-01-02": ("breadth_structure",)},
        base_active={},
        sentinel_first={"breadth_structure": "2026-01-02"},
        base_first={},
    )

    result = analyze_evidence_closure(
        timeline,
        forward_returns={
            "2026-01-02": {"5d": -0.01, "10d": None, "20d": None}
        },
    )

    event = result["events"][0]
    assert event["classification"] == "INCREMENTAL"
    assert event["outcome_status"] == "DATA_NOT_READY"
    assert event["diagnostic_opportunity_cost_return"] is None


def test_evidence_closure_rejects_non_market_or_misaligned_history() -> None:
    timeline = _timeline(
        sentinel_active={"2026-01-02": ("market_velocity",)},
        base_active={},
        sentinel_first={"market_velocity": "2026-01-02"},
        base_first={},
    )
    non_market = replace(
        timeline,
        sentinel_first_family_dates=(("live_book_damage", "2026-01-02"),),
    )
    misaligned = replace(timeline, base_rows=timeline.base_rows[:-1])

    with pytest.raises(ValueError, match="trusted market families"):
        analyze_evidence_closure(non_market, forward_returns={})
    with pytest.raises(ValueError, match="aligned"):
        analyze_evidence_closure(misaligned, forward_returns={})


def test_evidence_closure_runner_writes_canonical_account_free_artifact(
    data_dir: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "evidence-closure.json"

    assert main(
        [
            "--data-dir",
            str(data_dir),
            "--as-of",
            "2026-08-05",
            "--output",
            str(output),
        ]
    ) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["as_of"] == "2026-08-05"
    assert payload["production_causal_confirmation_enabled"] is False
    assert payload["account_history_used"] is False
    assert payload["provenance"]["config_sha256"] == config_fingerprint(
        DEFAULT_CONFIG
    )
    assert payload["provenance"]["universe_size"] == 34
    assert payload["provenance"]["data_sha256"]
    assert output.read_text(encoding="utf-8") == (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
