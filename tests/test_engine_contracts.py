from __future__ import annotations

import copy
import json

import pandas as pd
import pytest

from unified_ai_quant.account import load_account, save_account
from unified_ai_quant.engine import ProductionEngine
from unified_ai_quant.report import render_daily_report
from unified_ai_quant.types import AccountOrder, AccountState, Fill
from unified_ai_quant.validation.runner import POOLS

SYMBOLS = ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"]


def test_determinism_one_target_and_hard_constraints(data_dir):
    engine = ProductionEngine(data_dir)
    initial = AccountState.empty(2e6)
    first, state1 = engine.deterministic_decision(symbols=SYMBOLS, as_of="2026-06-30", account=initial)
    second, state2 = engine.deterministic_decision(
        symbols=list(reversed(SYMBOLS)), as_of="2026-06-30", account=initial
    )
    assert first.decision_digest == second.decision_digest
    assert state1.to_dict() == state2.to_dict()
    assert len({item.symbol for item in first.targets}) == len(first.targets)
    positive = [item for item in first.targets if item.weight > 0]
    assert len(positive) <= 6
    assert sum(item.weight for item in positive) <= 1.0 + 1e-9
    assert max((item.weight for item in positive), default=0.0) <= 0.60


def test_state_round_trip_and_fail_closed_hashes(data_dir, tmp_path):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    decision = engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)
    state.pending_orders = list(decision.pending_orders)
    state.strategic_cohort_symbols = ["sz300308", "sz300394", "sz300502"]
    state.strategic_cohort_targets = {"sz300308": 0.30}
    state.strategic_exit_bands = {"sz300308": [0.10, 0.08, 0.06]}
    state.strategic_active_bands = {"sz300308": [True, False, False]}
    state.strategic_restore_weights = {"sz300308": 0.30}
    path = tmp_path / "account.json"
    save_account(state, path)
    assert load_account(path).to_dict() == state.to_dict()
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_account(corrupt)
    missing_hash = tmp_path / "missing.json"
    payload = copy.deepcopy(state.to_dict())
    payload["data_hash"] = ""
    missing_hash.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_account(missing_hash)


def test_order_state_migrates_sequence_and_rejects_broken_references(tmp_path):
    state = AccountState.empty(2e6)
    state.data_hash = "data"
    state.code_hash = "code"
    state.order_ledger = [
        AccountOrder(
            order_id="O000000007",
            signal_date="2026-01-05",
            submitted_date="2026-01-05",
            symbol="sz300308",
            side="BUY",
            target_weight=0.5,
            reason="entry",
            lifecycle="CORE",
        )
    ]
    payload = state.to_dict()
    payload.pop("next_order_sequence")
    migrated = tmp_path / "migrated-account.json"
    migrated.write_text(json.dumps(payload), encoding="utf-8")
    assert load_account(migrated).next_order_sequence == 8

    payload["next_order_sequence"] = 7
    collision = tmp_path / "collision-account.json"
    collision.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="reuse an order id"):
        load_account(collision)

    payload["next_order_sequence"] = 8
    payload["pending_orders"] = [
        {
            "signal_date": "2026-01-05",
            "symbol": "sz300308",
            "side": "BUY",
            "target_weight": 0.5,
            "reason": "entry",
            "lifecycle": "CORE",
            "remaining_shares": 0,
            "attempts": 0,
            "order_id": "O000000999",
        }
    ]
    unknown = tmp_path / "unknown-order-account.json"
    unknown.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown account order"):
        load_account(unknown)


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
    from unified_ai_quant.engine import performance_metrics

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
    assert "Opportunity" in report and "Tomorrow" in report


def test_future_dated_state_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.last_successful_run = "2027-01-01"
    with pytest.raises(RuntimeError):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)


def test_stale_code_hash_fails_closed(data_dir):
    engine = ProductionEngine(data_dir)
    state = AccountState.empty(2e6)
    state.code_hash = "stale-code-hash"
    with pytest.raises(RuntimeError, match="code hash"):
        engine.decide(symbols=SYMBOLS, as_of="2026-06-30", account=state)


def test_pre_listing_symbols_are_point_in_time_invisible(data_dir):
    result = ProductionEngine(data_dir).backtest(
        symbols=(*SYMBOLS, "sh688072"),
        start="2022-01-04",
        end="2022-02-28",
    )
    assert result["start"] == "2022-01-04"
    assert result["final_wealth"] > 0


def test_historical_reference_coverage_is_point_in_time_dynamic(data_dir):
    result = ProductionEngine(data_dir).backtest(
        symbols=(*SYMBOLS, "sh688072", "sh688300", "sh688361"),
        start="2018-12-27",
        end="2018-12-28",
    )
    assert result["start"] == "2018-12-27"
    assert result["end"] == "2018-12-28"
    assert result["final_wealth"] > 0


def test_consumed_failed_promotion_window_is_now_a_research_risk_regression(
    data_dir,
):
    engine = ProductionEngine(data_dir)
    for symbols in POOLS.values():
        result = engine.backtest(
            symbols=symbols,
            start="2026-07-21",
            end="2026-08-05",
        )
        assert result["final_wealth"] > 0.85
        assert result["max_drawdown"] < 0.15
        assert result["account_orders"] <= 3
