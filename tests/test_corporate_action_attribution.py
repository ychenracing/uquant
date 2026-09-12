"""Source-rebuilt corporate-action income, rights, tax, and lot attribution."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest
from test_corporate_actions import _input
from test_execution import _canonical_pending

from uquant.account.corporate_actions import apply_corporate_actions, corporate_action_receivable
from uquant.attribution import (
    build_daily_ledger_row,
    build_daily_replay_evidence_row,
    build_economic_attribution,
    validate_attribution_against_engine_result,
)
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.account import AccountState


def _result(*, split: bool = False, delivered: bool = True, sale: bool = False, second_buy: bool = False, empty: bool = False):
    action, panel = _input()
    if split:
        action = replace(action, share_ratio=1.0, reference_share_ratio=1.0,
                         share_available_date=action.ex_date if delivered else "2023-08-14",
                         share_tax_acquisition_rule="original_acquisition")
        panel[action.symbol].loc[pd.Timestamp(action.ex_date), ["open", "high", "low", "close", "reference_close"]] /= 2
    account = AccountState.empty(1_000_000.0)
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    sessions = ("2023-08-09", "2023-08-10", "2023-08-11")
    ledger, evidence, curve = [], [], []
    previous = account.initial_cash
    for day in sessions:
        apply_corporate_actions(account, [action], date=day, phase="open")
        if day == sessions[0] and not empty:
            account.pending_orders = [_canonical_pending("2023-08-08", action.symbol, "BUY", 0.1, "entry")]
        elif second_buy and day == action.record_date:
            account.pending_orders = [_canonical_pending(sessions[0], action.symbol, "BUY", 0.2, "second origin")]
        elif sale and day == action.ex_date:
            account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "SELL", 0, "exit")]
        planner.execute_open(date=pd.Timestamp(day), account=account, panel=panel)
        apply_corporate_actions(account, [action], date=day, phase="close")
        symbols = set(account.positions)
        if split and not delivered and day == action.ex_date:
            symbols.add(action.symbol)
        marks = {symbol: float(panel[symbol].loc[pd.Timestamp(day), "close"]) for symbol in symbols}
        row = build_daily_ledger_row(date=day, account=account, close_prices=marks,
            previous_equity=previous, target_weights={}, target_gross=0.0,
            risk_gross_cap=1.0, system_gross_cap=1.0, risk_state="NORMAL", opportunity="CHOPPY")
        ledger.append(row)
        evidence.append(build_daily_replay_evidence_row(date=day, account=account, close_prices=marks))
        curve.append({"date": day, "equity": row["equity"]})
        previous = row["equity"]
    attribution = build_economic_attribution(account=account, final_prices=marks,
        sessions=sessions, economic_start=sessions[0], economic_end=sessions[-1],
        final_equity=previous, daily_ledger=ledger, benchmark_close=dict.fromkeys(sessions, 100.0))
    assert previous == pytest.approx(account.cash + sum(position.shares * marks[symbol]
        for symbol, position in account.positions.items()) + corporate_action_receivable(account, marks))
    result = {"attribution": attribution, "final_account": account.to_dict(),
              "final_equity": previous, "final_wealth": previous / account.initial_cash,
              "start": sessions[0], "end": sessions[-1],
              "gross_turnover": sum(fill.gross_value for fill in account.fills) / account.initial_cash,
              "symbol_pnl": {symbol: values["total_pnl"] for symbol, values in attribution["by_symbol"].items()},
              "daily_replay_evidence": evidence, "equity_curve": curve}
    return result


@pytest.mark.parametrize(("split", "delivered", "sale"), [(False, True, True), (True, True, False), (True, False, True)])
def test_source_replay_reconciles_dividend_tax_and_delivered_or_pending_bonus(split, delivered, sale):
    result = _result(split=split, delivered=delivered, sale=sale)
    canonical = validate_attribution_against_engine_result(result,
        economic_start=result["start"], economic_end=result["end"], require_daily_replay_evidence=True,
        **_trusted_sources(split=split, delivered=delivered))
    state = result["final_account"]["corporate_actions"][0]
    assert sum(lot.get("dividend_income", 0.0) for lot in canonical["lots"]) == pytest.approx(state["income_cash"])
    assert sum(lot.get("dividend_tax", 0.0) for lot in canonical["lots"]) == pytest.approx(state["tax_assessed"])
    if split:
        assert sum(lot["shares"] for lot in canonical["lots"]) == 2 * result["final_account"]["fills"][0]["shares"]
        assert sum(lot["entry_gross_value"] for lot in canonical["lots"]) == pytest.approx(result["final_account"]["fills"][0]["gross_value"])


def test_corporate_replay_rejects_missing_source_and_fabricated_daily_rights():
    result = _result(split=True, delivered=False, sale=True)
    missing = deepcopy(result)
    missing["final_account"]["corporate_actions"] = []
    with pytest.raises(ValueError, match="source state"):
        validate_attribution_against_engine_result(missing, economic_start=result["start"], economic_end=result["end"], **_trusted_sources(split=True, delivered=False))
    tampered = deepcopy(result)
    tampered["daily_replay_evidence"][-1]["corporate_action_receivable"] += 1.0
    with pytest.raises(ValueError, match="source-rebuilt corporate receivable"):
        validate_attribution_against_engine_result(tampered, economic_start=result["start"], economic_end=result["end"], **_trusted_sources(split=True, delivered=False))
    bad_basis = deepcopy(result)
    bad_basis["final_account"]["fills"][-1]["sold_tranches"][0]["cost_basis"] += 1.0
    with pytest.raises(ValueError, match="sold basis"):
        validate_attribution_against_engine_result(bad_basis, economic_start=result["start"], economic_end=result["end"], **_trusted_sources(split=True, delivered=False))


def test_dividend_and_tax_follow_each_original_entry_after_split_and_sale():
    result = _result(split=True, delivered=False, sale=True, second_buy=True)
    canonical = validate_attribution_against_engine_result(result,
        economic_start=result["start"], economic_end=result["end"], **_trusted_sources(split=True, delivered=False))
    state = result["final_account"]["corporate_actions"][0]
    assert len(state["entitled_lots"]) == 2
    for entitled in state["entitled_lots"]:
        rows = [row for row in canonical["lots"] if row["origin_event_id"] == entitled["event_id"]]
        income = entitled["shares"] * state["action"]["cash_per_share"]
        assert sum(row.get("dividend_income", 0.0) for row in rows) == pytest.approx(income)
        assert sum(row.get("dividend_tax", 0.0) for row in rows) == pytest.approx(income * 0.2)


def _trusted_sources(*, split=False, delivered=True):
    action, _ = _input()
    if split:
        action = replace(action, share_ratio=1.0, reference_share_ratio=1.0,
                         share_available_date=action.ex_date if delivered else "2023-08-14",
                         share_tax_acquisition_rule="original_acquisition")
    return {"trusted_corporate_actions": (action,), "trusted_tax_debits": ()}


def test_report_cannot_self_authenticate_corporate_action_or_tax_debit_sources():
    result = _result(sale=True)
    with pytest.raises(ValueError, match="independently trusted"):
        validate_attribution_against_engine_result(result, economic_start=result["start"],
            economic_end=result["end"], require_daily_replay_evidence=True)
    for field, forged in (("source_url", "https://forged.example/action"), ("source_sha256", "f" * 64)):
        changed = deepcopy(result)
        changed["final_account"]["corporate_actions"][0]["action"][field] = forged
        with pytest.raises(ValueError, match="independently trusted actions"):
            validate_attribution_against_engine_result(changed, economic_start=result["start"],
                economic_end=result["end"], require_daily_replay_evidence=True, **_trusted_sources())
    changed = deepcopy(result)
    changed["final_account"]["dividend_tax_debits"] = [{
        "debit_id": "forged", "action_id": "cninfo:1217467414", "date": result["end"],
        "phase": "close", "amount": 1.0, "source_url": "https://forged.example/tax", "source_sha256": "f" * 64,
    }]
    with pytest.raises(ValueError, match="independently trusted debits"):
        validate_attribution_against_engine_result(changed, economic_start=result["start"],
            economic_end=result["end"], require_daily_replay_evidence=True, **_trusted_sources())


def test_pending_share_rights_exposure_matches_native_current_weights_without_double_nav():
    from uquant.attribution.replay_evidence import corporate_account_from_payload
    from uquant.portfolio_core import current_weights

    result = _result(split=True, delivered=False, sale=True)
    account = corporate_account_from_payload(result["final_account"])
    marks = result["daily_replay_evidence"][-1]["close_marks"]
    expected_weights, expected_equity = current_weights(account, marks)
    row = result["attribution"]["daily_ledger"][-1]
    assert not account.positions
    assert row["corporate_action_share_rights_value"] > 0.0
    assert row["position_weights"] == pytest.approx(expected_weights)
    assert row["gross_exposure"] == pytest.approx(sum(abs(weight) for weight in expected_weights.values()))
    assert row["net_exposure"] == pytest.approx(sum(expected_weights.values()))
    assert row["equity"] == pytest.approx(expected_equity)
    assert row["equity"] == pytest.approx(account.cash + corporate_action_receivable(account, marks))
    validate_attribution_against_engine_result(result, economic_start=result["start"],
        economic_end=result["end"], require_daily_replay_evidence=True,
        **_trusted_sources(split=True, delivered=False))


def test_direct_daily_replay_rejects_deleted_actions_even_for_an_empty_account():
    from uquant.attribution.replay_evidence import validate_daily_replay_evidence

    result = _result(empty=True)

    def validate(candidate):
        account = candidate["final_account"]
        validate_daily_replay_evidence(
            result=candidate, attribution=candidate["attribution"], account=account,
            fills=account["fills"], positions=account["positions"],
            economic_start=candidate["start"], economic_end=candidate["end"],
            trusted_sessions=None, trusted_close=None, **_trusted_sources(),
        )

    assert not result["final_account"]["fills"]
    assert not result["final_account"]["positions"]
    validate(result)
    deleted = deepcopy(result)
    deleted["final_account"]["corporate_actions"] = []
    for row in deleted["attribution"]["daily_ledger"]:
        row.pop("corporate_action_receivable", None)
        row.pop("corporate_action_share_rights_value", None)
    for row in deleted["daily_replay_evidence"]:
        row.pop("corporate_action_receivable", None)
    with pytest.raises(ValueError, match="source state differs from independently trusted actions"):
        validate(deleted)
