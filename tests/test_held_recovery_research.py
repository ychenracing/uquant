"""A new held-book permission must survive real fills without releasing new risk."""
from dataclasses import asdict, replace

import pytest
from test_core_bounded_risk_restoration import SYMBOL, _allocate, _restorable, _submit

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.types import Target


def _held():
    fixture = _restorable()
    _, account, _, _, _, old = fixture
    account.capital_budget_repair_streak = 0
    account.capital_peak = DEFAULT_CONFIG.initial_cash * 1.2
    account.operating_peak = DEFAULT_CONFIG.initial_cash * 1.1
    evidence = {"transition_damage": .1, "held_damage_ratio": 0.,
                "capital_budget_level": 1, "chronic_level": 0,
                "freeze_new_risk": True}
    evidence.update(dict.fromkeys(("independent_damage", "sector_guard_active",
                                  "acute_sector_evacuation", "strategic_damage_guard",
                                  "sentinel_freeze_new_risk"), False))
    risk = replace(old, evidence=evidence, shock_state="NONE")
    return fixture, risk


def test_held_repair_needs_five_real_days_and_survives_restart_and_fill():
    fixture, risk = _held()
    _, account, dates, panel, _, _ = fixture
    peaks = account.capital_peak, account.operating_peak
    for day in dates[:4]:
        for _ in range(2):
            targets = _allocate(fixture, day, account=account, risk=risk)
            assert not _submit(account, day, targets)
        account = account_from_dict(asdict(account))
    targets = _allocate(fixture, dates[4], account=account, risk=risk)
    target = next(t for t in targets if t.symbol == SYMBOL)
    assert target.mechanism == "POST_SHOCK_RESTORATION"
    assert target.weight == pytest.approx(risk.target_gross_cap)
    orders = _submit(account, dates[4], targets)
    assert len(orders) == 1 and orders[0].side == "BUY"
    account = account_from_dict(asdict(account))
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY"
    assert fills[0].fill_date > fills[0].signal_date
    targets = _allocate(fixture, dates[6], account=account, risk=risk)
    assert not _submit(account, dates[6], targets)
    assert (account.capital_peak, account.operating_peak) == peaks
    assert account.capital_budget_level == 1
    assert account.capital_budget_repair_streak == 0
    assert risk.freeze_new_risk and risk.evidence['freeze_new_risk']


@pytest.mark.parametrize("damage", ("sentinel_freeze_new_risk", "independent_damage",
                                    "sector_guard_active", "strategic_damage_guard",
                                    "acute_sector_evacuation", "transition_damage",
                                    "held_damage_ratio", "missing_evidence", "structure"))
def test_held_repair_damage_resets_confirmation_without_granting_risk(damage):
    fixture, risk = _held()
    _, account, dates, panel, _, _ = fixture
    for day in dates[:4]:
        assert not _submit(account, day, _allocate(fixture, day, risk=risk))
    evidence = dict(risk.evidence)
    if damage == "structure":
        panel[SYMBOL].loc[dates[4], "close"] = 8.
    elif damage == "missing_evidence":
        evidence.pop("held_damage_ratio")
    else:
        evidence[damage] = .8 if damage in {"transition_damage", "held_damage_ratio"} else True
    targets = _allocate(fixture, dates[4], risk=replace(risk, evidence=evidence))
    assert not any(o.side == "BUY" for o in _submit(account, dates[4], targets))
    targets = _allocate(fixture, dates[5], risk=risk)
    assert not any(o.side == "BUY" for o in _submit(account, dates[5], targets))


def test_missing_observed_day_restarts_held_repair_window():
    fixture, risk = _held()
    _, account, dates, _, _, _ = fixture
    for i in (0, 1, 3, 4, 5, 6):
        assert not _submit(account, dates[i], _allocate(fixture, dates[i], risk=risk))
    orders = _submit(account, dates[7], _allocate(fixture, dates[7], risk=risk))
    assert len(orders) == 1 and orders[0].mechanism == "POST_SHOCK_RESTORATION"


def test_held_repair_keeps_only_its_authorized_partial_remainder():
    fixture, risk = _held()
    _, account, dates, panel, _, _ = fixture
    for day in dates[:4]:
        assert not _submit(account, day, _allocate(fixture, day, risk=risk))
    orders = _submit(account, dates[4], _allocate(fixture, dates[4], risk=risk))
    assert len(orders) == 1 and orders[0].side == "BUY"
    original_id = orders[0].order_id
    panel[SYMBOL].loc[dates[5], "volume"] = 10_000.
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].side == "BUY" and account.pending_orders
    account = account_from_dict(asdict(account))
    retained = _submit(account, dates[5], _allocate(fixture, dates[5], account=account, risk=risk))
    assert len(retained) == 1 and retained[0].order_id == original_id
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[6], account=account, panel=panel)
    assert len(fills) == 1 and fills[0].order_id == original_id
    assert not account.pending_orders


@pytest.mark.parametrize("block", ("new_episode", "capital_tier", "chronic_tier", "ordinary_pending"))
def test_new_held_permission_does_not_borrow_another_episode_or_order(block):
    fixture, risk = _held()
    _, account, dates, _, _, _ = fixture
    for day in dates[:4]:
        assert not _submit(account, day, _allocate(fixture, day, risk=risk))
    if block == "new_episode":
        account.last_shock_date = str(dates[4].date())
    elif block == "capital_tier":
        account.capital_budget_level = 2
    elif block == "chronic_tier":
        account.chronic_level = 1
    else:
        _submit(account, dates[3], (Target(
            SYMBOL, .6, "CORE", .9, .9, "unqualified ordinary pending buy",
            origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
        ),))
        assert account.pending_orders
    targets = _allocate(fixture, dates[4], risk=risk)
    assert not any(o.side == "BUY" for o in _submit(account, dates[4], targets))
