from __future__ import annotations

from dataclasses import asdict, replace

import pandas as pd
import pytest
from test_strategic_grant_identity import _account_with_grant, _strategic_target

from uquant.account.codec import account_from_dict
from uquant.application.target_attribution import attach_target_attribution
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.execution.reconciliation import register_account_order
from uquant.models.strategic_grant import StrategicGrantStatus
from uquant.models.trading import account_order_decision_origin_session, strategic_economic_remaining_shares
from uquant.portfolio.strategic.authority import assess_strategic_capital_authority
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _submit(account, *, submitted_date: str = "2026-01-05") -> str:
    grant = account.strategic_grant
    assert grant is not None
    targets = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-05",
        targets=(_strategic_target(grant.grant_id),),
        retained_orders=account.pending_orders,
    )
    planned = plan_orders(
        signal_date=submitted_date,
        targets=targets,
        account=account,
        prices={"sz300308": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    previous = list(account.pending_orders)
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=previous,
            current=planned,
            submitted_date=submitted_date,
        )
    )
    return account.pending_orders[0].order_id


def _frame(rows: list[dict[str, float | str]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date")


def _tradable_rows(*dates: str, volume: float = 10_000_000.0) -> pd.DataFrame:
    return _frame(
        [
            {
                "date": session,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.0,
                "volume": volume,
                "amount": volume * 10.0,
            }
            for session in dates
        ]
    )


def _legacy_released_remainder(account) -> None:
    """Fixture for persisted external cancel/replace records, not executor output.

    The new production executor never creates this ambiguous physical state;
    imported historical rows must still retain their broker safety contracts.
    """
    predecessor = account.order_ledger[0]
    predecessor.status = "CANCELLED"
    predecessor.cancel_reason = "strategic partial remainder replaced"
    predecessor.last_event = "PARTIAL_REMAINDER_RELEASED"
    predecessor.remainder_release_session = "2026-01-06"
    predecessor.remainder_release_shares = predecessor.remaining_shares
    account.pending_orders = [replace(account.pending_orders[0], order_id="")]


def test_suspension_retains_grant_and_retries_when_trading_resumes() -> None:
    account = _account_with_grant()
    original_grant_id = account.strategic_grant.grant_id  # type: ignore[union-attr]
    order_id = _submit(account)
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    panel = {"sz300308": _tradable_rows("2026-01-05", "2026-01-07")}

    assert planner.execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    ) == []
    assert account.pending_orders[0].order_id == order_id
    assert account.order_ledger[0].last_event == "MISSING_OR_SUSPENDED"
    assert account.strategic_grant is not None
    assert account.strategic_grant.grant_id == original_grant_id

    fills = planner.execute_open(
        date=pd.Timestamp("2026-01-07"), account=account, panel=panel
    )

    assert len(fills) == 1
    assert fills[0].grant_id == original_grant_id
    assert account.strategic_grant.status == StrategicGrantStatus.ACTIVE.value
    assert account.strategic_grant.filled_shares == fills[0].shares


def test_limit_up_retains_grant_until_the_market_opens() -> None:
    account = _account_with_grant()
    order_id = _submit(account)
    panel = {
        "sz300308": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 10.0,
                    "low": 10.0,
                    "close": 10.0,
                    "volume": 10_000_000.0,
                    "amount": 100_000_000.0,
                },
                {
                    "date": "2026-01-06",
                    "open": 12.0,
                    "high": 12.0,
                    "low": 12.0,
                    "close": 12.0,
                    "volume": 10_000_000.0,
                    "amount": 120_000_000.0,
                },
                {
                    "date": "2026-01-07",
                    "open": 11.8,
                    "high": 12.0,
                    "low": 11.5,
                    "close": 11.8,
                    "volume": 10_000_000.0,
                    "amount": 118_000_000.0,
                },
            ]
        )
    }
    planner = ExecutionPlanner(DEFAULT_CONFIG)

    assert planner.execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=panel
    ) == []
    assert account.pending_orders[0].order_id == order_id
    assert account.order_ledger[0].last_event == "LIMIT_BLOCKED"
    fills = planner.execute_open(
        date=pd.Timestamp("2026-01-07"), account=account, panel=panel
    )
    assert fills
    assert fills[0].grant_id == account.strategic_grant.grant_id  # type: ignore[union-attr]


def test_partial_fill_retains_only_the_unfilled_quantity_on_the_same_order() -> None:
    """The frozen cross-AI contract supersedes fresh simulated physical retries."""
    account = _account_with_grant()
    first_order_id = _submit(account)
    planner = ExecutionPlanner(DEFAULT_CONFIG.override(max_volume_participation=0.002))
    planner.execute_open(
        date=pd.Timestamp("2026-01-06"), account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    order = account.order_ledger[0]
    remaining = order.remaining_shares
    assert remaining > 0 and order.status == "PARTIALLY_FILLED"
    assert account.pending_orders[0].order_id == first_order_id
    assert not order.replaced_by and not order.remainder_release_session
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=list(account.pending_orders),
        current=tuple(account.pending_orders), submitted_date="2026-01-06",
    ))
    fills = planner.execute_open(
        date=pd.Timestamp("2026-01-07"), account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", "2026-01-07")},
    )
    assert len(fills) == 1 and fills[0].shares == remaining
    assert len(account.order_ledger) == 1
    assert order.filled_shares == order.requested_shares
    assert account.strategic_grant.submitted_order_ids == [first_order_id]


@pytest.mark.parametrize("next_open", (20.0, 5.0))  # type: ignore[untyped-decorator]
def test_remainder_successor_keeps_registered_quantity_across_next_open_drift(
    next_open: float,
) -> None:
    """Price drift cannot rewrite the fixed physical remainder successor."""

    from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
        validate_exact_execution_chain,
    )

    account = _account_with_grant()
    first_order_id = _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    planner = ExecutionPlanner(cfg)
    first_panel = {
        "sz300308": _tradable_rows(
            "2026-01-05", "2026-01-06", volume=100_000.0
        )
    }
    first_fills = planner.execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=first_panel
    )
    assert len(first_fills) == 1
    _legacy_released_remainder(account)
    predecessor = account.order_ledger[0]
    released_shares = predecessor.remainder_release_shares
    assert released_shares == predecessor.remaining_shares > 0

    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=tuple(account.pending_orders),
            submitted_date="2026-01-06",
        )
    )
    successor = account.order_ledger[1]
    assert predecessor.order_id == first_order_id
    assert predecessor.replaced_by == successor.order_id
    assert successor.requested_shares == released_shares

    drift_panel = {
        "sz300308": _frame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.0,
                    "volume": 10_000_000.0,
                    "amount": 100_000_000.0,
                },
                {
                    "date": "2026-01-06",
                    "open": 10.0,
                    "high": max(10.2, next_open),
                    "low": min(9.8, next_open),
                    "close": next_open,
                    "volume": 100_000.0,
                    "amount": 1_000_000.0,
                },
                {
                    "date": "2026-01-07",
                    "open": next_open,
                    "high": next_open * 1.02,
                    "low": next_open * 0.98,
                    "close": next_open,
                    "volume": 10_000_000.0,
                    "amount": 10_000_000.0 * next_open,
                },
            ]
        )
    }
    final_fills = planner.execute_open(
        date=pd.Timestamp("2026-01-07"), account=account, panel=drift_panel
    )
    assert len(final_fills) == 1

    assert successor.requested_shares == released_shares
    assert successor.filled_shares + successor.remaining_shares == released_shares
    assert account.pending_orders == []
    assert account.strategic_grant is not None
    if next_open > 10.0:
        assert successor.status == "CANCELLED"
        assert successor.cancel_reason == "target already satisfied"
        assert account.strategic_grant.status == StrategicGrantStatus.ACTIVE.value
    else:
        assert successor.status == "FILLED"
        assert successor.cancel_reason == ""
        assert (
            account.strategic_grant.status
            == StrategicGrantStatus.PARTIALLY_FILLED.value
        )
    assert (
        account_order_decision_origin_session(
            successor,
            predecessor,
            prior_physical_fills=tuple(
                fill
                for fill in account.fills
                if fill.order_id == predecessor.order_id
            ),
        )
        == predecessor.remainder_release_session
    )

    def trace_target(order) -> dict[str, object]:
        return {
            "origin_subsystem": order.origin_subsystem,
            "event_id": order.event_id,
            "epoch_id": order.epoch_id,
            "grant_id": order.grant_id,
            "symbol": order.symbol,
            "weight": order.target_weight,
        }

    validate_exact_execution_chain(
        final_account=account.to_dict(),
        trace=(
            {
                "session": predecessor.signal_date,
                "orders": [asdict(predecessor)],
                "targets": [trace_target(predecessor)],
            },
            {
                "session": predecessor.remainder_release_session,
                "orders": [asdict(successor)],
                "targets": [trace_target(successor)],
            },
        ),
        epochs=(),
    )


@pytest.mark.parametrize("failure", ("quantity", "ambiguity"))
def test_partial_remainder_registration_rejection_is_atomic(failure: str) -> None:
    """A rejected fresh physical retry leaves caller and durable state untouched."""

    account = _account_with_grant()
    _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    ExecutionPlanner(cfg).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    _legacy_released_remainder(account)
    successor = account.pending_orders[0]
    if failure == "quantity":
        successor.remaining_shares += 1
        message = "successor quantity differs"
    else:
        predecessor = account.order_ledger[0]
        account.order_ledger.append(
            replace(
                predecessor,
                order_id=f"O{account.next_order_sequence:09d}",
            )
        )
        account.next_order_sequence += 1
        message = "predecessor is ambiguous"

    state_before = account.to_dict()
    successor_before = asdict(successor)

    with pytest.raises(RuntimeError, match=message):
        register_account_order(
            account,
            successor,
            submitted_date="2026-01-06",
        )

    assert account.to_dict() == state_before
    assert asdict(successor) == successor_before


def test_strategic_partial_remainder_survives_the_no_trade_band() -> None:
    account = _account_with_grant()
    _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    ExecutionPlanner(cfg).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    grant = account.strategic_grant
    assert grant is not None
    targets = attach_target_attribution(
        "optical",
        REQUIRED_AI_UNIVERSE_SHA256,
        signal_date="2026-01-06",
        targets=(_strategic_target(grant.grant_id),),
        retained_orders=account.pending_orders,
    )
    targets = (replace(targets[0], weight=0.04),)

    retained = merge_pending_orders(
        retained=list(account.pending_orders),
        planned=(),
        targets=targets,
        cfg=cfg,
    )

    assert len(retained) == 1
    assert retained[0].order_id == account.order_ledger[0].order_id
    assert retained[0].remaining_shares > 0
    assert retained[0].grant_id == grant.grant_id


def test_restart_round_trip_continues_the_original_grant() -> None:
    account = _account_with_grant()
    grant_id = account.strategic_grant.grant_id  # type: ignore[union-attr]
    order_id = _submit(account)
    suspended = {"sz300308": _tradable_rows("2026-01-05", "2026-01-07")}
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-06"), account=account, panel=suspended
    )

    restored = account_from_dict(account.to_dict())
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=pd.Timestamp("2026-01-07"), account=restored, panel=suspended
    )

    assert len(fills) == 1
    assert restored.strategic_grant is not None
    assert restored.strategic_grant.grant_id == grant_id
    assert restored.strategic_grant.submitted_order_ids == [order_id]
    assert fills[0].grant_id == grant_id


def test_late_fill_credits_the_original_grant_and_suppresses_the_retry() -> None:
    account = _account_with_grant()
    first_order_id = _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    ExecutionPlanner(cfg).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    _legacy_released_remainder(account)
    first_order = account.order_ledger[0]
    late_shares = first_order.remaining_shares
    retry = tuple(account.pending_orders)
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=retry,
            submitted_date="2026-01-06",
        )
    )
    retry_order_id = account.pending_orders[0].order_id
    original_filled = first_order.filled_shares

    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-07",
            "cash": 1_000_000.0,
            "fills": [
                {
                    "fill_id": "late-original-grant",
                    "order_id": first_order_id,
                    "fill_date": "2026-01-07",
                    "symbol": "300308",
                    "side": "BUY",
                    "shares": late_shares,
                    "price": 10.0,
                    "final": True,
                    "remaining_shares": 0,
                }
            ],
            "positions": [
                {
                    "symbol": "300308",
                    "shares": original_filled + late_shares,
                    "sellable_shares": original_filled,
                    "avg_cost": 10.0,
                }
            ],
        },
        cfg=cfg,
    )

    assert account.pending_orders == []
    assert account.order_ledger[0].status == "FILLED"
    retry_order = next(order for order in account.order_ledger if order.order_id == retry_order_id)
    assert retry_order.status == "CANCELLED"
    assert retry_order.cancel_reason == "late fill satisfied strategic grant"
    assert first_order.replaced_by == retry_order.order_id
    assert first_order.remainder_release_session == "2026-01-06"
    assert first_order.remainder_release_shares == late_shares
    assert (
        account_order_decision_origin_session(
            retry_order,
            first_order,
            prior_physical_fills=tuple(
                fill for fill in account.fills if fill.order_id == first_order.order_id
            ),
        )
        == "2026-01-06"
    )
    restored = account_from_dict(account.to_dict())
    restored_first = next(
        order for order in restored.order_ledger if order.order_id == first_order.order_id
    )
    restored_retry = next(
        order for order in restored.order_ledger if order.order_id == retry_order.order_id
    )
    assert (
        account_order_decision_origin_session(
            restored_retry,
            restored_first,
            prior_physical_fills=tuple(
                fill
                for fill in restored.fills
                if fill.order_id == restored_first.order_id
            ),
        )
        == "2026-01-06"
    )
    assert account.strategic_grant is not None
    assert account.strategic_grant.status == StrategicGrantStatus.ACTIVE.value
    assert account.strategic_grant.filled_shares == original_filled + late_shares
    assert {position.grant_id for position in account.positions.values()} == {
        account.strategic_grant.grant_id
    }


def test_nonfinal_late_fill_cannot_reopen_a_released_predecessor() -> None:
    """A partial broker fill cannot revive one half of a duplicated physical chain."""

    account = _account_with_grant()
    first_order_id = _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    ExecutionPlanner(cfg).execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    _legacy_released_remainder(account)
    first_order = account.order_ledger[0]
    late_shares = first_order.remaining_shares
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=tuple(account.pending_orders),
            submitted_date="2026-01-06",
        )
    )
    original_filled = first_order.filled_shares
    partial_late_shares = late_shares // 2
    before = account.to_dict()

    with pytest.raises(ValueError, match="released strategic remainder late fill must be final"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-07",
                "cash": 1_000_000.0,
                "fills": [
                    {
                        "fill_id": "partial-late-original-grant",
                        "order_id": first_order_id,
                        "fill_date": "2026-01-07",
                        "symbol": "300308",
                        "side": "BUY",
                        "shares": partial_late_shares,
                        "price": 10.0,
                        "final": False,
                        "remaining_shares": late_shares - partial_late_shares,
                    }
                ],
                "positions": [
                    {
                        "symbol": "300308",
                        "shares": original_filled + partial_late_shares,
                        "sellable_shares": original_filled,
                        "avg_cost": 10.0,
                    }
                ],
            },
            cfg=cfg,
        )

    assert account.to_dict() == before


def test_late_fill_is_rejected_after_the_retry_completed_the_economic_order() -> None:
    account = _account_with_grant()
    first_order_id = _submit(account)
    cfg = DEFAULT_CONFIG.override(max_volume_participation=0.002)
    planner = ExecutionPlanner(cfg)
    planner.execute_open(
        date=pd.Timestamp("2026-01-06"),
        account=account,
        panel={"sz300308": _tradable_rows("2026-01-05", "2026-01-06", volume=100_000.0)},
    )
    _legacy_released_remainder(account)
    first_order = account.order_ledger[0]
    late_shares = first_order.remaining_shares
    account.pending_orders = list(
        reconcile_account_orders(
            account=account,
            previous=list(account.pending_orders),
            current=tuple(account.pending_orders),
            submitted_date="2026-01-06",
        )
    )
    planner.execute_open(
        date=pd.Timestamp("2026-01-07"),
        account=account,
        panel={
            "sz300308": _tradable_rows(
                "2026-01-05", "2026-01-06", "2026-01-07", volume=10_000_000.0
            )
        },
    )
    position = account.positions["sz300308"]

    assert strategic_economic_remaining_shares(
        order=first_order, orders=account.order_ledger, fills=account.fills,
    ) == 0
    authority = assess_strategic_capital_authority(account)
    assert authority.late_fill_remaining_shares == ((first_order_id, 0),)
    # An internally satisfied request is not proof of external cancellation.
    assert authority.late_fill_order_ids == (first_order_id,)

    with pytest.raises(ValueError, match="strategic economic order is already satisfied"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-08",
                "cash": account.cash,
                "fills": [
                    {
                        "fill_id": "late-after-retry",
                        "order_id": first_order_id,
                        "fill_date": "2026-01-08",
                        "symbol": "300308",
                        "side": "BUY",
                        "shares": late_shares,
                        "price": 10.0,
                        "final": True,
                        "remaining_shares": 0,
                    }
                ],
                "positions": [
                    {
                        "symbol": "300308",
                        "shares": position.shares,
                        "sellable_shares": position.shares,
                        "avg_cost": position.avg_cost,
                    }
                ],
            },
            cfg=cfg,
        )
