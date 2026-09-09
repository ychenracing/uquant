"""Native cancellation settles an empty deployment before fresh admission."""
from __future__ import annotations

import pandas as pd

from research.candidate_runner import CandidateRunner, CausalReplayDataStore
from scripts.run_strategic_ownership_acceptance import (
    _failed_grant_fixture,
    _write_prices,
)
from uquant.engine import ProductionEngine
from uquant.market import ReplayHarness
from uquant.models.strategic_universe import build_strategic_universe_declaration
from uquant.types import AccountState

OPTICAL = ("sz300308", "sz300502", "sz300394")
MATERIAL = ("sh688019", "sh688300", "sz300666")


def _native_fixture(root, *, peer_fills=False):
    _failed_grant_fixture(root)
    if peer_fills:
        dates = pd.bdate_range("2022-01-03", "2023-02-28")
        _write_prices(root, symbol="sz300666", dates=dates, daily_returns=[.005] * len(dates))
    engine = ProductionEngine(root)
    engine.data = CausalReplayDataStore(root)
    harness = ReplayHarness(
        workspace=engine.workspace,
        universe=CandidateRunner(root).replay_universe((*OPTICAL, *MATERIAL)),
    )
    panel = harness.raw_panel((*OPTICAL, *MATERIAL))
    return engine, AccountState.empty(engine.cfg.initial_cash), panel


def _native_day(engine, account, panel, session, *, removed=False):
    date = pd.Timestamp(session)
    fills = engine.execution.execute_open(date=date, account=account, panel=panel)
    symbols = OPTICAL if removed else (*OPTICAL, *MATERIAL)
    references = tuple(sorted(symbols))
    decision = engine.decide(
        symbols=symbols, as_of=session, account=account,
        strategic_universe_declaration=build_strategic_universe_declaration(
            qualification_reference_symbols=references, risk_reference_symbols=references,
        ),
    )
    account.pending_orders = list(decision.pending_orders)
    return decision, fills


def test_empty_failed_deployment_waits_for_real_cancellation_then_creates_fresh_grant(tmp_path):
    engine, account, panel = _native_fixture(tmp_path)
    _native_day(engine, account, panel, "2023-01-03")
    _native_day(engine, account, panel, "2023-01-04")
    first = account.strategic_grant
    assert first is not None and first.status == "PENDING_EXECUTION"
    old_orders = tuple(account.order_ledger)
    assert old_orders and all(order.status == "SUBMITTED" for order in old_orders)
    decision, fills = _native_day(engine, account, panel, "2023-01-05", removed=True)
    assert not fills and not account.positions
    assert first.status == "EXPIRED" and first.filled_shares == 0
    assert not any(target.weight > 0 for target in decision.targets)
    assert all(order.status == "CANCELLED" for order in old_orders)
    assert not account.pending_orders
    # Cancellation is now genuinely reconciled; no calendar-based extra wait.
    decision, fills = _native_day(engine, account, panel, "2023-01-06", removed=True)
    assert not fills
    second = account.strategic_grant
    assert second is not None and second.grant_id != first.grant_id
    assert second.previous_grant_id == first.grant_id
    assert account.strategic_epochs[0].realized_status == "EXPIRED"
    assert any(order.side == "BUY" and order.grant_id == second.grant_id
               for order in decision.pending_orders)
    _, fills = _native_day(engine, account, panel, "2023-01-09", removed=True)
    assert any(fill.side == "BUY" and fill.shares > 0 and fill.grant_id == second.grant_id
               for fill in fills)
    assert first.filled_shares == 0 and second.filled_shares > 0


def test_owner_zero_fill_does_not_hide_actual_filled_peer_deployment(tmp_path):
    engine, account, panel = _native_fixture(tmp_path, peer_fills=True)
    _native_day(engine, account, panel, "2023-01-03")
    _native_day(engine, account, panel, "2023-01-04")
    first = account.strategic_grant
    assert first is not None
    decision, fills = _native_day(engine, account, panel, "2023-01-05", removed=True)
    assert first.status == "EXPIRED" and first.filled_shares == 0
    assert any(fill.symbol == "sz300666" and fill.shares > 0 and fill.epoch_id == first.epoch_id
               for fill in fills)
    assert account.positions["sz300666"].shares > 0
    assert not account.strategic_epochs[0].terminal
    retained = next(target for target in decision.targets if target.symbol == "sz300666")
    assert retained.weight > 0 and retained.epoch_id == first.epoch_id
    assert retained.origin_subsystem == "STRATEGIC"
