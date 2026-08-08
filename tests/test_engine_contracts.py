from __future__ import annotations

import copy
import json

import pytest

from unified_ai_quant.account import load_account, save_account
from unified_ai_quant.engine import ProductionEngine
from unified_ai_quant.report import render_daily_report
from unified_ai_quant.types import AccountState

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
