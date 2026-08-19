from __future__ import annotations

import pytest

from uquant.report import render_daily_report
from uquant.types import AccountState, Decision, Opportunity, Risk


def _decision(summary: dict[str, object]) -> Decision:
    return Decision(
        date="2026-08-19",
        opportunity=Opportunity.CHOPPY,
        risk=Risk.NORMAL,
        target_gross=0.0,
        target_k=0,
        targets=(),
        pending_orders=(),
        risk_summary=summary,
        decision_digest="digest",
    )


@pytest.mark.parametrize(
    ("coverage", "base", "sentinel", "owner"),
    (
        ("READY", False, False, "NONE"),
        ("READY", True, False, "BASE_RISK"),
        ("READY", False, True, "SENTINEL"),
        ("READY", True, True, "BOTH"),
        ("DEGRADED", False, False, "DATA_NOT_READY"),
    ),
)
def test_daily_report_distinguishes_every_freeze_owner(
    coverage: str,
    base: bool,
    sentinel: bool,
    owner: str,
) -> None:
    summary: dict[str, object] = {
        "sentinel_mode": "FREEZE_ONLY",
        "sentinel_causal_observed_level": "CAUTION",
        "sentinel_causal_effective_level": "NORMAL",
        "sentinel_causal_coverage_status": coverage,
        "sentinel_causal_confidence": 0.9,
        "sentinel_causal_confirmation_history_trusted": True,
        "sentinel_causal_confirmation_days": 1,
        "sentinel_causal_repair_days": 0,
        "sentinel_causal_incremental_families": ["market_velocity"],
        "sentinel_causal_earlier_families": [],
        "sentinel_first_family_dates": {"market_velocity": "2026-08-18"},
        "base_first_family_dates": {"market_velocity": "2026-08-19"},
        "sentinel_causal_weakest_subindustries": ["a", "b", "c", "d"],
        "sentinel_causal_reasons": ["r1", "r2", "r3", "r4"],
        "base_freeze_new_risk": base,
        "sentinel_freeze_new_risk": sentinel,
        "freeze_new_risk": base or sentinel,
    }

    report = render_daily_report(_decision(summary), AccountState.empty(100.0))

    assert "## Risk Sentinel" in report
    assert f"Freeze Owner: **{owner}**" in report
    assert f"New Risk Allowed: **{'NO' if base or sentinel else 'YES'}**" in report
    assert "Weakest subindustries: a, b, c" in report
    assert "r1; r2; r3" in report
    assert "d" not in report.split("Weakest subindustries: ", 1)[1].splitlines()[0]
    assert "r4" not in report


def test_daily_report_is_byte_deterministic_for_unordered_family_maps() -> None:
    common: dict[str, object] = {
        "sentinel_mode": "FREEZE_ONLY",
        "sentinel_causal_observed_level": "NORMAL",
        "sentinel_causal_effective_level": "NORMAL",
        "sentinel_causal_coverage_status": "READY",
        "sentinel_causal_confidence": 1.0,
        "sentinel_causal_confirmation_history_trusted": True,
        "sentinel_causal_confirmation_days": 0,
        "sentinel_causal_repair_days": 3,
        "freeze_new_risk": False,
    }
    left = {
        **common,
        "sentinel_first_family_dates": {"market_velocity": "2", "breadth_structure": "1"},
    }
    right = {
        **common,
        "sentinel_first_family_dates": {"breadth_structure": "1", "market_velocity": "2"},
    }

    account = AccountState.empty(100.0)
    assert render_daily_report(_decision(left), account) == render_daily_report(
        _decision(right),
        account,
    )
