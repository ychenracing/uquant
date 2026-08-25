from __future__ import annotations

import json

import pandas as pd
import pytest
from test_engine_contracts import (
    SYMBOLS,
)

from uquant import engine as engine_module
from uquant.account import load_account
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import (
    ProductionEngine,
)
from uquant.report import render_daily_report
from uquant.risk_sentinel.models import (
    CoverageHealth,
    SentinelAssessment,
    SentinelLevel,
    WarmupStatus,
)
from uquant.types import (
    AccountOrder,
    AccountState,
    Fill,
    Position,
)
from uquant.validation.universe import default_ai_universe


def test_account_root_must_be_a_json_object(tmp_path):
    malformed = tmp_path / "array.json"
    malformed.write_text("[]", encoding="utf-8")

    with pytest.raises(RuntimeError, match="JSON object"):
        load_account(malformed)

def test_account_nested_collections_must_match_the_schema(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    payload = state.to_dict()
    payload["positions"] = []
    malformed = tmp_path / "invalid-positions.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="violates schema"):
        load_account(malformed)

def test_broker_order_metric_excludes_unfilled_submissions():
    orders = [
        AccountOrder(
            order_id="O000000001",
            signal_date="2026-01-05",
            submitted_date="2026-01-05",
            symbol="sz300308",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
            requested_shares=100,
            filled_shares=100,
            status="FILLED",
        ),
        AccountOrder(
            order_id="O000000002",
            signal_date="2026-01-06",
            submitted_date="2026-01-06",
            symbol="sz300502",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
            requested_shares=100,
            status="OPEN",
        ),
    ]
    fills = [
        Fill(
            signal_date="2026-01-05",
            fill_date="2026-01-06",
            symbol="sz300308",
            side="BUY",
            shares=100,
            price=10.0,
            gross_value=1000.0,
            commission=5.0,
            stamp_duty=0.0,
            transfer_fee=0.1,
            slippage_cost=0.0,
            reason="entry",
            lifecycle="CORE",
            order_id="O000000001",
        )
    ]
    from uquant.engine import performance_metrics

    metrics = performance_metrics(
        equity_rows=[
            (pd.Timestamp("2026-01-05"), 2e6),
            (pd.Timestamp("2026-01-06"), 2e6),
        ],
        fills=fills,
        orders=orders,
        initial_cash=2e6,
        risk_events=[],
        benchmark_total_return=0.0,
    )
    assert metrics["account_orders"] == 1
    assert metrics["submitted_account_orders"] == 2
    assert len(metrics["order_ledger"]) == 1
    assert len(metrics["submission_ledger"]) == 2

def test_backtest_and_daily_share_decision_kernel(data_dir):
    engine = ProductionEngine(data_dir)
    account = AccountState.empty(2e6)
    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=account)
    report = render_daily_report(decision, account)
    assert decision.decision_digest in report
    assert config_fingerprint(engine.cfg) in report
    assert "Opportunity" in report and "Tomorrow" in report

def test_structured_sector_guard_counts_as_first_risk_reduction():
    from uquant.engine import performance_metrics

    reduced = Fill(
        signal_date="2026-01-05",
        fill_date="2026-01-06",
        symbol="sz300308",
        side="SELL",
        shares=100,
        price=10.0,
        gross_value=1_000.0,
        commission=5.0,
        stamp_duty=0.5,
        transfer_fee=0.1,
        slippage_cost=0.0,
        reason="portfolio rebalance",
        lifecycle="CORE",
        exit_kind="sector_guard",
    )

    observed = performance_metrics(
        equity_rows=[
            (pd.Timestamp("2026-01-05"), 2e6),
            (pd.Timestamp("2026-01-06"), 1.99e6),
        ],
        fills=[reduced],
        orders=[],
        initial_cash=2e6,
        risk_events=[],
        benchmark_total_return=0.0,
    )

    assert observed["first_reduce"] == "2026-01-06"

def test_decision_keeps_omitted_durable_symbols_in_strategy_panel(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from uquant.types import Risk, RiskAssessment

    omitted = SYMBOLS[3]
    account = AccountState(
        initial_cash=2e6,
        cash=1_900_000.0,
        positions={
            omitted: Position(
                omitted,
                shares=1_000,
                avg_cost=100.0,
                entry_date="2026-01-05",
            )
        },
        protected_weights={omitted: 0.05},
        operating_peak=2e6,
        capital_peak=2e6,
    )
    observed: dict[str, set[str]] = {}

    def normal_risk(**kwargs):
        observed["user_panel"] = set(kwargs["user_panel"])
        return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    monkeypatch.setattr("uquant.engine.assess_risk", normal_risk)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS[:3],
        as_of="2026-06-30",
        account=account,
    )

    assert omitted in observed["user_panel"]

def test_decision_keeps_sector_guard_cohort_in_risk_panel(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
):
    from uquant.types import Risk, RiskAssessment

    omitted = SYMBOLS[3]
    account = AccountState.empty(2e6)
    account.sector_guard_active = True
    account.sector_guard_started = "2026-06-20"
    account.sector_guard_symbols = [omitted]
    observed: dict[str, set[str]] = {}

    def normal_risk(**kwargs):
        observed["user_panel"] = set(kwargs["user_panel"])
        return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    monkeypatch.setattr("uquant.engine.assess_risk", normal_risk)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS[:3],
        as_of="2026-06-30",
        account=account,
    )

    assert omitted in observed["user_panel"]

def test_decision_evaluates_sentinel_from_canonical_point_in_time_universe(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uquant.types import Risk, RiskAssessment

    assessment = SentinelAssessment(
        date="2026-06-30",
        level=SentinelLevel.NORMAL,
        confidence=1.0,
        suggested_gross_cap=None,
        freeze_new_risk=False,
        evidence_families=(),
        reasons=("no independent risk family triggered",),
        first_evidence_date=None,
        coverage=CoverageHealth(
            status=WarmupStatus.READY,
            confidence=1.0,
            component_observation=1.0,
            subindustry_coverage=1.0,
            held_industry_mapping=1.0,
            reference_warmup=1.0,
            missing_indices=(),
            new_symbols=(),
            stale_symbols=(),
        ),
        metrics={"evidence_confirmation_days": 0.0},
    )
    observed: dict[str, object] = {}

    def sentinel(**kwargs: object) -> SentinelAssessment:
        observed["sentinel"] = kwargs
        return assessment

    def normal_risk(**kwargs: object) -> RiskAssessment:
        observed["risk"] = kwargs
        return RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NONE")

    monkeypatch.setattr(engine_module, "evaluate_sentinel", sentinel)
    monkeypatch.setattr(engine_module, "assess_risk", normal_risk)
    ProductionEngine(
        data_dir,
        DEFAULT_CONFIG.override(risk_sentinel_mode="FREEZE_ONLY"),
    ).decide(
        symbols=SYMBOLS,
        as_of="2026-06-30",
        account=AccountState.empty(2e6),
    )

    sentinel_args = observed["sentinel"]
    assert isinstance(sentinel_args, dict)
    industries = sentinel_args["point_in_time_industries"]
    assert isinstance(industries, dict)
    universe = default_ai_universe()
    assert tuple(sorted(industries)) == universe.symbols_as_of("2026-06-30")
    assert industries == {
        symbol: universe.industry_of(symbol, "2026-06-30")
        for symbol in universe.symbols_as_of("2026-06-30")
    }
    risk_args = observed["risk"]
    assert isinstance(risk_args, dict)
    assert risk_args["sentinel_assessment"] is assessment

def test_shadow_mode_keeps_sentinel_out_of_the_production_decision_path(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(**_: object) -> SentinelAssessment:
        raise AssertionError("Shadow Sentinel must remain outside production decisions")

    monkeypatch.setattr(engine_module, "evaluate_sentinel", forbidden)

    ProductionEngine(
        data_dir, DEFAULT_CONFIG.override(risk_sentinel_mode="SHADOW")
    ).decide(
        symbols=SYMBOLS,
        as_of="2026-06-30",
        account=AccountState.empty(2e6),
    )

def test_decision_routes_sentinel_pending_buy_cancellation_through_execution(
    data_dir,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uquant.types import Risk, RiskAssessment

    observed: dict[str, object] = {}

    def sentinel_risk(**_: object) -> RiskAssessment:
        return RiskAssessment(
            Risk.NORMAL,
            1.0,
            0,
            {
                "base_freeze_new_risk": False,
                "sentinel_freeze_new_risk": True,
                "freeze_new_risk": True,
            },
            (),
            "NONE",
            freeze_new_risk=True,
        )

    original = engine_module.reconcile_account_orders

    def reconcile(**kwargs: object):
        observed.update(kwargs)
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(engine_module, "assess_risk", sentinel_risk)
    monkeypatch.setattr(engine_module, "reconcile_account_orders", reconcile)
    ProductionEngine(data_dir).decide(
        symbols=SYMBOLS,
        as_of="2026-06-30",
        account=AccountState.empty(2e6),
    )

    assert observed["removed_buy_reason"] == "sentinel_freeze_new_risk"
