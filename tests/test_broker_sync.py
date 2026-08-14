from __future__ import annotations

import copy

import pytest

from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Lifecycle,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    Position,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _identity(
    *,
    signal_date: str = "2026-01-05",
    symbol: str = "sz300308",
    target_weight: float = 0.50,
    lifecycle: str = Lifecycle.CORE.value,
    origin_subsystem: str = OriginSubsystem.LEADER.value,
    mechanism: str = AttributionMechanism.LEADER_SELECTION.value,
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "origin_subsystem": origin_subsystem,
        "mechanism": mechanism,
        "origin_lifecycle": lifecycle,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": REQUIRED_AI_UNIVERSE_SHA256,
    }
    fields["event_id"] = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=symbol,
        target_weight=target_weight,
        lifecycle=lifecycle,
        origin_lifecycle=lifecycle,
        origin_subsystem=origin_subsystem,
        mechanism=mechanism,
        replaces_symbol=None,
        industry_at_entry="optical",
        industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
        reduction_policy="FIFO",
        reason_code="strategy_target",
        exit_kind="strategy",
    )
    return fields


def _migration_event() -> dict[str, object]:
    return {
        "migrated_at_utc": "2026-01-01T00:00:00+00:00",
        "from_schema": 3,
        "to_schema": 4,
        "from_code_hash": "old-code",
        "to_code_hash": "code",
    }


def _legacy_tranche(
    *,
    symbol: str,
    shares: int,
    lifecycle: str = Lifecycle.CORE.value,
    highest_close: float = 1.0,
    avg_cost: float = 1.0,
) -> Tranche:
    return Tranche(
        tranche_id=f"migrated:{symbol}",
        lifecycle=lifecycle,
        shares=shares,
        avg_cost=avg_cost,
        entry_date="2026-01-02",
        sellable_date="2026-01-03",
        highest_close=highest_close,
        lowest_close=avg_cost,
        **_identity(
            signal_date="2026-01-02",
            symbol=symbol,
            target_weight=0.0,
            lifecycle=lifecycle,
            origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
            mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
        ),
    )


def _buy_account(*, requested_shares: int = 100) -> AccountState:
    remaining = requested_shares
    identity = _identity()
    pending = PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="entry",
        lifecycle="CORE",
        remaining_shares=remaining,
        order_id="O000000001",
        **identity,
    )
    order = AccountOrder(
        order_id="O000000001",
        signal_date="2026-01-05",
        submitted_date="2026-01-05",
        symbol="sz300308",
        side="BUY",
        target_weight=0.50,
        reason="entry",
        lifecycle="CORE",
        status=OrderStatus.OPEN.value,
        requested_shares=requested_shares,
        remaining_shares=remaining,
        **identity,
    )
    return AccountState(
        initial_cash=2_000.0,
        cash=2_000.0,
        pending_orders=[pending],
        order_ledger=[order],
        next_order_sequence=2,
        operating_peak=2_000.0,
        capital_peak=2_000.0,
    )


def _buy_fill(
    fill_id: str,
    *,
    shares: int,
    remaining_shares: int,
    final: bool,
    execution_sequence: int | None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "fill_id": fill_id,
        "order_id": "O000000001",
        "fill_date": "2026-01-06",
        "symbol": "300308",
        "side": "BUY",
        "shares": shares,
        "price": 10.0,
        "final": final,
        "remaining_shares": remaining_shares,
    }
    if execution_sequence is not None:
        result["execution_sequence"] = execution_sequence
    return result


def _position_snapshot(fills: list[dict[str, object]]) -> dict[str, object]:
    return {
        "as_of": "2026-01-06",
        "cash": 1_000.0,
        "fills": fills,
        "positions": [
            {
                "symbol": "300308",
                "shares": 100,
                "sellable_shares": 0,
                "avg_cost": 10.0,
            }
        ],
    }


def test_snapshot_cannot_overwrite_engine_owned_position_metadata() -> None:
    symbol = "sz300308"
    account = AccountState.empty(2_000.0)
    account.positions[symbol] = Position(
        symbol=symbol,
        shares=100,
        avg_cost=10.0,
        entry_date="2026-01-02",
        highest_close=15.0,
        lifecycle="ADD2",
        tranches=[
            Tranche(
                tranche_id="engine-lot",
                lifecycle="ADD2",
                shares=100,
                avg_cost=10.0,
                entry_date="2026-01-02",
                sellable_date="2026-01-03",
                highest_close=15.0,
                lowest_close=9.0,
                **_identity(
                    signal_date="2026-01-02",
                    symbol=symbol,
                    target_weight=0.0,
                    lifecycle="ADD2",
                    origin_subsystem=OriginSubsystem.LEGACY_MIGRATION.value,
                    mechanism=AttributionMechanism.LEGACY_MIGRATION.value,
                ),
            )
        ],
    )
    account.account_migrations = [_migration_event()]

    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-06",
            "cash": 1_000.0,
            "fills": [],
            "positions": [
                {
                    "symbol": "300308",
                    "shares": 100,
                    "sellable_shares": 100,
                    "avg_cost": 11.0,
                    "lifecycle": "SATELLITE",
                    "highest_close": 999.0,
                }
            ],
        },
    )

    position = account.positions[symbol]
    assert position.avg_cost == 11.0
    assert position.lifecycle == "ADD2"
    assert position.highest_close == 15.0
    assert position.tranches[0].lifecycle == "ADD2"
    assert position.tranches[0].highest_close == 15.0


def test_unmatched_external_inventory_fails_closed_without_a_planned_buy() -> None:
    account = AccountState.empty(2_000.0)
    before = account.to_dict()

    with pytest.raises(ValueError, match="exceeds known BUY lot inventory"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-06",
                "cash": 1_000.0,
                "fills": [],
                "positions": [
                    {
                        "symbol": "300308",
                        "shares": 100,
                        "sellable_shares": 100,
                        "avg_cost": 10.0,
                        "entry_date": "2000-01-01",
                        "lifecycle": "SATELLITE",
                        "highest_close": 999.0,
                    }
                ],
            },
        )

    assert account.to_dict() == before


def test_same_day_fill_permutations_reconcile_identically_by_execution_sequence() -> None:
    first = _buy_fill(
        "fill-1",
        shares=30,
        remaining_shares=70,
        final=False,
        execution_sequence=1,
    )
    second = _buy_fill(
        "fill-2",
        shares=70,
        remaining_shares=0,
        final=True,
        execution_sequence=2,
    )
    chronological = _buy_account()
    reversed_input = _buy_account()

    sync_broker_snapshot(chronological, _position_snapshot([first, second]))
    sync_broker_snapshot(reversed_input, _position_snapshot([second, first]))

    assert reversed_input.to_dict() == chronological.to_dict()
    assert [fill.fill_id for fill in chronological.fills] == ["fill-1", "fill-2"]
    assert chronological.order_ledger[0].status == OrderStatus.FILLED.value


def test_reverse_chronological_fill_array_is_sorted_by_execution_date() -> None:
    partial = _buy_fill(
        "fill-1",
        shares=30,
        remaining_shares=70,
        final=False,
        execution_sequence=None,
    )
    final = _buy_fill(
        "fill-2",
        shares=70,
        remaining_shares=0,
        final=True,
        execution_sequence=None,
    )
    final["fill_date"] = "2026-01-07"
    chronological = _buy_account()
    reversed_input = _buy_account()
    chronological_snapshot = _position_snapshot([partial, final])
    chronological_snapshot["as_of"] = "2026-01-07"
    reversed_snapshot = _position_snapshot([final, partial])
    reversed_snapshot["as_of"] = "2026-01-07"

    sync_broker_snapshot(chronological, chronological_snapshot)
    sync_broker_snapshot(reversed_input, reversed_snapshot)

    assert reversed_input.to_dict() == chronological.to_dict()
    assert [fill.fill_date for fill in chronological.fills] == ["2026-01-06", "2026-01-07"]


def test_multiple_same_day_fills_without_sequence_fail_atomically() -> None:
    account = _buy_account()
    before = copy.deepcopy(account.to_dict())
    fills = [
        _buy_fill(
            "fill-1",
            shares=30,
            remaining_shares=70,
            final=False,
            execution_sequence=None,
        ),
        _buy_fill(
            "fill-2",
            shares=70,
            remaining_shares=0,
            final=True,
            execution_sequence=None,
        ),
    ]

    with pytest.raises(ValueError, match="require explicit execution_sequence"):
        sync_broker_snapshot(account, _position_snapshot(fills))
    assert account.to_dict() == before


def test_same_day_increment_repeats_prior_fill_with_sequence() -> None:
    account = _buy_account()
    partial = _buy_fill(
        "fill-1",
        shares=30,
        remaining_shares=70,
        final=False,
        execution_sequence=1,
    )
    first_snapshot = {
        "as_of": "2026-01-06",
        "cash": 1_700.0,
        "fills": [partial],
        "positions": [
            {
                "symbol": "300308",
                "shares": 30,
                "sellable_shares": 0,
                "avg_cost": 10.0,
            }
        ],
    }
    sync_broker_snapshot(account, first_snapshot)

    final = _buy_fill(
        "fill-2",
        shares=70,
        remaining_shares=0,
        final=True,
        execution_sequence=2,
    )
    sync_broker_snapshot(account, _position_snapshot([final, partial]))

    assert [fill.fill_id for fill in account.fills] == ["fill-1", "fill-2"]
    assert account.order_ledger[0].status == OrderStatus.FILLED.value
    assert account.pending_orders == []


def test_final_fill_must_be_last_for_its_order() -> None:
    account = _buy_account()
    fills = [
        _buy_fill(
            "terminal-too-early",
            shares=30,
            remaining_shares=0,
            final=True,
            execution_sequence=1,
        ),
        _buy_fill(
            "after-terminal",
            shares=70,
            remaining_shares=0,
            final=True,
            execution_sequence=2,
        ),
    ]

    with pytest.raises(ValueError, match="must be its last reported fill"):
        sync_broker_snapshot(account, _position_snapshot(fills))


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("final", "requires explicit boolean final"),
        ("remaining_shares", "requires explicit remaining_shares"),
    ],
)
def test_new_order_fill_requires_explicit_completion_fields(
    missing: str,
    message: str,
) -> None:
    account = _buy_account(requested_shares=0)
    before = copy.deepcopy(account.to_dict())
    fill = _buy_fill(
        "fill-1",
        shares=100,
        remaining_shares=0,
        final=True,
        execution_sequence=None,
    )
    del fill[missing]

    with pytest.raises(ValueError, match=message):
        sync_broker_snapshot(account, _position_snapshot([fill]))
    assert account.to_dict() == before


def test_broker_zero_position_settles_strategic_sell_only_state_atomically() -> None:
    exiting = "sz300308"
    survivor = "sz300502"
    account = AccountState(
        initial_cash=100.0,
        cash=50.0,
        positions={
            exiting: Position(exiting, shares=30, avg_cost=1.0),
            survivor: Position(
                survivor,
                shares=20,
                avg_cost=1.0,
                entry_date="2026-01-02",
                highest_close=1.0,
                tranches=[_legacy_tranche(symbol=survivor, shares=20)],
            ),
        },
        account_migrations=[_migration_event()],
        strategic_cohort_symbols=[exiting, survivor],
        strategic_cohort_targets={exiting: 0.30, survivor: 0.20},
        strategic_exit_bands={exiting: [0.06] * 5},
        strategic_active_bands={exiting: [True] * 5},
        strategic_restore_weights={exiting: 0.30, survivor: 0.20},
        protected_weights={exiting: 0.30, survivor: 0.20},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    sync_broker_snapshot(
        account,
        {
            "as_of": "2026-01-06",
            "cash": 80.0,
            "fills": [],
            "positions": [
                {
                    "symbol": survivor,
                    "shares": 20,
                    "sellable_shares": 20,
                    "avg_cost": 1.0,
                }
            ],
        },
    )

    assert exiting not in account.strategic_cohort_targets
    assert exiting not in account.strategic_exit_bands
    assert exiting not in account.strategic_active_bands
    assert exiting not in account.strategic_restore_weights
    assert exiting not in account.protected_weights
    assert account.strategic_cohort_targets == {survivor: 0.20}
    assert account.strategic_restore_weights == {survivor: 0.20}
    assert account.protected_weights == {survivor: 0.20}


def test_broker_zero_position_releases_unowned_tactical_lifecycle() -> None:
    symbol = "sz300308"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0, lifecycle="RECOVERY")},
        tactical_anchor_symbol=symbol,
        candidate_tenure={"tactical_active": 1, "tactical_promotable": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    sync_broker_snapshot(
        account,
        {"as_of": "2026-01-06", "cash": 100.0, "fills": [], "positions": []},
    )

    assert account.tactical_anchor_symbol == ""
    assert account.candidate_tenure["tactical_active"] == 0
    assert account.candidate_tenure["tactical_promotable"] == 0
    assert account.candidate_tenure["tactical_cooldown"] == DEFAULT_CONFIG.tactical_rebound_cooldown_days


def test_broker_zero_position_releases_anchorless_ordinary_tactical_lifecycle() -> None:
    symbol = "sz300308"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0, lifecycle="RECOVERY")},
        candidate_tenure={"tactical_active": 1, "tactical_promotable": 0},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    sync_broker_snapshot(
        account,
        {"as_of": "2026-01-06", "cash": 100.0, "fills": [], "positions": []},
    )

    assert account.tactical_anchor_symbol == ""
    assert account.candidate_tenure["tactical_active"] == 0
    assert account.candidate_tenure["tactical_promotable"] == 0
    assert account.candidate_tenure["tactical_cooldown"] == DEFAULT_CONFIG.tactical_rebound_cooldown_days


def test_broker_zero_position_preserves_tactical_lifecycle_with_restore_owner() -> None:
    symbol = "sz300308"
    account = AccountState(
        initial_cash=100.0,
        cash=40.0,
        positions={symbol: Position(symbol, shares=60, avg_cost=1.0, lifecycle="RECOVERY")},
        tactical_anchor_symbol=symbol,
        protected_weights={symbol: 0.60},
        candidate_tenure={"tactical_active": 1, "tactical_promotable": 1},
        operating_peak=100.0,
        capital_peak=100.0,
    )

    sync_broker_snapshot(
        account,
        {"as_of": "2026-01-06", "cash": 100.0, "fills": [], "positions": []},
    )

    assert account.tactical_anchor_symbol == symbol
    assert account.candidate_tenure["tactical_active"] == 1
    assert account.protected_weights == {symbol: 0.60}
