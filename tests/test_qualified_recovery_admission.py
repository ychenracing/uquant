"""Confirmed recovery opens the common CORE gate, not extra risk authority."""
from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_grant_observation import _risk

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.models.strategic_universe import build_strategic_universe_roles
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Risk
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

OWNER = "sz300308"


def _scenario(route):
    dates = pd.bdate_range("2023-01-02", periods=280)
    symbols = (OWNER,) if route == "ordinary" else (OWNER, "sz300502", "sz300394")
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    for frame in panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * .99
        frame["volume"] = 100_000_000.
    leaders = {symbol: _leader(symbol, .96 - i * .01, industry="optical")
               for i, symbol in enumerate(symbols)}
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=symbols,
        qualification_reference_symbols=symbols, risk_reference_symbols=("sh000300", "sh000682"),
        industries=dict.fromkeys(symbols, "optical"),
        available_symbols=(*symbols, "sh000300", "sh000682"),
    )
    account = AccountState.empty(2_000_000.)
    account.account_identity = "account:qualified-recovery"
    account.code_hash, account.data_hash = "source:regression", "data:regression"
    return PortfolioAllocator(DEFAULT_CONFIG), account, dates[240:], panel, leaders, roles


def _decide(policy, account, date, panel, leaders, roles, *, risk=None, opportunity=Opportunity.TREND):
    risk = _risk(frozen=False) if risk is None else risk
    risk = replace(risk, evidence={**risk.evidence, "broad_ret120": .04,
        "ai_fast_return": .16, "declining_ratio": .05, "below_ma20_ratio": .05,
        "tech_speed": .16, "broad_speed": .02})
    previous = list(account.pending_orders)
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    targets = policy.allocate(
        date=date, opportunity=opportunity, risk=risk, user_panel=panel, leaders=leaders,
        account=account, prices=prices, qualification_panel=panel, qualification_leaders=leaders,
        strategic_universe=roles,
    )
    attributed = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=str(date.date()),
        targets=targets, retained_orders=previous,
    )
    planned = plan_orders(signal_date=str(date.date()), targets=attributed,
                          account=account, prices=prices, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date()),
    ))
    return targets


@pytest.mark.parametrize("route", ("ordinary", "strategic"))
def test_current_trend_ordinary_or_recovery_strategic_produces_real_order_and_fill(route):
    policy, account, dates, panel, leaders, roles = _scenario(route)
    for _index, date in enumerate(dates[:7]):
        _decide(policy, account, date, panel, leaders, roles,
                opportunity=Opportunity.TREND if route == "ordinary" else Opportunity.RECOVERY)
        if account.pending_orders:
            break
    else:
        pytest.fail("confirmed RECOVERY qualification never reached native BUY planning")
    orders = list(account.pending_orders)
    assert all(order.side == "BUY" and order.lifecycle == "CORE" for order in orders)
    if route == "ordinary":
        assert len(orders) == 1 and orders[0].mechanism == "LEADER_SELECTION"
        assert orders[0].target_weight == pytest.approx(DEFAULT_CONFIG.single_core_entry_cap)
        assert orders[0].grant_id == orders[0].epoch_id == ""
        assert account.strategic_grant is None
        assert account.replacement_tenure[f"strategic_eligibility:independent_core:{OWNER}"] >= 5
    else:
        assert len(orders) == 3 and all(order.mechanism == "STRATEGIC_COHORT" for order in orders)
        assert account.strategic_grant.qualification_quorum == "FULL_COHORT"
    account = account_from_dict(asdict(account))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[_index + 1], account=account, panel=panel)
    assert len(fills) == len(orders) and all(fill.shares > 0 for fill in fills)
    assert {fill.order_id for fill in fills} == {order.order_id for order in orders}


@pytest.mark.parametrize("route", ("ordinary", "strategic"))
@pytest.mark.parametrize("block", ("caution", "risk_off", "freeze", "sentinel", "qualification", "choppy", "weak"))
def test_recovery_does_not_bypass_current_risk_qualification_cash_or_closed_opportunity(route, block):
    policy, account, dates, panel, leaders, roles = _scenario(route)
    risk = _risk(frozen=False)
    opportunity = Opportunity.TREND if route == "ordinary" else Opportunity.RECOVERY
    if block in {"caution", "risk_off"}:
        risk = replace(risk, state=Risk.CAUTION if block == "caution" else Risk.RISK_OFF)
    elif block in {"freeze", "sentinel"}:
        risk = replace(risk, freeze_new_risk=True, evidence={
            **risk.evidence, "freeze_new_risk": True,
            "sentinel_freeze_new_risk": block == "sentinel", "base_freeze_new_risk": block != "sentinel",
        })
    elif block == "qualification":
        leaders = {symbol: replace(leader, score=.1, mature=False, emerging=False,
                                   components={**leader.components, "secular_score": .1,
                                               "secular_confidence": .1, "breakout_quality": 0.})
                   for symbol, leader in leaders.items()}
        for frame in panel.values():
            frame["close"] = frame["close"].iloc[::-1].to_numpy()
            frame["ma20"], frame["ma60"] = frame["close"] * 1.1, frame["close"] * 1.2
            frame["ret20"], frame["ret60"], frame["ret120"] = -.1, -.2, -.3
    else:
        opportunity = Opportunity.CHOPPY if block == "choppy" else Opportunity.WEAK
    for date in dates[:7]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk, opportunity=opportunity)
        assert not any(order.side == "BUY" for order in account.pending_orders)
    assert not account.fills and not account.positions


def test_frozen_recovery_does_not_accumulate_trend_only_flat_repair():
    policy, account, dates, panel, leaders, roles = _scenario("ordinary")
    account.capital_budget_level = 1
    risk = replace(_risk(frozen=False), freeze_new_risk=True, evidence={
        **_risk(frozen=False).evidence, "freeze_new_risk": True, "reference_coverage": 1.,
        "transition_damage": .1,
    })
    for date in dates[:22]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
        assert not account.pending_orders
    assert account.flat_book_capital_repair.healthy_session_count == 0
    assert account.strategic_cash_rearm.status not in {"AUTHORIZED", "CONSUMED"}
    assert account.capital_budget_level == 1


def test_trend_partial_buy_still_loses_capital_when_current_qualification_fails():
    policy, account, dates, panel, leaders, roles = _scenario("ordinary")
    for _index, date in enumerate(dates[:7]):
        _decide(policy, account, date, panel, leaders, roles)
        if account.pending_orders:
            break
    assert len(account.pending_orders) == 1
    fill_day = dates[_index + 1]
    panel[OWNER].loc[fill_day, "volume"] = 100_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=.002)).execute_open(
        date=fill_day, account=account, panel=panel,
    )
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].status == "PARTIALLY_FILLED"
    shares, cash = account.positions[OWNER].shares, account.cash
    leaders[OWNER] = replace(leaders[OWNER], score=.1)
    _decide(policy, account, fill_day, panel, leaders, roles)
    assert not account.pending_orders
    assert account.order_ledger[0].status == "CANCELLED"
    account = account_from_dict(asdict(account))
    _decide(policy, account, dates[_index + 2], panel, leaders, roles)
    assert not account.pending_orders
    assert account.positions[OWNER].shares == shares and account.cash == cash


@pytest.mark.parametrize("cash_available", (True, False), ids=("spendable-cash", "no-spendable-cash"))
def test_trend_new_independent_core_beside_real_holding_uses_only_available_cash(cash_available):
    policy, account, dates, panel, leaders, roles = _scenario("ordinary")
    for _index, date in enumerate(dates[:7]):
        _decide(policy, account, date, panel, leaders, roles, opportunity=Opportunity.TREND)
        if account.pending_orders:
            break
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[_index + 1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].shares > 0
    held_shares = account.positions[OWNER].shares
    challenger = "sh688146"
    panel[challenger] = panel[OWNER].copy()
    leaders[challenger] = _leader(challenger, .95, industry="materials")
    roles = build_strategic_universe_roles(
        as_of=str(dates[-1].date()), tradable_symbols=(OWNER, challenger),
        qualification_reference_symbols=(OWNER, challenger),
        risk_reference_symbols=("sh000300", "sh000682"),
        industries={OWNER: "optical", challenger: "materials"},
        available_symbols=(OWNER, challenger, "sh000300", "sh000682"),
    )
    if not cash_available:
        # Explicit current account funding constraint, while its positive equity
        # is backed by the preceding native holding (not a fabricated position).
        account.cash = 0.
    new_dates = dates[_index + 2:]
    for _step, date in enumerate(new_dates[:7]):
        targets = _decide(policy, account, date, panel, leaders, roles)
        buys = [order for order in account.pending_orders if order.side == "BUY"]
        if buys:
            break
    assert account.positions[OWNER].shares == held_shares
    assert account.strategic_grant is None
    if cash_available:
        assert len(buys) == 1 and buys[0].symbol == challenger
        assert buys[0].mechanism == "LEADER_SELECTION"
        held_weight = held_shares * float(panel[OWNER].loc[date, "close"]) / (account.cash + held_shares * float(panel[OWNER].loc[date, "close"]))
        assert buys[0].target_weight == pytest.approx(DEFAULT_CONFIG.industry_weight_cap - held_weight)
        assert sum(target.weight for target in targets) <= DEFAULT_CONFIG.max_gross
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=new_dates[_step + 1], account=account, panel=panel)
        assert len(fills) == 1 and fills[0].symbol == challenger and fills[0].shares > 0
        assert account.cash >= 0 and account.positions[OWNER].shares == held_shares
    else:
        assert not buys
        assert challenger not in account.positions
        assert len(account.fills) == 1


def test_ordinary_recovery_is_closed_even_with_current_stock_and_common_numeric_proof():
    policy, account, dates, panel, leaders, roles = _scenario("ordinary")
    for date in dates[:7]:
        _decide(policy, account, date, panel, leaders, roles, opportunity=Opportunity.RECOVERY)
        assert not account.pending_orders
    assert not account.fills and not account.positions and account.strategic_grant is None
