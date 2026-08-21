from __future__ import annotations

import json
from dataclasses import replace

import pytest

from research.candidate_runner import DecisionTrace
from research.risk_differential import (
    align_three_way,
    classify_boolean_axis,
    classify_normalized_scalar,
    differential_events,
    forward_outcomes,
    normalize_trade_governance,
    normalize_uquant_decision,
    prefix_trace_sha256,
)
from research.risk_differential_models import RiskTraceRow


def _row(date: str, system: str, *, velocity: bool | None = False) -> RiskTraceRow:
    return replace(
        RiskTraceRow.empty(date, system, status="READY", severity_rank=0),
        market_velocity=velocity,
    )


def test_missing_capability_is_not_false() -> None:
    assert classify_boolean_axis(trade=None, base=False, sentinel=False) == "NOT_COMPARABLE"
    row = normalize_trade_governance(
        {
            "date": "2026-08-05",
            "risk_level": 1,
            "block_new_entries": False,
            "block_pyramids": True,
            "risk_confidence": 0.8,
        }
    )
    assert row.market_velocity is None


def test_missing_trade_admission_fields_are_not_coerced_to_false() -> None:
    row = normalize_trade_governance(
        {
            "date": "2026-08-05",
            "risk_level": 1,
            "risk_confidence": 0.8,
        }
    )
    assert row.block_new_entries is None
    assert row.block_pyramiding is None


def test_scalar_classification_preserves_exact_risk_magnitude() -> None:
    assert classify_normalized_scalar(
        trade=2, base=1, sentinel=1, higher_is_riskier=True
    ) == "TRADE_ONLY"
    assert classify_normalized_scalar(
        trade=2, base=1, sentinel=0, higher_is_riskier=True
    ) == "TRADE_ONLY"
    assert classify_normalized_scalar(
        trade=0.35, base=0.60, sentinel=0.60, higher_is_riskier=False
    ) == "TRADE_ONLY"
    assert classify_normalized_scalar(
        trade=0.85, base=0.60, sentinel=0.60, higher_is_riskier=False
    ) == "BASE_AND_SENTINEL_NOT_TRADE"


def test_incomplete_forward_horizon_is_null_not_truncated() -> None:
    outcomes = forward_outcomes(
        ["2026-08-04"],
        ["2026-08-04", "2026-08-05"],
        [1.0, 0.9],
        [1.0, 0.8],
        horizons=(1, 3),
    )[0]["outcomes"]
    assert outcomes["1d"] is not None
    assert outcomes["3d"] is None


def test_uquant_adapter_reads_base_and_sentinel_from_same_decision() -> None:
    evidence = {
        "base_family_active": {"market_velocity": True},
        "base_freeze_new_risk": True,
        "base_target_gross_cap": 0.6,
        "severity": "DEFENSIVE",
        "sentinel_family_active": {"market_velocity": False},
        "sentinel_causal_coverage_status": "READY",
        "sentinel_causal_confidence": 0.9,
        "sentinel_causal_effective_level": "NORMAL",
        "sentinel_freeze_new_risk": False,
        "sentinel_assessment": {"suggested_gross_cap": None},
    }
    trace = DecisionTrace(
        date="2026-01-05",
        opportunity="WATCH",
        risk="DEFENSIVE",
        transition_damage=0.0,
        family_votes=(),
        sector_guard_active=False,
        capital_budget_level=0,
        leaders=(),
        strategic_tag="",
        targets=(),
        orders=(),
        fills=(),
        equity=1.0,
        risk_evidence=tuple((key, json.dumps(value)) for key, value in evidence.items()),
    )
    base, sentinel = normalize_uquant_decision(trace)
    assert base.date == sentinel.date == trace.date
    assert base.market_velocity is True
    assert sentinel.market_velocity is False


def test_base_warning_level_uses_canonical_decision_risk_not_damage_severity() -> None:
    evidence = {
        "severity": "MARKET",
        "base_freeze_new_risk": True,
        "base_target_gross_cap": 0.6,
        "sentinel_causal_coverage_status": "NOT_READY",
    }
    trace = DecisionTrace(
        date="2026-01-05",
        opportunity="WATCH",
        risk="RISK_OFF",
        transition_damage=0.0,
        family_votes=(),
        sector_guard_active=False,
        capital_budget_level=0,
        leaders=(),
        strategic_tag="",
        targets=(),
        orders=(),
        fills=(),
        equity=1.0,
        risk_evidence=tuple((key, json.dumps(value)) for key, value in evidence.items()),
    )
    base, _ = normalize_uquant_decision(trace)
    assert base.level == "RISK_OFF"
    assert base.severity_rank == 2


def test_trace_dates_must_align() -> None:
    with pytest.raises(ValueError, match="aligned"):
        align_three_way(
            (_row("2026-08-04", "trade"),),
            (_row("2026-08-05", "uquant_base"),),
            (_row("2026-08-05", "uquant_sentinel"),),
        )


def test_trade_only_event_counts_actual_buy_and_pyramid_intents() -> None:
    trade = replace(_row("2026-08-05", "trade", velocity=True), block_new_entries=True)
    base = _row("2026-08-05", "uquant_base")
    sentinel = _row("2026-08-05", "uquant_sentinel")
    events = differential_events(
        align_three_way((trade,), (base,), (sentinel,)),
        actionability={"2026-08-05": {"buy": 2, "pyramid": 1, "gross": 0.8}},
    )
    event = next(item for item in events if item.axis == "market_velocity")
    assert event.classification == "TRADE_ONLY"
    assert (event.actionable_buy_intents, event.actionable_pyramid_intents) == (2, 1)


def test_future_rows_do_not_change_prefix_trace() -> None:
    prefix = (_row("2026-08-04", "trade"), _row("2026-08-05", "trade", velocity=True))
    extended = (*prefix, _row("2026-08-06", "trade"))
    assert prefix_trace_sha256(prefix, as_of="2026-08-05") == prefix_trace_sha256(
        extended, as_of="2026-08-05"
    )
