"""Ordinary mature capital has no concentrated strategic authority."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_universe_quorum import _risk

from uquant.account import load_account, save_account
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.leader import apply_leader_tenure
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.qualification_candidates import candidate_entry
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _ordinary_inputs():
    dates = pd.bdate_range("2023-01-02", periods=260)
    symbol = "sz300308"
    panel = {symbol: _strategic_frame(dates)}
    base = {symbol: _leader(symbol, .80)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity, account.code_hash = "account:ordinary-fixture", "code:ordinary-fixture"
    account.data_hash = sha256(panel[symbol].to_csv().encode()).hexdigest()
    risk = replace(_risk(), target_gross_cap=1.0)
    # Current market strength is outside the concentrated independent-clock range.
    risk.evidence["tech_ret120"] = .40
    execution = {symbol: pd.DataFrame({
        "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.0,
        "volume": 100_000_000.0, "amount": 300_000_000.0,
    }, index=dates)}
    return dates, symbol, panel, base, account, risk, execution


def _decide(policy, *, date, symbol, panel, leaders, account, risk):
    signal = str(date.date())
    targets = policy.allocate(date=date, opportunity=Opportunity.TREND, risk=risk,
        user_panel=panel, leaders=leaders, account=account, prices={symbol: 3.0})
    attributed = attach_target_attribution("optical", REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=signal, targets=targets, retained_orders=account.pending_orders)
    orders = plan_orders(signal_date=signal, targets=attributed, account=account,
        prices={symbol: 3.0}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=account.pending_orders, current=orders, submitted_date=signal))
    return targets, orders


@pytest.mark.parametrize("condition", ["tenure_incomplete", "confirmed", "frozen"])
def test_native_maturity_history_controls_only_ordinary_buy(condition):
    dates, symbol, panel, base, account, risk, execution = _ordinary_inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    days = DEFAULT_CONFIG.leader_tenure_days - (condition == "tenure_incomplete")
    if condition == "frozen":
        risk = replace(risk, freeze_new_risk=True)
    for index, date in enumerate(dates[-8:-8 + days], 1):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        assert leaders[symbol].mature == (index >= DEFAULT_CONFIG.leader_tenure_days)
        targets, orders = _decide(policy, date=date, symbol=symbol, panel=panel,
                                  leaders=leaders, account=account, risk=risk)
        if index < DEFAULT_CONFIG.leader_tenure_days:
            assert not orders and not targets
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{symbol}", 0) == 0
    assert account.strategic_grant is None and not account.strategic_epochs
    assert not account.strategic_cash_rearm.authorized
    if condition != "confirmed":
        assert not orders
        return
    assert len(orders) == 1 and orders[0].side == "BUY"
    strict = candidate_entry(policy, symbol=symbol, score=leaders[symbol], date=date,
        user_panel=panel, account=account, confirmation_days=DEFAULT_CONFIG.leader_tenure_days)
    assert strict["block"] == "CONFIRMATION_INCOMPLETE"
    assert strict["confirmations"]["independent_core"] == 0
    assert orders[0].target_weight == pytest.approx(DEFAULT_CONFIG.core_admission_weight)
    assert not orders[0].grant_id and not orders[0].epoch_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-3], account=account, panel=execution)
    assert len(fills) == 1 and fills[0].shares > 0 and fills[0].side == "BUY"
    assert not fills[0].grant_id and not fills[0].epoch_id


def test_native_partial_buy_loses_current_qualification_and_stays_cancelled_after_restart(tmp_path):
    dates, symbol, panel, base, account, risk, execution = _ordinary_inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    for date in dates[-9:-4]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        _, orders = _decide(policy, date=date, symbol=symbol, panel=panel,
                            leaders=leaders, account=account, risk=risk)
    assert len(orders) == 1 and orders[0].side == "BUY"
    order_id = orders[0].order_id
    execution[symbol].loc[dates[-4], ["volume", "amount"]] = [1_000_000.0, 3_000_000.0]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-4], account=account, panel=execution)
    assert len(fills) == 1 and fills[0].shares > 0
    assert any(o.order_id == order_id for o in account.pending_orders)
    held_shares, cash = account.positions[symbol].shares, account.cash
    # Real current alpha failure also prevents any shared strategic certificate.
    invalid = {symbol: replace(base[symbol], score=.40, mature=False, emerging=False,
                              components={**base[symbol].components, "secular_score": .40})}
    leaders = apply_leader_tenure(invalid, account=account, cfg=DEFAULT_CONFIG)
    _, orders = _decide(policy, date=dates[-3], symbol=symbol, panel=panel,
                        leaders=leaders, account=account, risk=risk)
    assert not any(o.side == "BUY" for o in orders)
    assert not any(o.side == "BUY" for o in account.pending_orders)
    assert risk.evidence["core_allocation"]["symbols"][symbol]["pending_buy_rejected"]
    save_account(account, tmp_path / "cancelled.json")
    restored = load_account(tmp_path / "cancelled.json")
    leaders = apply_leader_tenure(invalid, account=restored, cfg=DEFAULT_CONFIG)
    _, orders = _decide(PortfolioAllocator(DEFAULT_CONFIG), date=dates[-2], symbol=symbol,
                        panel=panel, leaders=leaders, account=restored, risk=risk)
    assert not any(o.side == "BUY" for o in orders)
    after = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=restored, panel=execution)
    assert not any(f.side == "BUY" for f in after)
    assert restored.positions[symbol].shares == held_shares and restored.cash == cash
    assert restored.strategic_grant is None and not restored.strategic_epochs
    assert not restored.strategic_cash_rearm.authorized


def test_native_repair_partial_cannot_use_mature_fallback_after_strict_clock_loss(tmp_path):
    from test_ordinary_cash_rearm import SYMBOL, _allocate, _roles, _scenario
    from test_ordinary_cash_rearm import _decide as repair_decide

    from uquant.portfolio.strategic import rearm

    policy, account, dates, panel, base, risk = _scenario(sessions=0)
    # Observe actual healthy repair sessions and native leader persistence together.
    for date in dates[:19]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        rearm.observe_flat_book_capital_repair_state(
            account=account, risk=risk, universe=_roles(date),
            observed_session=str(date.date()), cfg=DEFAULT_CONFIG)
        assert not _allocate(policy, account, date, panel, leaders, risk)
    leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    repair_decide(policy, account, dates[19], panel, leaders, risk)
    original = account.pending_orders[0]
    reference = account.strategic_cash_rearm.consumed_order
    assert reference is not None
    assert (reference.order_id, reference.event_id) == (original.order_id, original.event_id)
    panel[SYMBOL].loc[dates[20], "volume"] = 100_000.0
    panel[SYMBOL].loc[dates[20], "amount"] = 100_000.0 * panel[SYMBOL].loc[dates[20], "close"]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[20], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].status == "PARTIALLY_FILLED"
    shares, cash = account.positions[SYMBOL].shares, account.cash
    weakened = replace(risk, evidence={**risk.evidence, "broad_ret20": -.20})
    leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    assert leaders[SYMBOL].mature and account.leader_tenure[SYMBOL] >= DEFAULT_CONFIG.leader_tenure_days
    repair_decide(policy, account, dates[20], panel, leaders, weakened)
    assert account.replacement_tenure[f"strategic_eligibility:independent_core:{SYMBOL}"] == 0
    assert not account.pending_orders
    assert account.order_ledger[0].status == "CANCELLED"
    save_account(account, tmp_path / "repair-cancelled.json")
    restored = load_account(tmp_path / "repair-cancelled.json")
    repair_decide(PortfolioAllocator(DEFAULT_CONFIG), restored, dates[21], panel, leaders, weakened)
    assert not restored.pending_orders
    assert restored.positions[SYMBOL].shares == shares and restored.cash == cash
    assert restored.strategic_grant is None and not restored.strategic_epochs
