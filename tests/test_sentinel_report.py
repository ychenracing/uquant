from __future__ import annotations

import pytest

from uquant.report import render_daily_report
from uquant.types import AccountState, Decision, Opportunity, Risk


def _summary(
    *,
    coverage: str,
    base_freeze: bool = False,
    sentinel_freeze: bool = False,
    observed_level: str = "CAUTION",
) -> dict[str, object]:
    return {
        "freeze_new_risk": base_freeze or sentinel_freeze,
        "base_freeze_new_risk": base_freeze,
        "sentinel_freeze_new_risk": sentinel_freeze,
        "sentinel_mode": "FREEZE_ONLY",
        "sentinel_causal_coverage_status": coverage,
        "sentinel_causal_confidence": 0.91 if coverage == "READY" else 0.0,
        "sentinel_causal_observed_level": observed_level,
        "sentinel_causal_active_families": [
            "breadth_structure",
            "market_velocity",
        ],
        "sentinel_causal_weakest_subindustries": ["design", "optical"],
        "sentinel_causal_reasons": ["market breadth weakened"],
        "sentinel_causal_confirmation_history_trusted": coverage == "READY",
        "sentinel_causal_confirmation_days": 2,
        "sentinel_causal_repair_days": 0,
        "sentinel_causal_effective_level": observed_level,
        "sentinel_causal_incremental_families": ["breadth_structure"],
        "sentinel_causal_earlier_families": [],
        "base_first_family_dates": {"market_velocity": "2026-01-02"},
        "sentinel_first_family_dates": {
            "breadth_structure": "2026-01-02",
            "market_velocity": "2026-01-02",
        },
    }


def _report(summary: dict[str, object]) -> str:
    decision = Decision(
        date="2026-08-05",
        opportunity=Opportunity.TREND,
        risk=Risk.CAUTION,
        target_gross=0.7,
        target_k=2,
        targets=(),
        pending_orders=(),
        risk_summary=summary,
        decision_digest="digest",
    )
    return render_daily_report(decision, AccountState.empty(100_000.0))


@pytest.mark.parametrize(
    ("coverage", "base_freeze", "sentinel_freeze", "owner"),
    (
        ("NOT_READY", False, False, "DATA_NOT_READY"),
        ("READY", False, False, "NONE"),
        ("READY", True, False, "BASE_RISK"),
        ("READY", False, True, "SENTINEL"),
        ("READY", True, True, "BOTH"),
    ),
)
def test_daily_report_distinguishes_every_sentinel_owner(
    coverage: str,
    base_freeze: bool,
    sentinel_freeze: bool,
    owner: str,
) -> None:
    report = _report(
        _summary(
            coverage=coverage,
            base_freeze=base_freeze,
            sentinel_freeze=sentinel_freeze,
        )
    )

    assert f"- Owner: **{owner}**" in report


def test_daily_report_contains_the_compact_sentinel_operating_fields() -> None:
    report = _report(_summary(coverage="READY"))
    section = report.split("## Risk Sentinel", 1)[1].split("## Targets", 1)[0]

    assert "- Mode: FREEZE_ONLY" in section
    assert "- Level: CAUTION" in section
    assert "- Coverage: READY" in section
    assert "- Confidence: 91.0%" in section
    assert "- Owner: **NONE**" in section
    assert "- Risk Families: breadth_structure, market_velocity" in section
    assert "- AI Industry Risk: design, optical" in section
    assert "- Conclusion: normal execution; Sentinel remains observational." in section
    assert not any(
        forbidden in section
        for forbidden in ("SELL", "sell", "reduce position", "single-stock")
    )


def test_daily_report_limits_sentinel_conclusions_to_safe_manual_actions() -> None:
    data_report = _report(_summary(coverage="NOT_READY"))
    freeze_report = _report(
        _summary(coverage="READY", sentinel_freeze=True)
    )

    assert "- Conclusion: check market data; do not infer safety." in data_report
    assert "- Conclusion: do not add new risk." in freeze_report


def test_daily_report_handles_absent_sentinel_summary_as_data_not_ready() -> None:
    report = _report({})

    assert "- Level: NOT_READY" in report
    assert "- Coverage: NOT_READY" in report
    assert "- Owner: **DATA_NOT_READY**" in report
    assert "- Risk Families: NONE" in report
    assert "- AI Industry Risk: NONE" in report
