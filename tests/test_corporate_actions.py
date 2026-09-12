"""Actual 688008 distribution notice/quotes through the native execution ledger."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from test_execution import _canonical_pending, _frame

from uquant.account.codec import account_from_dict
from uquant.account.corporate_actions import (
    CorporateAction,
    apply_corporate_actions,
    corporate_action_receivable,
    dividend_tax_rate,
)
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.models.account import AccountState

FIXTURES = Path(__file__).parent / "fixtures" / "corporate_actions"


def _input():
    metadata = json.loads((FIXTURES / "sh688008.json").read_text())
    notice = (FIXTURES / "sh688008_notice.txt").read_bytes()
    assert hashlib.sha256(notice).hexdigest() == metadata["notice_text_sha256"]
    assert "每股现金红利 0.30 元" in notice.decode()
    assert "2023/8/10             2023/8/11              2023/8/11" in notice.decode()
    action = CorporateAction(
        action_id="cninfo:1217467414", symbol="sh688008", disclosed_date="2023-08-05",
        record_date="2023-08-10", ex_date="2023-08-11", payment_date="2023-08-11",
        payment_phase="close", cash_per_share=0.30, cash_adjustment_per_share=0.30, share_ratio=0.0, reference_share_ratio=0.0,
        share_available_date="", share_tax_acquired_date="", share_tax_acquisition_rule="", source_url=metadata["source_url"],
        source_sha256=metadata["source_sha256"], tax_category="cn_individual_dividend",
        bonus_tax_per_share=0.0,
    )
    panel = {action.symbol: _frame(metadata["quotes"])}
    panel[action.symbol].loc[pd.Timestamp(action.ex_date), "reference_close"] = 56.57
    panel[action.symbol]["reference_close"] = panel[action.symbol]["reference_close"].fillna(
        panel[action.symbol]["close"].shift()
    )
    return action, panel


def _bought_account():
    action, panel = _input()
    account = AccountState.empty(1_000_000.0)
    account.pending_orders = [_canonical_pending("2023-08-08", action.symbol, "BUY", 0.1, "source case")]
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=pd.Timestamp("2023-08-09"), account=account, panel=panel)
    assert account.positions[action.symbol].shares > 0
    return account, action, panel


def test_actual_notice_cash_equity_restart_and_tax_liability():
    account, action, panel = _bought_account()
    shares = account.positions[action.symbol].shares
    before_cash = account.cash
    before_cost = account.positions[action.symbol].avg_cost
    before_peak = account.positions[action.symbol].highest_close
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    account = account_from_dict(account.to_dict(), require_hashes=False)
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    assert account.cash == before_cash
    assert corporate_action_receivable(account) == pytest.approx(shares * 0.30)
    assert account.positions[action.symbol].shares == shares
    assert account.positions[action.symbol].avg_cost == before_cost
    assert account.positions[action.symbol].highest_close == pytest.approx(before_peak - 0.30)
    account = account_from_dict(account.to_dict(), require_hashes=False)
    snapshot = account.to_dict()
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    assert account.to_dict() == snapshot
    account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "SELL", 0, "exit")]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=pd.Timestamp(action.ex_date), account=account, panel=panel)
    sale = fills[0]
    assert sale.price == pytest.approx(56.52 * (1 - DEFAULT_CONFIG.slippage))
    state = account.corporate_actions[0]
    assert state.tax_assessed == pytest.approx(shares * 0.30 * 0.2)
    assert state.tax_cash == 0  # Actual debit needs subsequent broker evidence.
    assert corporate_action_receivable(account) == pytest.approx(shares * 0.30 - state.tax_assessed)
    # Issuer specifies the date; this supported scenario confirms credit at close.
    apply_corporate_actions(account, [action], date=action.ex_date, phase="close")
    assert corporate_action_receivable(account) == pytest.approx(-state.tax_assessed)
    assert account.cash == pytest.approx(before_cash + shares * 0.30 + sale.gross_value - sale.commission - sale.stamp_duty - sale.transfer_fee)
    account_from_dict(account.to_dict(), require_hashes=False)
    with pytest.raises(ValueError, match="chronological"):
        apply_corporate_actions(account, [action], date=action.record_date, phase="close")


def test_no_record_holding_gets_no_dividend_even_after_ex_buy():
    action, panel = _input()
    account = AccountState.empty(1_000_000.0)
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "BUY", 0.1, "after record")]
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=pd.Timestamp(action.ex_date), account=account, panel=panel)
    assert account.corporate_actions[0].income_cash == 0
    assert account.corporate_actions[0].entitled_lots == []
    account_from_dict(account.to_dict(), require_hashes=False)


def test_source_and_missing_record_fail_closed():
    account, action, _ = _bought_account()
    with pytest.raises(ValueError, match="source URL"):
        apply_corporate_actions(account, [replace(action, source_sha256="")], date=action.record_date, phase="close")
    with pytest.raises(ValueError, match="missing record-date"):
        apply_corporate_actions(account, [action], date=action.ex_date, phase="open")


@pytest.mark.parametrize(("sale", "expected"), [("2023-02-08", .2), ("2023-02-09", .1), ("2024-01-08", .1), ("2024-01-09", 0)])
def test_statutory_calendar_holding_boundaries(sale, expected):
    assert dividend_tax_rate("2023-01-08", sale) == expected


def test_separate_reference_ratio_and_share_delivery_preserve_cost_and_origin():
    """Constructed distribution exercises fractional reference, integer real award."""
    account, cash_action, _ = _bought_account()
    action = replace(cash_action, cash_per_share=0, cash_adjustment_per_share=0,
                     share_ratio=1, reference_share_ratio=.8,
                     share_available_date="2023-08-14", share_tax_acquired_date="",
                     share_tax_acquisition_rule="original_acquisition")
    shares = account.positions[action.symbol].shares
    cost = account.positions[action.symbol].avg_cost
    peak = account.positions[action.symbol].highest_close
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    position = account.positions[action.symbol]
    assert position.shares == shares
    assert position.avg_cost == cost / 2
    assert position.highest_close == pytest.approx(peak / 1.8)
    assert corporate_action_receivable(account, {action.symbol: 30}) == shares * 30
    account = account_from_dict(account.to_dict(), require_hashes=False)
    apply_corporate_actions(account, [action], date=action.ex_date, phase="close")
    apply_corporate_actions(account, [action], date="2023-08-14", phase="open")
    assert account.positions[action.symbol].shares == 2 * shares
    assert account.positions[action.symbol].avg_cost == cost / 2
    assert account.positions[action.symbol].sellable_shares("2023-08-14") == 2 * shares
    assert corporate_action_receivable(account) == 0
    account_from_dict(account.to_dict(), require_hashes=False)
    payload = account.to_dict()
    payload["corporate_actions"][0]["entitled_lots"][0]["shares"] += 100
    with pytest.raises(RuntimeError, match="record-date lots differ"):
        account_from_dict(payload, require_hashes=False)


def test_fractional_share_rights_are_not_fabricated():
    account, cash_action, _ = _bought_account()
    action = replace(cash_action, share_ratio=.0001, share_available_date=cash_action.ex_date,
                     share_tax_acquisition_rule="original_acquisition")
    with pytest.raises(ValueError, match="fractional entitlement"):
        apply_corporate_actions(account, [action], date=action.record_date, phase="close")


def test_tax_fifo_is_independent_of_risk_priority_sale_allocation():
    action, panel = _input()
    account = AccountState.empty(1_000_000)
    engine = ExecutionPlanner(DEFAULT_CONFIG)
    account.pending_orders = [_canonical_pending("2022-08-08", action.symbol, "BUY", .15, "old lot")]
    engine.execute_open(date=pd.Timestamp("2022-08-09"), account=account, panel=panel)
    old_shares = account.positions[action.symbol].shares
    account.pending_orders = [_canonical_pending("2023-08-08", action.symbol, "BUY", .20, "new lot")]
    engine.execute_open(date=pd.Timestamp("2023-08-09"), account=account, panel=panel)
    position = account.positions[action.symbol]
    new_shares = position.shares - old_shares
    assert 0 < new_shares <= old_shares
    position.tranches[-1].mae = -.5
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    price = float(panel[action.symbol].loc[pd.Timestamp(action.ex_date), "open"])
    equity = account.cash + position.shares * price + corporate_action_receivable(account)
    desired_weight = (old_shares + .01) * price * (1 - DEFAULT_CONFIG.slippage) / equity
    account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "SELL", desired_weight,
                                                "risk reduction", reduction_policy="RISK_PRIORITY")]
    fills = engine.execute_open(date=pd.Timestamp(action.ex_date), account=account, panel=panel)
    assert fills[0].shares == new_shares
    assert fills[0].sold_tranches[0]["entry_date"] == "2023-08-09"
    assert account.corporate_actions[0].tax_assessed == 0
    account_from_dict(account.to_dict(), require_hashes=False)


def test_tax_debit_requires_evidence_and_is_not_double_spent():
    from uquant.models.corporate_action import DividendTaxDebit

    account, action, panel = _bought_account()
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "SELL", 0, "exit")]
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=pd.Timestamp(action.ex_date), account=account, panel=panel)
    apply_corporate_actions(account, [action], date=action.ex_date, phase="close")
    tax = account.corporate_actions[0].tax_assessed
    # Constructed broker evidence tests posting semantics, not actual broker history.
    debit = DividendTaxDebit("fixture-debit", action.action_id, "2023-08-14", "close", tax,
                             "https://example.test/broker-statement", "a" * 64)
    cash = account.cash
    apply_corporate_actions(account, [action], date=debit.date, phase="open", tax_debits=[debit])
    assert account.cash == cash
    apply_corporate_actions(account, [action], date=debit.date, phase="close", tax_debits=[debit])
    assert account.cash == pytest.approx(cash - tax)
    assert corporate_action_receivable(account) == 0
    restored = account_from_dict(account.to_dict(), require_hashes=False)
    apply_corporate_actions(restored, [action], date=debit.date, phase="close", tax_debits=[debit])
    assert restored.to_dict() == account.to_dict()
    corrupt = account.to_dict()
    corrupt["corporate_actions"][0]["tax_assessed"] = 0
    with pytest.raises(RuntimeError, match="assessed tax differs"):
        account_from_dict(corrupt, require_hashes=False)


def test_reviewed_original_bytes_feed_native_engine_execution_and_nav(tmp_path):
    """Actual notice/quotes, with an explicitly assumed close-credit scenario."""
    from dataclasses import asdict

    from uquant.engine import ProductionEngine
    from uquant.portfolio_core import current_weights

    action, panel = _input()
    pdf = (FIXTURES / "sh688008_notice.pdf").read_bytes()
    assert hashlib.sha256(pdf).hexdigest() == action.source_sha256
    (tmp_path / "notice.pdf").write_bytes(pdf)
    quotes = panel[action.symbol].loc["2023-08-08":].drop(columns="reference_close")
    quotes.to_csv(tmp_path / "sh688008.csv")
    document = {
        "schema": "historical-raw-shares", "start": "2023-08-08", "end": "2023-08-14",
        "sessions": [str(day.date()) for day in quotes.index],
        "files": {action.symbol: hashlib.sha256((tmp_path / "sh688008.csv").read_bytes()).hexdigest()},
        "sources": {"issuer_notice": {"path": "notice.pdf", "url": action.source_url,
                                       "sha256": action.source_sha256}},
        "coverage": {action.symbol: {"reviewed_from": "2023-08-08", "reviewed_through": "2023-08-14",
                                      "action_sources": ["issuer_notice"], "absent_sessions": {}}},
        "actions": [asdict(action)], "tax_debits": [],
    }
    (tmp_path / "ACCOUNT_INPUT.json").write_text(json.dumps(document))
    engine = ProductionEngine(tmp_path)
    engine._load([action.symbol])
    reviewed = engine.data.account_input
    assert reviewed is not None
    account = AccountState.empty(1_000_000)
    account.pending_orders = [_canonical_pending("2023-08-08", action.symbol, "BUY", .1, "source case")]
    raw = {action.symbol: engine._raw[action.symbol]}
    apply_corporate_actions(account, reviewed.actions, date="2023-08-09", phase="open")
    buys = engine.execution.execute_open(date=pd.Timestamp("2023-08-09"), account=account, panel=raw)
    shares = buys[0].shares
    apply_corporate_actions(account, reviewed.actions, date=action.record_date, phase="close")
    cash = account.cash
    apply_corporate_actions(account, reviewed.actions, date=action.ex_date, phase="open")
    assert engine.equity(account, pd.Timestamp(action.ex_date), "open") == pytest.approx(
        cash + shares * (56.52 + .30)
    )
    assert account.cash == cash  # Payment-date notice alone does not establish open cash.
    close_equity = cash + shares * (54.55 + .30)
    weights, nav = current_weights(account, {action.symbol: 54.55})
    assert nav == pytest.approx(close_equity)
    assert weights[action.symbol] == pytest.approx(shares * 54.55 / close_equity)
    apply_corporate_actions(account, reviewed.actions, date=action.ex_date, phase="close")
    assert engine.equity(account, pd.Timestamp(action.ex_date)) == pytest.approx(close_equity)
    restored = account_from_dict(account.to_dict(), require_hashes=False)
    assert engine.equity(restored, pd.Timestamp(action.ex_date)) == pytest.approx(close_equity)
    from uquant.broker import sync_broker_snapshot
    with pytest.raises(RuntimeError, match="corporate-action entitlement/tax reconciliation"):
        sync_broker_snapshot(restored, {})


def _sale_tax_account(*, pay_cash=True):
    from uquant.models.corporate_action import DividendTaxDebit

    account, action, panel = _bought_account()
    apply_corporate_actions(account, [action], date=action.record_date, phase="close")
    apply_corporate_actions(account, [action], date=action.ex_date, phase="open")
    account.pending_orders = [_canonical_pending(action.record_date, action.symbol, "SELL", 0, "exit")]
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=pd.Timestamp(action.ex_date), account=account, panel=panel)
    if pay_cash:
        apply_corporate_actions(account, [action], date=action.ex_date, phase="close")
    debit = DividendTaxDebit("fixture-debit", action.action_id, "2023-08-14", "close",
                             account.corporate_actions[0].tax_assessed,
                             "https://example.test/broker-statement", "a" * 64)
    return account, action, debit


@pytest.mark.parametrize(("debit_phase", "date", "phase"), [
    ("close", "2023-08-15", "open"),
    ("open", "2023-08-14", "close"),
])
def test_restart_cannot_skip_known_tax_debit_boundary(debit_phase, date, phase):
    account, action, debit = _sale_tax_account()
    debit = replace(debit, phase=debit_phase)
    before = account.to_dict()
    with pytest.raises(ValueError, match="missing dividend tax debit processing"):
        apply_corporate_actions(account, [action], date=date, phase=phase, tax_debits=[debit])
    assert account.to_dict() == before


@pytest.mark.parametrize("fields", [
    {"date": "2023-8-14"}, {"date": "20230814"}, {"date": "invalid"},
    {"amount": float("nan")}, {"amount": float("inf")}, {"amount": True},
    {"amount": -1}, {"amount": 0}, {"phase": "afternoon"}, {"debit_id": ""},
    {"action_id": "absent"}, {"source_url": "http://example.test/statement"},
    {"source_url": "https://"}, {"source_sha256": ""},
])
def test_invalid_future_tax_debit_is_rejected_before_cash_payment(fields):
    account, action, debit = _sale_tax_account(pay_cash=False)
    before = account.to_dict()
    with pytest.raises(ValueError, match="dividend tax debit"):
        apply_corporate_actions(account, [action], date=action.ex_date, phase="close",
                                tax_debits=[replace(debit, **fields)])
    assert account.to_dict() == before


def test_duplicate_and_excess_tax_debits_are_atomic():
    account, action, debit = _sale_tax_account()
    before = account.to_dict()
    with pytest.raises(ValueError, match="duplicate dividend tax debit input identity"):
        apply_corporate_actions(account, [action], date=debit.date, phase=debit.phase,
                                tax_debits=[debit, debit])
    assert account.to_dict() == before
    with pytest.raises(ValueError, match="outstanding liability"):
        apply_corporate_actions(account, [action], date=debit.date, phase=debit.phase,
                                tax_debits=[debit, replace(debit, debit_id="second")])
    assert account.to_dict() == before


def test_posted_tax_debit_source_is_required_and_immutable_across_restart():
    account, action, debit = _sale_tax_account()
    apply_corporate_actions(account, [action], date=debit.date, phase="open", tax_debits=[debit])
    assert account.dividend_tax_debits == []
    apply_corporate_actions(account, [action], date=debit.date, phase="close", tax_debits=[debit])
    account = account_from_dict(account.to_dict(), require_hashes=False)
    before = account.to_dict()
    for source, message in [([], "omitted durable"),
                            ([replace(debit, amount=debit.amount / 2)], "source changed")]:
        with pytest.raises(ValueError, match=message):
            apply_corporate_actions(account, [action], date="2023-08-15", phase="open", tax_debits=source)
        assert account.to_dict() == before
    apply_corporate_actions(account, [action], date="2023-08-15", phase="open", tax_debits=[debit])
    assert account.cash == before["cash"]
    assert account.dividend_tax_debits == [debit]
