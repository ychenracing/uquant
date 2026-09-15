"""Keep genuine long-cycle formation distinct from ordinary trend entry."""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_ordinary_trend_budget import _decide
from test_strategic_common_core_admission import _qualified
from test_strategic_grant_observation import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.engine import INDEX_SYMBOLS, ProductionEngine
from uquant.leader import REFERENCE_UNIVERSE
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.discovery import (
    _confirm_persistent_formation_entries,
    current_core_qualification,
)
from uquant.portfolio.strategic.qualification_candidates import candidate_entry
from uquant.types import AccountState

SYMBOLS = ("sz300308", "sz300394", "sz300502")


def _january_prefix(symbols=SYMBOLS):
    engine = ProductionEngine(Path(__file__).resolve().parents[1] / "data/frozen")
    engine._load(set(symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    panel = {symbol: engine._raw[symbol] for symbol in symbols}
    for date in pd.to_datetime(["2024-01-02", "2024-01-03"]):
        engine.execution.execute_open(date=date, account=account, panel=panel)
        decision = engine.decide(symbols=symbols, as_of=str(date.date()), account=account)
        account.pending_orders = list(decision.pending_orders)
    return engine, account, panel, decision


def test_confirmed_full_persistent_formation_uses_existing_full_budget():
    from uquant.execution import ExecutionPlanner

    policy, account, dates, panel, leaders, risk, _, _ = _formation_fixture()
    _decide(policy, account, dates[-2], panel, leaders, risk)
    assert all(not leaders[symbol].mature for symbol in SYMBOLS)
    assert {order.symbol for order in account.pending_orders} == set(SYMBOLS)
    assert [order.target_weight for order in account.pending_orders] == pytest.approx([1 / 3] * 3)
    grant = account.strategic_grant
    assert grant is not None
    assert all(order.epoch_id == grant.epoch_id for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-1], account=account, panel=panel,
    )
    assert len(fills) == 3 and all(fill.side == "BUY" and fill.shares > 0 for fill in fills)
    assert all(position.epoch_id == grant.epoch_id for position in account.positions.values())
    assert account.positions[grant.candidate_symbol].grant_id == grant.grant_id
    assert account.cash >= 0


def test_reference_only_third_member_cannot_supply_full_formation_capital():
    _, account, _, _ = _january_prefix(SYMBOLS[:2])
    assert account.strategic_grant is None and not account.pending_orders
    assert not account.positions


def _formation_fixture():
    dates = pd.bdate_range("2023-01-02", periods=247)
    panel = {symbol: _strategic_frame(dates) for symbol in SYMBOLS}
    for frame in panel.values():
        frame["ma60"] = frame["close"] * .9
        frame["ret60"] = .1
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * .99
        frame["volume"] = 100_000_000.
        frame["amount"] = frame["volume"] * frame["close"]
    leaders = {symbol: _leader(symbol, .60 - .05 * index, mature=False)
               for index, symbol in enumerate(SYMBOLS)}
    policy, account = PortfolioAllocator(DEFAULT_CONFIG), AccountState.empty(2_000_000.)
    account.code_hash = "persistent-formation-test"
    account.data_hash = "persistent-formation-fixture"
    for date in dates[-3:-1]:
        _decide(policy, account, date, panel, leaders, _risk(frozen=True))
        assert account.strategic_grant is None and not account.pending_orders
    risk, date = _risk(frozen=False), dates[-2]
    assert account.strategic_qualification.qualification_route == "persistent_industry"
    certificates = current_core_qualification(
        policy, date=date, user_panel=panel, leaders=leaders, account=account, risk=risk,
    )
    entries = {symbol: candidate_entry(
        policy, symbol=symbol, score=leaders[symbol], date=date, user_panel=panel,
        account=account, confirmation_days=5, certificate=certificates.get(symbol),
    ) for symbol in SYMBOLS}
    snapshots = policy._strategic_qualification_snapshots(date=date, user_panel=panel, leaders=leaders)
    return policy, account, dates, panel, leaders, risk, entries, snapshots


@pytest.mark.parametrize("failure", [None, "structure", "stale", "missing_certificate", "reference_only",
                                    "illiquid", "not_full", "other_route", "cash_rearm",
                                    "unconfirmed", "lost_persistence"])
def test_full_formation_requires_every_members_current_own_proof(failure):
    policy, account, dates, panel, leaders, risk, entries, snapshots = _formation_fixture()
    qualified = _qualified(account, SYMBOLS)
    peer = SYMBOLS[-1]
    if failure == "structure":
        entries[peer]["block"] = "STRUCTURE_NOT_REPAIRED"
    elif failure == "stale":
        entries[peer]["as_of"] = str(dates[-3].date())
    elif failure == "missing_certificate":
        entries[peer].pop("qualification_evidence_sha256")
    elif failure == "reference_only":
        panel.pop(peer)
    elif failure == "illiquid":
        panel[peer]["amount"] = 1.
    elif failure == "not_full":
        qualified = replace(qualified, symbols=list(SYMBOLS[:2]), quorum_route="STRONG_PAIR")
    elif failure == "other_route":
        qualified = replace(qualified, route="reversal_industry")
    elif failure == "cash_rearm":
        qualified = replace(qualified, cash_rearm_authorized=True)
    elif failure == "unconfirmed":
        account.replacement_tenure[f"strategic_eligibility:persistent_industry:{peer}"] = 1
    elif failure == "lost_persistence":
        snapshots[peer]["persistent_ret240"] = 0.
    _confirm_persistent_formation_entries(
        policy, qualified=qualified, entries=entries, snapshots=snapshots, leaders=leaders,
        account=account, risk=risk, date=dates[-2], user_panel=panel,
    )
    if failure in {None, "structure"}:
        assert all(entry.get("formation_quality") == "CONFIRMED_PERSISTENT" for entry in entries.values())
    else:
        assert all("formation_quality" not in entry for entry in entries.values())


def test_partial_formation_keeps_fills_and_revokes_invalid_owner_remainder():
    policy, account, dates, panel, leaders, risk, _, _ = _formation_fixture()
    _decide(policy, account, dates[-2], panel, leaders, risk)
    assert len(account.pending_orders) == 3
    for frame in panel.values():
        frame.loc[dates[-1], "volume"] = 1_000_000.
        frame.loc[dates[-1], "amount"] = frame.loc[dates[-1], "close"] * 1_000_000.
    from uquant.execution import ExecutionPlanner

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[-1], account=account, panel=panel)
    assert fills and account.pending_orders
    shares = {symbol: position.shares for symbol, position in account.positions.items()}
    assert account.strategic_grant is not None
    owner = account.strategic_grant.candidate_symbol
    panel[owner].loc[dates[-1], "close"] = .01
    _decide(policy, account, dates[-1], panel, leaders, risk)
    # FULL_COHORT ownership starts at the first real receipt. Healthy peers
    # retain their separately bound commitments; the damaged owner cannot add.
    assert not any(order.side == "BUY" and order.symbol == owner for order in account.pending_orders)
    assert any(order.side == "BUY" and order.symbol != owner for order in account.pending_orders)
    assert {symbol: position.shares for symbol, position in account.positions.items()} == shares
