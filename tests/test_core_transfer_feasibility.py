"""A rotation must make the confirmed challenger fundable after settlement."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pandas as pd
import pytest
from test_unified_core_book import _inputs

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Position, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

WEAK = "sh688233"
INCUMBENT = "sh688012"
CHALLENGER = "sh601869"
SECOND_INCUMBENT = "sh688200"


def _scenario(monkeypatch, obstruction):
    _, panel, leaders, risk = _inputs()
    names = dict(zip(panel, (WEAK, INCUMBENT, CHALLENGER), strict=True))
    panel = {names[symbol]: frame for symbol, frame in panel.items()}
    leaders = {names[symbol]: replace(score, symbol=names[symbol]) for symbol, score in leaders.items()}
    dates = panel[WEAK].index
    date = dates[-3]
    panel[WEAK].loc[date:, ["ma20", "ret20"]] = [20.0, -0.10]
    leaders[WEAK] = replace(leaders[WEAK], score=0.2, mature=False)
    cash, weak_shares, incumbent_shares = {
        "cash_shortfall": (55_800.0, 5_350, 89_070),
        "industry_limit": (50_000.0, 30_000, 65_000),
        "correlation_limit": (50_000.0, 30_000, 65_000),
        "missing_correlation": (50_000.0, 30_000, 65_000),
        "below_trade_minimum": (170_000.0, 4_000, 79_000),
        "held_too_briefly": (100_000.0, 40_000, 50_000),
        "ready": (100_000.0, 40_000, 50_000),
    }[obstruction]
    account = AccountState.empty(1_000_000.0)
    account.cash = cash
    account.positions = {
        symbol: Position(symbol, shares, 10.0, str(dates[-12].date()), 10.0)
        for symbol, shares in ((WEAK, weak_shares), (INCUMBENT, incumbent_shares))
    }
    stable_symbols = [INCUMBENT]
    if incumbent_shares > 60_000:
        first_shares = incumbent_shares // 2
        account.positions[INCUMBENT].shares = first_shares
        account.positions[SECOND_INCUMBENT] = Position(
            SECOND_INCUMBENT, incumbent_shares - first_shares, 10.0, str(dates[-12].date()), 10.0,
        )
        panel[SECOND_INCUMBENT] = panel[INCUMBENT].copy()
        leaders[SECOND_INCUMBENT] = replace(leaders[INCUMBENT], symbol=SECOND_INCUMBENT)
        stable_symbols.append(SECOND_INCUMBENT)
    weak_weight = weak_shares / 100_000.0
    account.protected_weights = {WEAK: weak_weight}
    account.strategic_restore_weights = {WEAK: weak_weight}
    account.strategic_cohort_targets = {WEAK: weak_weight}
    account.strategic_exit_bands = {WEAK: [weak_weight / 2, weak_weight / 2]}
    account.replacement_tenure.update({
        f"strategic_eligibility:established:{CHALLENGER}": 5,
        # Discovery is isolated here; the challenger has observed both predicates.
        f"strategic_eligibility:independent_core:{CHALLENGER}": 5,
        f"core_transfer:{WEAK}->{CHALLENGER}": 2,
    })
    account.candidate_tenure.update({
        "strategic_eligibility_session": date.toordinal(),
        f"core_transfer_session:{WEAK}->{CHALLENGER}": dates[-4].toordinal(),
    })
    if obstruction == "industry_limit":
        for symbol in stable_symbols:
            leaders[symbol] = replace(leaders[symbol], industry=leaders[CHALLENGER].industry)
    elif obstruction == "correlation_limit":
        for symbol in stable_symbols:
            panel[symbol] = panel[CHALLENGER].copy()
    elif obstruction == "missing_correlation":
        panel[INCUMBENT] = panel[INCUMBENT].tail(12)
    elif obstruction == "held_too_briefly":
        account.positions[WEAK].entry_date = str(dates[-11].date())
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    # The ordinary allocator consumes already observed absolute route evidence.
    monkeypatch.setattr(policy, "_strategic_cohort_targets", lambda **kwargs: None)
    arguments = dict(
        date=date, opportunity=Opportunity.TREND, risk=risk, user_panel=panel,
        leaders=leaders, account=account, prices=dict.fromkeys(panel, 10.0),
    )
    return policy, arguments


@pytest.mark.parametrize("obstruction", (
    "cash_shortfall", "industry_limit", "correlation_limit", "missing_correlation",
    "below_trade_minimum", "held_too_briefly",
))
def test_unfundable_or_unexecutable_transfer_preserves_incumbent_rights_and_rotation_quota(
    monkeypatch, obstruction,
):
    policy, arguments = _scenario(monkeypatch, obstruction)
    account = arguments["account"]
    before = deepcopy(account)

    targets = policy.allocate(**arguments)

    expected = {symbol: position.shares / 100_000.0 for symbol, position in before.positions.items()}
    assert {target.symbol: target.weight for target in targets} == pytest.approx(expected)
    assert account.rotation_dates == before.rotation_dates == []
    assert account.protected_weights == before.protected_weights
    assert account.strategic_restore_weights == before.strategic_restore_weights
    assert account.strategic_cohort_targets == before.strategic_cohort_targets
    assert account.strategic_exit_bands == before.strategic_exit_bands
    assert account.cash == before.cash and account.positions == before.positions
    assert plan_orders(
        signal_date=str(arguments["date"].date()), targets=targets, account=account,
        prices=arguments["prices"], cfg=DEFAULT_CONFIG,
    ) == ()


def test_feasible_transfer_funds_challenger_only_after_actual_sell_settlement(monkeypatch):
    policy, arguments = _scenario(monkeypatch, "ready")
    dates = arguments["user_panel"][WEAK].index
    account = AccountState.empty(1_000_000.0)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    arguments["account"] = account
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    execution_panel = {
        symbol: pd.DataFrame(
            {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
             "volume": 100_000_000.0, "amount": 1_000_000_000.0}, index=dates[-13:],
        )
        for symbol in arguments["user_panel"]
    }

    def submit(day, targets):
        signal = str(day.date())
        targets = attach_target_attribution(
            "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
            targets=targets, retained_orders=account.pending_orders,
        )
        orders = plan_orders(signal_date=signal, targets=targets, account=account,
                             prices=arguments["prices"], cfg=DEFAULT_CONFIG)
        account.pending_orders = list(reconcile_account_orders(
            account=account, previous=account.pending_orders, current=orders, submitted_date=signal,
        ))
        return orders

    submit(dates[-13], tuple(
        Target(symbol, weight, "CORE", 0.9, 0.9, "prior core entry",
               origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE")
        for symbol, weight in ((WEAK, 0.4), (INCUMBENT, 0.5))
    ))
    entry_fills = planner.execute_open(date=dates[-12], account=account, panel=execution_panel)
    assert {fill.symbol: fill.shares for fill in entry_fills} == {WEAK: 39_900, INCUMBENT: 49_900}
    assert not account.pending_orders
    account.protected_weights = {WEAK: 0.4}
    account.replacement_tenure.update({
        f"strategic_eligibility:established:{CHALLENGER}": 5,
        f"strategic_eligibility:independent_core:{CHALLENGER}": 5,
        f"core_transfer:{WEAK}->{CHALLENGER}": 2,
    })
    account.candidate_tenure.update({
        "strategic_eligibility_session": dates[-3].toordinal(),
        f"core_transfer_session:{WEAK}->{CHALLENGER}": dates[-4].toordinal(),
    })
    cash_before_sale = account.cash
    equity = account.cash + 898_000.0
    weak_before_sale = 399_000.0 / equity

    targets = policy.allocate(**arguments)

    observed = {target.symbol: target.weight for target in targets}
    assert observed[WEAK] == pytest.approx(weak_before_sale - 0.3)
    assert CHALLENGER not in observed
    assert account.protected_weights[WEAK] == pytest.approx(weak_before_sale - 0.3)
    assert account.rotation_dates == [str(dates[-3].date())]
    assert account.cash == cash_before_sale and account.positions[WEAK].shares == 39_900
    orders = submit(dates[-3], targets)
    assert [(order.symbol, order.side) for order in orders] == [(WEAK, "SELL")]
    awaiting_settlement = policy.allocate(**arguments)
    assert CHALLENGER not in {target.symbol for target in awaiting_settlement}
    assert account.cash == cash_before_sale
    assert account.positions[WEAK].shares == 39_900
    assert len(account.rotation_dates) == 1

    sold = planner.execute_open(date=dates[-2], account=account, panel=execution_panel)
    assert len(sold) == 1 and sold[0].symbol == WEAK and sold[0].side == "SELL"
    assert sold[0].shares == 30_000
    assert account.positions[WEAK].shares == 9_900
    assert not account.pending_orders and account.cash > cash_before_sale
    arguments["date"] = dates[-2]
    admitted = policy.allocate(**arguments)
    challenger = next(target for target in admitted if target.symbol == CHALLENGER)
    assert challenger.weight == pytest.approx(0.2)
    assert challenger.mechanism == "LEADER_ROTATION" and challenger.replaces_symbol == WEAK
    orders = submit(dates[-2], admitted)
    assert [(order.symbol, order.side) for order in orders] == [(CHALLENGER, "BUY")]
    bought = planner.execute_open(date=dates[-1], account=account, panel=execution_panel)
    assert len(bought) == 1 and bought[0].symbol == CHALLENGER and bought[0].side == "BUY"
    assert bought[0].shares > 0 and account.positions[CHALLENGER].shares == bought[0].shares
    assert len(account.rotation_dates) == 1


@pytest.mark.parametrize("another_weak_holding", (False, True))
def test_existing_lifecycle_exit_is_not_relabelled_or_spent_as_a_new_rotation(monkeypatch, another_weak_holding):
    policy, arguments = _scenario(monkeypatch, "ready")
    account = arguments["account"]
    account.strategic_cohort_targets.clear()
    account.strategic_restore_weights.clear()
    account.strategic_exit_bands.clear()
    if another_weak_holding:
        arguments["leaders"][INCUMBENT] = replace(arguments["leaders"][INCUMBENT], score=0.3, mature=False)
        arguments["user_panel"][INCUMBENT].loc[arguments["date"]:, ["ma20", "ret20"]] = [20.0, -0.05]
        account.replacement_tenure[f"core_transfer:{INCUMBENT}->{CHALLENGER}"] = 2
        account.candidate_tenure[f"core_transfer_session:{INCUMBENT}->{CHALLENGER}"] = (
            arguments["user_panel"][INCUMBENT].index[-4].toordinal()
        )
    monkeypatch.setattr(policy, "_leader_lifecycle_exit_confirmed", lambda **kwargs: kwargs["symbol"] == WEAK)

    targets = policy.allocate(**arguments)

    reduction = next(target for target in targets if target.symbol == WEAK)
    assert reduction.weight == 0.0
    assert reduction.mechanism == "LEADER_LIFECYCLE_EXIT"
    assert next(target.weight for target in targets if target.symbol == INCUMBENT) == pytest.approx(0.5)
    assert not account.rotation_dates
    assert CHALLENGER not in {target.symbol for target in targets}
    assert account.cash == 100_000.0
