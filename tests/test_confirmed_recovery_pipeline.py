"""Actual recovery admission after current risk reopens, in the same cash book."""
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from test_core_bounded_risk_restoration import _submit
from test_lifecycle_and_risk import _leader
from test_ordinary_trend_budget import _decide

from uquant.config import DEFAULT_CONFIG
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio_core import current_weights
from uquant.types import AccountState, Opportunity, Risk, RiskAssessment, Target

SYMBOLS = ("sz300308", "sz300394", "sz300502")


@pytest.fixture(scope="module")
def recovery_prefix():
    engine = ProductionEngine(Path(__file__).resolve().parents[1] / "data/frozen")
    engine._load(set(SYMBOLS) | set(INDEX_SYMBOLS) | set(REFERENCE_UNIVERSE))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in SYMBOLS}
    calendar = engine._raw["sh000300"].index
    snapshots = {}
    for date in calendar[(calendar >= "2025-04-01") & (calendar <= "2025-05-09")]:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        decision = engine.decide(symbols=SYMBOLS, as_of=str(date.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
        snapshots[str(date.date())] = deepcopy((account, decision))
    return engine, snapshots


def test_actual_tactical_probe_precedes_confirmed_recovery_and_fills(recovery_prefix):
    engine, snapshots = recovery_prefix
    frozen, decision = snapshots["2025-04-03"]
    assert decision.risk_summary["freeze_new_risk"] is True
    assert not frozen.positions
    assert len(frozen.pending_orders) == 1
    probe = frozen.pending_orders[0]
    assert (probe.symbol, probe.side, probe.mechanism) == ("sz300308", "BUY", "TACTICAL_REBOUND")
    assert probe.target_weight == pytest.approx(DEFAULT_CONFIG.tactical_probe_weight)
    first, _ = snapshots["2025-05-06"]
    second, _ = snapshots["2025-05-07"]
    assert not first.pending_orders and not second.pending_orders
    assert set(first.positions) == set(second.positions) == {probe.symbol}
    assert first.positions[probe.symbol].shares == second.positions[probe.symbol].shares
    confirmed, decision = snapshots["2025-05-08"]
    assert decision.risk_summary["freeze_new_risk"] is False
    assert len(confirmed.pending_orders) == 2
    prices = {symbol: float(engine._raw[symbol].loc["2025-05-08", "close"]) for symbol in SYMBOLS}
    held_weights, _ = current_weights(confirmed, prices)
    assert {order.symbol: order.target_weight for order in confirmed.pending_orders} == pytest.approx({
        "sz300394": (DEFAULT_CONFIG.recovery_target_gross - held_weights[probe.symbol]) / 2,
        "sz300502": (DEFAULT_CONFIG.recovery_target_gross - held_weights[probe.symbol]) / 2,
    })
    assert all(order.side == "BUY" and order.mechanism == "RECOVERY_COHORT"
               and order.origin_subsystem == "RECOVERY" and not order.grant_id and not order.epoch_id
               for order in confirmed.pending_orders)
    filled, _ = snapshots["2025-05-09"]
    assert set(filled.positions) == set(SYMBOLS)
    assert filled.positions[probe.symbol].shares == first.positions[probe.symbol].shares
    assert all(p.shares > 0 for p in filled.positions.values())
    assert len([fill for fill in filled.fills if fill.side == "BUY"]) == 3
    assert filled.cash >= 0


def test_repeating_same_day_does_not_fabricate_second_recovery_observation(recovery_prefix):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-06"])
    before = account.to_dict()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="strictly after"):
            engine.decide(symbols=SYMBOLS, as_of="2025-05-06", account=account)
        assert account.to_dict() == before


def test_real_partial_recovery_keeps_fills_and_cancels_lost_current_proof(recovery_prefix):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-08"])
    date = pd.Timestamp("2025-05-09")
    panel = {symbol: engine._raw[symbol].copy(deep=True) for symbol in SYMBOLS}
    for frame in panel.values():
        frame.loc[date, "volume"] = 100 / DEFAULT_CONFIG.max_volume_participation
        frame.loc[date, "amount"] = frame.loc[date, "close"] * frame.loc[date, "volume"]
    fills = engine.execution.execute_open(date=date, account=account, panel=panel)
    assert len(fills) == 2 and account.pending_orders
    assert {fill.symbol for fill in fills} == {"sz300394", "sz300502"}
    shares = {symbol: position.shares for symbol, position in account.positions.items()}
    decision = engine.decide(symbols=SYMBOLS, as_of=str(date.date()), account=account)
    account.pending_orders = list(decision.pending_orders)
    assert {symbol: position.shares for symbol, position in account.positions.items()} == shares
    assert not any(order.side == "BUY" and order.symbol in {"sz300308", "sz300394"}
                   for order in account.pending_orders)


def test_reference_only_third_member_cannot_create_immediate_full_recovery(recovery_prefix):
    engine, _ = recovery_prefix
    symbols = SYMBOLS[:2]
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in symbols}
    calendar = engine._raw["sh000300"].index
    for date in calendar[(calendar >= "2025-04-01") & (calendar <= "2025-05-09")]:
        engine.execution.execute_open(date=date, account=account, panel=panel)
        decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
        assert not account.pending_orders and not account.positions
    assert not account.anchor_weights


@pytest.mark.parametrize("exit_owner", ("risk", "strategy"))
@pytest.mark.parametrize("restore_cap", (.5, DEFAULT_CONFIG.recovery_target_gross))
def test_only_actual_risk_sales_preserve_flat_cohort_restoration(recovery_prefix, exit_owner, restore_cap):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-09"])
    panel = {symbol: engine._features[symbol] for symbol in SYMBOLS}
    leaders = {symbol: _leader(symbol, .9, industry="optical") for symbol in SYMBOLS}
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    shock = pd.Timestamp("2025-10-13")
    prices = {symbol: float(frame.loc[shock, "close"]) for symbol, frame in panel.items()}
    account.protected_weights = current_weights(account, prices)[0]
    account.last_shock_date = str(shock.date())
    if exit_owner == "risk":
        hard = RiskAssessment(Risk.CRISIS, 0., 4, {}, ("fixture crisis exit",), "SHOCK",
                              freeze_new_risk=True, reduction_level=3)
        _decide(policy, account, shock, panel, leaders, hard)
    else:
        _submit(account, shock, tuple(Target(
            symbol, 0., "RECOVERY", .9, .9, "final recovery strategy exit",
            origin_subsystem="RECOVERY", mechanism="RECOVERY_COHORT", origin_lifecycle="RECOVERY",
        ) for symbol in SYMBOLS))
    fills = engine.execution.execute_open(date=pd.Timestamp("2025-10-14"), account=account, panel=panel)
    assert len(fills) == 3 and all(fill.side == "SELL" for fill in fills)
    assert all(fill.exit_kind == ("crisis" if exit_owner == "risk" else "strategy") for fill in fills)
    assert not account.positions
    account.capital_budget_level = 1
    account.capital_budget_repair_streak = 1
    repair = RiskAssessment(Risk.CAUTION, restore_cap, 0, {
        "transition_damage": .1, "broad_ret120": .2, "tech_ret120": .4,
    }, (), "RECOVERY", freeze_new_risk=True, reduction_level=1)
    targets = _decide(policy, account, pd.Timestamp("2025-10-16"), panel, leaders, repair)
    restored = [target for target in targets if target.weight > 0]
    if exit_owner == "strategy":
        assert not restored and not account.pending_orders
        return
    expected = min(restore_cap, sum(min(DEFAULT_CONFIG.max_symbol_weight, weight)
                                   for weight in account.protected_weights.values()))
    assert restored and sum(target.weight for target in restored) == pytest.approx(expected)
    if restore_cap == DEFAULT_CONFIG.recovery_target_gross:
        assert {target.symbol for target in restored} == set(SYMBOLS)
    assert all(target.mechanism == "POST_SHOCK_RESTORATION" and target.lifecycle == "RECOVERY"
               and target.origin_subsystem == "RECOVERY" and target.origin_lifecycle == "RECOVERY"
               for target in restored)
    assert account.pending_orders and all(order.side == "BUY" for order in account.pending_orders)
    fills = engine.execution.execute_open(date=pd.Timestamp("2025-10-17"), account=account, panel=panel)
    assert fills and all(fill.side == "BUY" and fill.origin_subsystem == "RECOVERY"
                         and fill.origin_lifecycle == "RECOVERY" for fill in fills)


def test_recovery_admission_cannot_bypass_protected_owner_structure(recovery_prefix):
    engine, snapshots = recovery_prefix
    account, _ = deepcopy(snapshots["2025-05-09"])
    panel = {symbol: engine._features[symbol].copy(deep=True) for symbol in SYMBOLS}
    leaders = {symbol: _leader(symbol, .9, industry="optical") for symbol in SYMBOLS}
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    shock = pd.Timestamp("2025-10-13")
    account.last_shock_date = str(shock.date())
    account.protected_weights = current_weights(
        account, {symbol: float(frame.loc[shock, "close"]) for symbol, frame in panel.items()},
    )[0]
    hard = RiskAssessment(Risk.CRISIS, 0., 4, {}, ("fixture crisis exit",), "SHOCK",
                          freeze_new_risk=True, reduction_level=3)
    _decide(policy, account, shock, panel, leaders, hard)
    fills = engine.execution.execute_open(date=pd.Timestamp("2025-10-14"), account=account, panel=panel)
    assert len(fills) == 3 and not account.positions
    date = pd.Timestamp("2025-10-16")
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    for symbol, frame in panel.items():
        frame.loc[date, f"ma{DEFAULT_CONFIG.trend_medium}"] = prices[symbol] * 2
    risk = RiskAssessment(Risk.NORMAL, .95, 0, {"broad_ret120": .2, "tech_ret120": .4}, (),
                          "RECOVERY", freeze_new_risk=False)
    targets = policy.allocate(date=date, opportunity=Opportunity.RECOVERY, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices=prices)
    assert not any(target.weight > 0 for target in targets), (targets, account.anchor_weights,
                                                             risk.evidence.get("core_allocation"))
    assert all(row["restore_block"] == "STRUCTURE_NOT_REPAIRED"
               for row in risk.evidence["core_allocation"]["symbols"].values())
