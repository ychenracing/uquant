"""Live risk recovery permits existing restoration, never new entries."""

from dataclasses import replace

import pytest
from test_core_bounded_risk_restoration import SYMBOL, _allocate, _restorable, _submit

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.types import Risk


@pytest.mark.parametrize("missing", [None, "repair", "damage", "votes", "state",
                                     "shock", "sentinel", "capital", "chronic", "confirmation"])
def test_live_recovery_requires_current_held_repair_and_all_risk_permissions(missing):
    fixture = _restorable()
    _, account, dates, panel, _, risk = fixture
    panel[SYMBOL]["ret5"] = .01
    account.capital_budget_repair_streak = 0
    account.risk_streaks["concentrated_repair"] = DEFAULT_CONFIG.concentrated_repair_days
    risk = replace(risk, evidence={"transition_damage": .2, "held_repair_ratio": 1.,
                                   "held_damage_ratio": 0.})
    if missing == "confirmation":
        account.risk_streaks["concentrated_repair"] = 0
    elif missing == "repair":
        risk.evidence.pop("held_repair_ratio")
    elif missing == "damage":
        risk.evidence["held_damage_ratio"] = 1.
    elif missing == "votes":
        risk = replace(risk, votes=2)
    elif missing == "state":
        risk = replace(risk, state=Risk.CRISIS)
    elif missing == "shock":
        risk = replace(risk, shock_state="NONE")
    elif missing == "sentinel":
        risk.evidence["sentinel_freeze_new_risk"] = True
    elif missing == "capital":
        account.capital_budget_level = 2
    elif missing == "chronic":
        account.chronic_level = 2
    before = account.positions[SYMBOL].shares
    targets = _allocate(fixture, dates[0], risk=risk)
    orders = _submit(account, dates[0], targets)
    buys = [order for order in orders if order.side == "BUY"]
    assert bool(buys) == (missing is None)
    assert account.positions[SYMBOL].shares == before
    if missing is None:
        assert len(buys) == 1 and buys[0].mechanism == "POST_SHOCK_RESTORATION"
        assert buys[0].target_weight <= risk.target_gross_cap
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=dates[1], account=account, panel=panel,
        )
        assert len(fills) == 1 and fills[0].shares > 0 and account.cash >= 0


@pytest.mark.parametrize("missing", [None, "frame", "date", "close", "ma20", "ret5", "prior_close"])
def test_live_recovery_requires_complete_evidence_for_every_actual_holding(missing):
    from copy import deepcopy
    from types import SimpleNamespace

    from uquant.portfolio.pipeline import _bounded_ordinary_restore_risk_open
    from uquant.risk.market_book import _held_book_state

    fixture = _restorable()
    policy, account, dates, panel, _, risk = fixture
    day = dates[0]
    peer = "sh688009"
    account.positions[peer] = deepcopy(account.positions[SYMBOL])
    panel[peer] = panel[SYMBOL].copy()
    for frame in panel.values():
        frame.loc[day, "close"] = 10.1
        frame.loc[day, "ret5"] = .01
    account.capital_budget_repair_streak = 0
    account.risk_streaks["concentrated_repair"] = DEFAULT_CONFIG.concentrated_repair_days
    if missing == "frame":
        del panel[peer]
    elif missing == "date":
        panel[peer] = panel[peer].drop(index=day)
    elif missing == "ret5":
        panel[peer] = panel[peer].drop(columns="ret5")
    elif missing == "prior_close":
        prior = panel[peer].loc[:day].index[-2]
        panel[peer].loc[prior, "close"] = float("nan")
    elif missing:
        panel[peer].loc[day, missing] = float("nan")
    held = _held_book_state(date=day, user_panel=panel, account=account, cfg=DEFAULT_CONFIG)
    risk = replace(risk, evidence={"transition_damage": .2,
                                   "held_repair_ratio": held.repair_ratio,
                                   "held_damage_ratio": held.damage_ratio})
    book = SimpleNamespace(policy=policy, account=account, date=day, user_panel=panel, risk=risk)
    assert _bounded_ordinary_restore_risk_open(book) is (missing is None)
