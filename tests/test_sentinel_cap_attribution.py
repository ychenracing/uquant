from __future__ import annotations

import pandas as pd
import pytest

from research.sentinel_cap_ablation import compare_sentinel_cap_results
from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    Position,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Target,
)


def _sentinel_risk(cap: float) -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=cap,
        votes=0,
        evidence={
            "sentinel_cap": cap,
            "sentinel_cap_binding": True,
            "sentinel_effective_level": "DEFENSIVE",
        },
        reasons=(),
        shock_state="NONE",
        freeze_new_risk=True,
        reduction_level=0,
        severity="NORMAL",
    )


def test_sentinel_cap_reuses_risk_priority_and_stable_risk_mechanism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symbol = "sz300308"
    account = AccountState(
        initial_cash=100.0,
        cash=5.0,
        positions={symbol: Position(symbol, shares=95, avg_cost=1.0)},
        strategic_epoch=1,
        strategic_cohort_symbols=[symbol],
        strategic_cohort_targets={symbol: 0.95},
        candidate_tenure={
            "strategic_cohort_active": 1,
            "strategic_dominant_epoch": 1,
        },
        operating_peak=100.0,
        capital_peak=100.0,
    )
    strategy_target = Target(
        symbol=symbol,
        weight=0.95,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=1.0,
        confidence=1.0,
        reason="healthy strategic incumbent",
    )
    allocator = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(
        allocator,
        "_allocate_strategy",
        lambda **_: (strategy_target,),
    )

    reduced = allocator.allocate(
        date=pd.Timestamp("2026-08-19"),
        opportunity=Opportunity.TREND,
        risk=_sentinel_risk(0.70),
        user_panel={},
        leaders={},
        account=account,
        prices={symbol: 1.0},
    )

    assert sum(item.weight for item in reduced) == pytest.approx(0.70)
    assert reduced[0].reduction_policy == ReductionPolicy.RISK_PRIORITY.value
    assert reduced[0].reason_code == "sentinel_gross_cap"
    assert reduced[0].exit_kind == "portfolio_risk"
    assert reduced[0].origin_subsystem == OriginSubsystem.RISK.value
    assert reduced[0].mechanism == AttributionMechanism.RISK_GROSS_CAP.value


def test_nonbinding_sentinel_candidate_keeps_base_reduction_owner() -> None:
    risk = RiskAssessment(
        state=Risk.RISK_OFF,
        target_gross_cap=0.40,
        votes=0,
        evidence={
            "sentinel_cap": 0.70,
            "sentinel_cap_binding": False,
        },
        reasons=(),
        shock_state="NONE",
    )

    assert PortfolioAllocator._risk_reduction_metadata(risk) == (
        "portfolio risk-off gross cap",
        "risk_off",
        "risk_off",
    )


def test_paired_counterfactual_reports_non_accounting_cap_effects() -> None:
    dates = [f"2026-08-{day:02d}" for day in range(17, 22)]

    def result(
        equity: list[float],
        cash: list[float],
        *,
        candidate: bool,
    ) -> dict[str, object]:
        orders = [
            {
                "order_id": "base",
                "signal_date": dates[0],
                "reason_code": "strategy_target",
            }
        ]
        fills = [{"fill_date": dates[0], "gross_value": 10.0}]
        if candidate:
            orders.append(
                {
                    "order_id": "sentinel",
                    "signal_date": dates[1],
                    "reason_code": "sentinel_gross_cap",
                }
            )
            fills.append({"fill_date": dates[2], "gross_value": 20.0})
        return {
            "final_wealth": equity[-1] / 100.0,
            "max_drawdown": 1.0 - min(equity) / max(equity[: equity.index(min(equity)) + 1]),
            "account_orders": len(orders),
            "gross_turnover": sum(float(item["gross_value"]) for item in fills) / 100.0,
            "equity_curve": [
                {"date": date, "equity": value}
                for date, value in zip(dates, equity, strict=True)
            ],
            "decision_trace": [
                {
                    "date": date,
                    "risk": {
                        "target_gross_cap": (
                            0.70 if candidate and index >= 1 else 0.90
                        )
                    },
                    "target_gross": 0.70 if candidate and index >= 1 else 0.90,
                    "targets": [],
                    "orders": [],
                }
                for index, date in enumerate(dates)
            ],
            "order_ledger": orders,
            "final_account": {"fills": fills},
            "attribution": {
                "daily_ledger": [
                    {"date": date, "cash": value, "equity": total}
                    for date, value, total in zip(dates, cash, equity, strict=True)
                ]
            },
            "sentinel_events": (
                [
                    {"date": dates[1], "sentinel_cap_binding": True, "sentinel_cap": 0.7},
                    {"date": dates[2], "sentinel_cap_binding": True, "sentinel_cap": 0.7},
                ]
                if candidate
                else []
            ),
        }

    compared = compare_sentinel_cap_results(
        base=result([100.0, 90.0, 80.0, 100.0, 110.0], [0.0] * 5, candidate=False),
        candidate=result(
            [100.0, 92.0, 88.0, 98.0, 107.0],
            [0.0, 20.0, 20.0, 0.0, 0.0],
            candidate=True,
        ),
        benchmark_close=dict(zip(dates, [100.0, 90.0, 80.0, 100.0, 110.0], strict=True)),
        recovery_sessions=1,
    )

    assert compared["base_counterfactual"]["final_equity"] == 110.0
    assert compared["first_behavior_divergence"] == {
        "date": dates[1],
        "changed_fields": ["target_gross_cap", "target_gross"],
        "base": {"target_gross_cap": 0.90, "target_gross": 0.90},
        "candidate": {"target_gross_cap": 0.70, "target_gross": 0.70},
    }
    assert compared["avoided_drawdown"] == pytest.approx(0.08)
    assert compared["cash_drag"]["is_accounting_pnl"] is False
    assert compared["order_attribution"]["additional_orders"] == 1
    assert compared["turnover_attribution"]["additional_turnover"] == pytest.approx(0.20)
    assert compared["events"][0]["start"] == dates[1]
    assert compared["events"][0]["end"] == dates[2]
    assert compared["events"][0]["additional_orders"] == 1
    assert compared["events"][0]["sentinel_orders"] == 1
    assert compared["events"][0]["with_sentinel_equity"][0] == {
        "date": dates[1],
        "equity": 92.0,
    }
    assert compared["events"][0]["base_counterfactual_equity"][0] == {
        "date": dates[1],
        "equity": 90.0,
    }
    assert compared["events"][0]["post_release_recovery_cost"] >= 0.0
    assert compared["events"][0]["recovery_status"] == "OBSERVED"


def test_binding_event_at_window_end_is_explicitly_right_censored() -> None:
    dates = ["2026-08-17", "2026-08-18", "2026-08-19"]

    def result(*, candidate: bool) -> dict[str, object]:
        return {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "equity_curve": [
                {"date": date, "equity": 100.0} for date in dates
            ],
            "decision_trace": [
                {
                    "date": date,
                    "risk": {"target_gross_cap": 0.7 if candidate else 1.0},
                    "target_gross": 0.7 if candidate else 1.0,
                    "targets": [],
                    "orders": [],
                }
                for date in dates
            ],
            "order_ledger": [],
            "final_account": {"fills": []},
            "attribution": {
                "daily_ledger": [
                    {"date": date, "cash": 100.0, "equity": 100.0}
                    for date in dates
                ]
            },
            "sentinel_events": (
                [{"date": dates[-1], "sentinel_cap_binding": True}]
                if candidate
                else []
            ),
        }

    compared = compare_sentinel_cap_results(
        base=result(candidate=False),
        candidate=result(candidate=True),
        benchmark_close={date: 100.0 for date in dates},
        recovery_sessions=1,
    )

    event = compared["events"][0]
    assert event["release"] is None
    assert event["recovery_horizon"] is None
    assert event["post_release_recovery_cost"] is None
    assert event["recovery_status"] == "RIGHT_CENSORED"
    assert compared["post_release_recovery_cost"] == {
        "observed_total": 0,
        "observed_events": 0,
        "right_censored_events": 1,
    }
