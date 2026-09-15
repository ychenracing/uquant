"""Existing Base Risk repair permissions survive the unified allocator."""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from test_unified_core_book import _inputs

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment, Target
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256

SYMBOL = "sh688008"


def _submit(account, day, targets):
    previous = list(account.pending_orders)
    signal = str(day.date())
    attributed = attach_target_attribution(
        "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
        targets=targets, retained_orders=previous,
    )
    planned = plan_orders(signal_date=signal, targets=attributed, account=account,
                          prices={symbol: 10.0 for symbol in set(account.positions) | {t.symbol for t in attributed}},
                          cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=signal,
    ))
    return merged


def _allocate(fixture, day, *, account=None, risk=None):
    policy, state, _, panel, leaders, assessment = fixture
    return policy.allocate(
        date=day, opportunity=Opportunity.RECOVERY, risk=risk or assessment,
        user_panel=panel, leaders=leaders, account=state if account is None else account,
        prices={SYMBOL: 10.0},
    )


def _restorable():
    _, source_panel, source_leaders, _ = _inputs()
    panel = {SYMBOL: source_panel["sh600001"].copy()}
    dates = panel[SYMBOL].index[-12:]
    for column, value in (("open", 10.0), ("close", 10.0), ("high", 10.1), ("low", 9.9),
                          ("ma20", 9.5), ("ma60", 9.0), ("volume", 100_000_000.0)):
        panel[SYMBOL][column] = value
    leaders = {SYMBOL: replace(source_leaders["sh600001"], symbol=SYMBOL, mature=False)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    _submit(account, dates[0], (Target(
        SYMBOL, .6, "CORE", .9, .9, "prior ordinary core entry",
        origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
    ),))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
    saved_weight = fills[0].shares * 10 / (account.cash + fills[0].shares * 10)
    account.last_shock_date = str(dates[2].date())
    account.protected_weights[SYMBOL] = saved_weight
    hard = RiskAssessment(Risk.RISK_OFF, .2, 3, {}, ("fixture risk compression",), "SHOCK",
                          freeze_new_risk=True, reduction_level=2)
    targets = policy.allocate(date=dates[2], opportunity=Opportunity.RECOVERY, risk=hard,
                              user_panel=panel, leaders=leaders, account=account, prices={SYMBOL: 10.0})
    _submit(account, dates[2], targets)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    assert account.positions[SYMBOL].shares > 0
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 1
    risk = RiskAssessment(Risk.CAUTION, .5, 0, {"transition_damage": .1}, (), "RECOVERY",
                          freeze_new_risk=True, reduction_level=1)
    return policy, account, dates[4:], panel, leaders, risk


@pytest.mark.parametrize("permission", ("protected-level1", "level1-two-days", "chronic-two-days", "synchronized"))
def test_existing_bounded_repair_restores_continuous_core_within_risk_cap(permission):
    fixture = _restorable()
    _, account, dates, panel, _, risk = fixture
    if permission == "level1-two-days":
        account.capital_budget_repair_streak = 2
        risk = replace(risk, shock_state="NONE")
    elif permission == "chronic-two-days":
        account.capital_budget_level = 0
        account.capital_budget_repair_streak = 0
        account.chronic_level = 1
        account.chronic_repair_streak = 2
        risk = replace(risk, shock_state="NONE")
    elif permission == "synchronized":
        panel[SYMBOL]["ret5"] = .01
        account.capital_budget_repair_streak = 0
        account.risk_streaks["concentrated_repair"] = DEFAULT_CONFIG.concentrated_repair_days
        risk = replace(risk, evidence={**risk.evidence, "held_repair_ratio": 1.,
                                     "held_damage_ratio": 0.},
                       reasons=("two-day synchronized leader repair",))
    targets = _allocate(fixture, dates[0], risk=risk)
    target = next(target for target in targets if target.symbol == SYMBOL)
    assert target.weight == pytest.approx(risk.target_gross_cap)
    assert target.mechanism == "POST_SHOCK_RESTORATION"
    assert target.origin_subsystem == "RECOVERY"
    orders = _submit(account, dates[0], targets)
    assert len(orders) == 1 and orders[0].side == "BUY"
    restored = account_from_dict(asdict(account))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=restored, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
    targets = _allocate(fixture, dates[2], account=restored, risk=risk)
    assert not _submit(restored, dates[2], targets)


@pytest.mark.parametrize("missing", (
    "risk-shock", "shock-date", "repair-streak", "capital-level", "chronic-streak",
    "high-votes", "transition-repair", "hard-reduction", "sentinel-freeze",
    "structure", "stale-shock-before-entry", "rejected-ordinary-pending",
))
def test_bounded_repair_does_not_override_missing_authority(missing):
    fixture = _restorable()
    _, account, dates, panel, _, risk = fixture
    if missing == "risk-shock":
        risk = replace(risk, shock_state="NONE")
    elif missing == "shock-date":
        account.last_shock_date = ""
    elif missing == "repair-streak":
        account.capital_budget_repair_streak = 0
    elif missing == "capital-level":
        account.capital_budget_level = 2
    elif missing == "chronic-streak":
        account.capital_budget_level = 0
        account.capital_budget_repair_streak = 0
        account.chronic_level = 1
        account.chronic_repair_streak = 1
        risk = replace(risk, shock_state="NONE")
    elif missing == "high-votes":
        risk = replace(risk, votes=2)
    elif missing == "transition-repair":
        risk = replace(risk, evidence={"transition_damage": DEFAULT_CONFIG.transition_damage_repair + .01})
    elif missing == "hard-reduction":
        risk = replace(risk, reduction_level=2)
    elif missing == "sentinel-freeze":
        risk = replace(risk, evidence={**risk.evidence, "sentinel_freeze_new_risk": True,
                                      "base_freeze_new_risk": True})
    elif missing == "structure":
        panel[SYMBOL].loc[dates[0]:, ["ma20", "ma60"]] = 12.0
        panel[SYMBOL].loc[dates[0]:, "ret20"] = -.1
    elif missing == "stale-shock-before-entry":
        account.last_shock_date = str(panel[SYMBOL].index[0].date())
    else:
        _submit(account, dates[0], (Target(
            SYMBOL, .6, "CORE", .9, .9, "ordinary buy with no current qualification",
            origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
        ),))
        assert account.pending_orders[0].side == "BUY"
    before = account.positions[SYMBOL].shares
    targets = _allocate(fixture, dates[1], risk=risk)
    orders = _submit(account, dates[1], targets)
    assert not any(order.side == "BUY" for order in orders)
    assert account.positions[SYMBOL].shares == before
    restored = account_from_dict(asdict(account))
    orders = _submit(restored, dates[2], _allocate(fixture, dates[2], account=restored, risk=risk))
    assert not any(order.side == "BUY" for order in orders)


@pytest.mark.parametrize("reentered", (False, True), ids=("flat", "reentered-after-shock"))
def test_flat_or_reentered_holding_cannot_reuse_bounded_restore(reentered):
    fixture = _restorable()
    _, account, dates, panel, _, risk = fixture
    saved = dict(account.protected_weights)
    _submit(account, dates[0], (Target(
        SYMBOL, 0.0, "CORE", .9, .9, "independent full exit",
        origin_subsystem="LEADER", mechanism="LEADER_LIFECYCLE_EXIT", origin_lifecycle="CORE",
    ),))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert SYMBOL not in account.positions
    if reentered:
        _submit(account, dates[2], (Target(
            SYMBOL, .2, "CORE", .9, .9, "later independent entry",
            origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
        ),))
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
        assert len(fills) == 1 and fills[0].side == "BUY"
    account.protected_weights = saved
    targets = _allocate(fixture, dates[4], risk=risk)
    orders = _submit(account, dates[4], targets)
    assert not any(order.side == "BUY" for order in orders)


def test_sentinel_planning_copy_cannot_revive_rejected_buy_after_restart():
    fixture = _restorable()
    _, account, dates, _, _, base_repair = fixture
    _submit(account, dates[0], (Target(
        SYMBOL, .6, "CORE", .9, .9, "ordinary buy without current qualification",
        origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
    ),))
    rejected_id = account.pending_orders[0].order_id
    sentinel = replace(base_repair, state=Risk.NORMAL, shock_state="NONE",
                       evidence={"transition_damage": .1, "sentinel_freeze_new_risk": True,
                                 "base_freeze_new_risk": False})
    targets = _allocate(fixture, dates[1], risk=sentinel)
    assert not any(order.side == "BUY" for order in _submit(account, dates[1], targets))
    rejected = next(order for order in account.order_ledger if order.order_id == rejected_id)
    assert rejected.status == "CANCELLED" and rejected.filled_shares == 0
    restored = account_from_dict(asdict(account))
    targets = _allocate(fixture, dates[2], account=restored, risk=base_repair)
    assert not any(order.side == "BUY" for order in _submit(restored, dates[2], targets))


@pytest.mark.parametrize("homogeneous", (False, True), ids=("mixed-without-breadth", "homogeneous"))
def test_locked_history_neither_vetoes_continuous_holding_nor_rebuys_flat_member(homogeneous):
    fixture = _restorable()
    _, account, dates, panel, leaders, risk = fixture
    secondary = "sh688012"
    panel[secondary] = panel[SYMBOL].copy()
    leaders[secondary] = replace(leaders[SYMBOL], symbol=secondary,
                                 industry=leaders[SYMBOL].industry if homogeneous else "equipment")

    def targets(secondary_weight):
        equity = account.cash + sum(position.shares * 10 for position in account.positions.values())
        retained = account.positions[SYMBOL].shares * 10 / equity
        return tuple(Target(
            symbol, weight, "CORE", .9, .9, "earlier recovery member transaction",
            origin_subsystem="LEADER", origin_lifecycle="CORE",
            mechanism="LEADER_SELECTION" if weight else "LEADER_LIFECYCLE_EXIT",
        ) for symbol, weight in ((SYMBOL, retained), (secondary, secondary_weight)))

    # The legacy anchor set includes a genuinely earlier member that subsequently
    # exited; its bookkeeping is not a fabricated extra live holding.
    _submit(account, dates[0], targets(.1))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[1], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == secondary and fills[0].side == "BUY"
    _submit(account, dates[2], targets(0.0))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[3], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == secondary and fills[0].side == "SELL"
    assert secondary not in account.positions
    account.anchor_weights = {SYMBOL: .6, secondary: .1}
    account.candidate_tenure["recovery_cohort_locked"] = 1
    account.protected_weights[secondary] = .1
    saved_rights = dict(account.protected_weights)
    # Unified restoration uses actual holding continuity and shared risk/cash/
    # concentration authority. A former cohort's industry count grants neither
    # a veto over its live survivor nor permission to rebuy the flat member.
    account.capital_budget_repair_streak = 2
    risk = replace(risk, reasons=("two-day synchronized leader repair",))
    result = _allocate(fixture, dates[4], risk=risk)
    orders = _submit(account, dates[4], result)
    assert len(orders) == 1 and orders[0].symbol == SYMBOL and orders[0].side == "BUY"
    assert orders[0].target_weight == pytest.approx(risk.target_gross_cap)
    assert account.protected_weights == saved_rights
    restored = account_from_dict(asdict(account))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=restored, panel=panel)
    assert len(fills) == 1 and fills[0].symbol == SYMBOL and fills[0].side == "BUY"
    assert secondary not in restored.positions
    unfrozen = replace(risk, state=Risk.NORMAL, shock_state="NONE", freeze_new_risk=False, reasons=())
    result = _allocate(fixture, dates[6], account=restored, risk=unfrozen)
    assert all(order.symbol == SYMBOL for order in _submit(restored, dates[6], result))
    assert restored.protected_weights == saved_rights
