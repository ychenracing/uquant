"""Ordinary bounded rotation can reduce genuinely owned strategic CORE inventory."""
from __future__ import annotations

from dataclasses import asdict

from test_strategic_core_structural_exit import _aged_core
from test_strategic_probe_holding import OWNER, _allocate, _submit
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio.allocation_book import AllocationBook
from uquant.portfolio.pipeline import _book_targets, _record_completed_transfer
from uquant.portfolio_core import current_weights
from uquant.types import AttributionMechanism


def test_strategic_core_rotation_preserves_native_ownership_through_fifo_and_restart():
    allocator, account, dates, panel, leaders, roles = _aged_core()
    date, fill_date = dates[:2]
    strategic = _allocate(allocator, account, date, panel, leaders, roles)
    retained = next(target for target in strategic if target.symbol == OWNER)
    epoch = account.strategic_epochs[0]
    assert retained.origin_subsystem == "STRATEGIC"
    assert retained.grant_id == epoch.grant_id and retained.epoch_id == epoch.epoch_id
    prices = {OWNER: float(panel[OWNER].loc[date, "close"])}
    weights, _ = current_weights(account, prices)
    book = AllocationBook(
        policy=allocator, date=date, risk=_risk(), user_panel=panel, leaders=leaders,
        account=account, prices=prices, weights_now=weights, owned={OWNER},
        strategic_targets={OWNER: retained}, proposed={OWNER: weights[OWNER] / 2},
        committed={OWNER: weights[OWNER]}, cash_room=0.0,
        reasons={OWNER: "leader rotation: bounded transfer after confirmed deterioration"},
        mechanisms={OWNER: AttributionMechanism.LEADER_ROTATION},
    )
    # This is the merge/execution boundary after bounded transfer has chosen a
    # reduction; it does not claim to re-prove challenger qualification here.
    targets = _book_targets(book)
    assert len(targets) == 1
    rotation = targets[0]
    assert rotation.origin_subsystem == "LEADER"
    assert rotation.mechanism == "LEADER_ROTATION"
    for field in ("grant_id", "epoch_id", "lifecycle", "origin_lifecycle", "reduction_policy"):
        assert getattr(rotation, field) == getattr(retained, field)

    before_shares = account.positions[OWNER].shares
    before_cash = account.cash
    orders = _submit(account, targets, date, panel)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].origin_subsystem == "LEADER"
    assert orders[0].mechanism == "LEADER_ROTATION"
    assert orders[0].grant_id == epoch.grant_id
    assert orders[0].epoch_id == epoch.epoch_id
    assert orders[0].reduction_policy == "FIFO"

    challenger = "sz300502"
    transfer = f"core_transfer:{OWNER}->{challenger}"
    account.replacement_tenure[transfer] = DEFAULT_CONFIG.replacement_confirm_days
    account.candidate_tenure[f"core_transfer_session:{OWNER}->{challenger}"] = date.toordinal()
    _record_completed_transfer(book, challenger)
    assert challenger not in book.replacements
    assert account.cash == before_cash and account.positions[OWNER].shares == before_shares

    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=fill_date, account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL"
    assert 0 < fills[0].shares < before_shares
    assert fills[0].origin_subsystem == "LEADER"
    assert fills[0].mechanism == "LEADER_ROTATION"
    assert fills[0].epoch_id == epoch.epoch_id and fills[0].grant_id == epoch.grant_id
    assert account.cash > before_cash
    assert account.positions[OWNER].shares == before_shares - fills[0].shares
    assert not epoch.terminal
    restored = account_from_dict(asdict(account))
    assert restored.positions[OWNER].epoch_id == epoch.epoch_id
    assert restored.positions[OWNER].grant_id == epoch.grant_id
    assert not restored.strategic_epochs[0].terminal

    book.account, book.date = restored, fill_date
    _record_completed_transfer(book, challenger)
    assert book.replacements[challenger] == OWNER
    assert book.mechanisms[challenger] is AttributionMechanism.LEADER_ROTATION
    assert restored.replacement_tenure[transfer] == 0
