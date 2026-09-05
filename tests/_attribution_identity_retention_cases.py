from __future__ import annotations

import json

import pandas as pd
import pytest
from test_attribution_identity import (
    _identity,
)

from uquant import types as domain
from uquant.account import (
    UnsupportedAccountSchemaError,
    load_account,
    save_account,
)
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.engine import _attach_target_attribution
from uquant.execution import (
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from uquant.portfolio import PortfolioAllocator


@pytest.mark.parametrize(
    "identity_change",
    [
        {"mechanism": domain.AttributionMechanism.CRISIS.value},
        {
            "origin_subsystem": domain.OriginSubsystem.RECOVERY.value,
            "mechanism": domain.AttributionMechanism.RECOVERY_CAP.value,
        },
        {"replaces_symbol": "sz300308"},
        {"industry_at_entry": "semiconductor"},
        {"event_id": "evt_" + "f" * 64},
    ],
)
def test_full_exit_retention_requires_identical_causal_identity(
    identity_change: dict[str, str],
) -> None:
    retained_identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="full exit",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **retained_identity,
    )
    changed_identity = {**retained_identity, **identity_change}
    if "event_id" not in identity_change:
        changed_identity["event_id"] = domain.derive_attribution_event_id(
            signal_date="2026-01-06",
            symbol=retained.symbol,
            target_weight=0.0,
            lifecycle=retained.lifecycle,
            origin_lifecycle=str(changed_identity["origin_lifecycle"]),
            origin_subsystem=str(changed_identity["origin_subsystem"]),
            mechanism=str(changed_identity["mechanism"]),
            replaces_symbol=changed_identity.get("replaces_symbol"),
            industry_at_entry=str(changed_identity["industry_at_entry"]),
            industry_manifest_sha256=str(
                changed_identity["industry_manifest_sha256"]
            ),
            reduction_policy=domain.ReductionPolicy.FIFO.value,
            reason_code="strategy_target",
            exit_kind="strategy",
        )
    planned = domain.PendingOrder(
        signal_date="2026-01-06",
        symbol=retained.symbol,
        side=retained.side,
        target_weight=0.0,
        reason=retained.reason,
        lifecycle=retained.lifecycle,
        **changed_identity,
    )
    target = domain.Target(
        symbol=retained.symbol,
        weight=0.0,
        lifecycle=retained.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason=retained.reason,
        **changed_identity,
    )

    merged = merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert merged == (planned,)

def test_unchanged_full_exit_retains_the_same_broker_order() -> None:
    identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="full exit",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        reason_code="risk_off",
        exit_kind="risk_off",
        **identity,
    )
    planned = domain.PendingOrder(
        signal_date="2026-01-06",
        symbol=retained.symbol,
        side=retained.side,
        target_weight=retained.target_weight,
        reason="different display prose",
        lifecycle=retained.lifecycle,
        reason_code=retained.reason_code,
        exit_kind="renamed_display_exit",
        **identity,
    )
    target = domain.Target(
        symbol=retained.symbol,
        weight=retained.target_weight,
        lifecycle=retained.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason=planned.reason,
        reason_code=planned.reason_code,
        exit_kind=planned.exit_kind,
        **identity,
    )

    assert merge_pending_orders(
        retained=[retained],
        planned=(planned,),
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    ) == (retained,)

@pytest.mark.parametrize("remaining_shares", (0, 100))
def test_production_full_exit_retains_originating_event_for_residual_shares(
    remaining_shares: int,
) -> None:
    """A later classifier cannot relabel an already-submitted full liquidation."""
    identity = _identity(
        target_weight=0.0,
        origin_subsystem=domain.OriginSubsystem.LEADER.value,
        mechanism=domain.AttributionMechanism.LEADER_ROTATION.value,
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.SELL.value,
        target_weight=0.0,
        reason="leader rotation exit",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        # A blocked order has not been sized yet, so zero is still an active
        # quantity state rather than proof that the liquidation is complete.
        remaining_shares=remaining_shares,
        **identity,
    )
    raw_target = domain.Target(
        symbol=retained.symbol,
        weight=0.0,
        lifecycle=retained.lifecycle,
        alpha_score=0.0,
        confidence=0.0,
        reason="leader lifecycle exit",
        origin_subsystem=domain.OriginSubsystem.LEADER.value,
        mechanism=domain.AttributionMechanism.LEADER_LIFECYCLE_EXIT.value,
        origin_lifecycle=retained.lifecycle,
    )

    target = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=(raw_target,),
        retained_orders=(retained,),
    )[0]
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=(target,),
        account=domain.AccountState(
            initial_cash=1_000.0,
            cash=0.0,
            positions={
                retained.symbol: domain.Position(
                    retained.symbol,
                    shares=100,
                    avg_cost=10.0,
                )
            },
        ),
        prices={retained.symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )

    assert target.event_id == retained.event_id
    assert target.mechanism == retained.mechanism
    assert merge_pending_orders(
        retained=[retained],
        planned=planned,
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    ) == (retained,)

def test_partial_buy_keeps_originating_event_across_daily_mechanism_reclassification() -> None:
    """A still-active GTC buy owns its cause until its economic intent changes."""

    identity = _identity(
        target_weight=0.35,
        origin_subsystem=domain.OriginSubsystem.STRATEGIC.value,
        mechanism=domain.AttributionMechanism.STRATEGIC_RESTORATION.value,
        reason_code="strategic_cohort",
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.35,
        reason="strategic restoration",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=400,
        attempts=1,
        reason_code="strategic_cohort",
        **identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.pending_orders = [retained]
    raw_target = domain.Target(
        symbol=retained.symbol,
        weight=0.351,
        lifecycle=retained.lifecycle,
        alpha_score=0.8,
        confidence=0.9,
        reason="strategic cohort hold",
        reason_code=retained.reason_code,
        origin_subsystem=domain.OriginSubsystem.STRATEGIC.value,
        mechanism=domain.AttributionMechanism.STRATEGIC_COHORT.value,
        origin_lifecycle=retained.lifecycle,
    )

    target = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=(raw_target,),
        retained_orders=account.pending_orders,
    )[0]
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=(target,),
        account=account,
        prices={retained.symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )

    assert tuple(
        getattr(target, field) for field in domain.ATTRIBUTION_IDENTITY_FIELDS
    ) == tuple(
        getattr(retained, field) for field in domain.ATTRIBUTION_IDENTITY_FIELDS
    )
    assert len(planned) == 1
    assert planned[0].event_id == retained.event_id
    assert merge_pending_orders(
        retained=account.pending_orders,
        planned=planned,
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    ) == (retained,)

def test_blocked_recovery_replacement_retains_event_and_link_next_session() -> None:
    identity = _identity(
        signal_date="2026-01-05",
        symbol="sz300502",
        target_weight=0.30,
        origin_subsystem=domain.OriginSubsystem.RECOVERY.value,
        mechanism=domain.AttributionMechanism.RECOVERY_SUBSTITUTION.value,
        replaces_symbol="sh688008",
    )
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.30,
        reason="recovery anchor entry: replaces sh688008",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        attempts=1,
        **identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.pending_orders = [retained]
    account.anchor_weights = {"sz300308": 0.30, "sz300502": 0.30}
    account.candidate_tenure["recovery_substitution_pending"] = 1
    account.replacement_events.append(
        {
            "signal_date": "2026-01-05",
            "old_symbol": "sh688008",
            "new_symbol": "sz300502",
            "route": "recovery_anchor_substitution",
        }
    )
    leaders = {
        symbol: domain.LeaderScore(
            symbol=symbol,
            score=0.8,
            confidence=0.9,
            mature=True,
            emerging=False,
            industry=industry,
            components={},
        )
        for symbol, industry in {
            "sz300308": "film",
            "sz300502": "optical",
        }.items()
    }
    targets = PortfolioAllocator(DEFAULT_CONFIG)._recovery_anchor_substitution(
        date=pd.Timestamp("2026-01-06"),
        risk=domain.RiskAssessment(
            state=domain.Risk.NORMAL,
            target_gross_cap=1.0,
            votes=0,
            evidence={},
            reasons=(),
            shock_state="NONE",
        ),
        user_panel={},
        leaders=leaders,
        account=account,
        weights_now={"sz300308": 0.30, "sz300502": 0.0},
        anchor_elapsed=DEFAULT_CONFIG.recovery_add_window_days + 1,
    )
    assert targets is not None
    targets = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=targets,
        retained_orders=account.pending_orders,
    )
    replacement = next(target for target in targets if target.symbol == retained.symbol)
    planned = plan_orders(
        signal_date="2026-01-06",
        targets=targets,
        account=account,
        prices={"sz300308": 10.0, "sz300502": 10.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders,
        planned=planned,
        targets=targets,
        cfg=DEFAULT_CONFIG,
    )
    merged_replacement = next(order for order in merged if order.symbol == retained.symbol)

    assert replacement.replaces_symbol == "sh688008"
    assert replacement.event_id == retained.event_id
    assert merged_replacement is retained
    assert merged_replacement.replaces_symbol == "sh688008"

def test_reduced_buy_target_supersedes_larger_intent_inside_no_trade_band() -> None:
    retained_identity = _identity(target_weight=0.95)
    retained = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.95,
        reason="strategic cohort",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=1_900,
        attempts=3,
        **retained_identity,
    )
    account = domain.AccountState.empty(2_000_000.0)
    account.pending_orders = [retained]
    target = domain.Target(
        symbol=retained.symbol,
        weight=0.931,
        lifecycle=retained.lifecycle,
        alpha_score=0.8,
        confidence=0.9,
        reason="retain strategic price drift",
        origin_subsystem=domain.OriginSubsystem.LEADER.value,
        mechanism=domain.AttributionMechanism.LEADER_SELECTION.value,
        origin_lifecycle=domain.Lifecycle.CORE.value,
    )
    target = _attach_target_attribution(
        signal_date="2026-01-06",
        targets=(target,),
        retained_orders=account.pending_orders,
    )[0]

    planned = plan_orders(
        signal_date="2026-01-06",
        targets=(target,),
        account=account,
        prices={retained.symbol: 10.0},
        cfg=DEFAULT_CONFIG,
    )
    merged = merge_pending_orders(
        retained=account.pending_orders,
        planned=planned,
        targets=(target,),
        cfg=DEFAULT_CONFIG,
    )

    assert target.event_id != retained.event_id
    assert len(planned) == 1
    assert planned[0].target_weight == target.weight
    assert planned[0].event_id == target.event_id
    assert len(merged) == 1 and merged[0] is not retained

def test_new_buy_without_pit_universe_membership_fails_closed() -> None:
    symbol = "sz000001"
    identity = _identity(symbol=symbol)
    target = domain.Target(
        symbol=symbol,
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="outside reviewed universe",
        **identity,
    )

    with pytest.raises(RuntimeError, match="no point-in-time AI-universe membership"):
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={symbol: 10.0},
            cfg=DEFAULT_CONFIG,
        )

def test_new_buy_without_any_attribution_cannot_bypass_planning_validation() -> None:
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="empty machine attribution must not create a BUY",
    )

    with pytest.raises(RuntimeError, match="event_id"):
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={"sz300502": 10.0},
            cfg=DEFAULT_CONFIG,
        )

def test_risk_off_identity_cannot_fabricate_a_native_buy_at_any_boundary() -> None:
    identity = _identity(
        origin_subsystem=domain.OriginSubsystem.RISK.value,
        mechanism=domain.AttributionMechanism.RISK_OFF.value,
    )
    target = domain.Target(
        symbol="sz300502",
        weight=0.05,
        lifecycle=domain.Lifecycle.CORE.value,
        alpha_score=0.8,
        confidence=0.9,
        reason="fabricated semantic BUY",
        reason_code="strategy_target",
        exit_kind="strategy",
        **identity,
    )
    with pytest.raises(RuntimeError, match="not permitted for BUY"):
        plan_orders(
            signal_date="2026-01-05",
            targets=(target,),
            account=domain.AccountState.empty(2_000_000.0),
            prices={"sz300502": 10.0},
            cfg=DEFAULT_CONFIG,
        )

    pending = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="fabricated semantic BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        remaining_shares=100,
        **identity,
    )
    reconcile_account = domain.AccountState.empty(2_000_000.0)
    before_reconcile = reconcile_account.to_dict()
    with pytest.raises(RuntimeError, match="not permitted for BUY"):
        reconcile_account_orders(
            account=reconcile_account,
            previous=[],
            current=(pending,),
            submitted_date="2026-01-05",
        )
    assert reconcile_account.to_dict() == before_reconcile

    pending.order_id = "O000000001"
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.OPEN.value,
        requested_shares=100,
        remaining_shares=100,
        **identity,
    )
    broker_account = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    before_broker = broker_account.to_dict()
    with pytest.raises(RuntimeError, match="not permitted for BUY"):
        sync_broker_snapshot(
            broker_account,
            {
                "as_of": "2026-01-06",
                "cash": 1_998_994.9,
                "positions": [
                    {"symbol": "sz300502", "shares": 100, "avg_cost": 10.051}
                ],
                "fills": [
                    {
                        "fill_id": "fabricated-risk-buy",
                        "order_id": "O000000001",
                        "symbol": "sz300502",
                        "side": "BUY",
                        "shares": 100,
                        "price": 10.0,
                        "gross_value": 1_000.0,
                        "commission": 5.0,
                        "transfer_fee": 0.1,
                        "final": True,
                        "remaining_shares": 0,
                    }
                ],
            },
        )
    assert broker_account.to_dict() == before_broker

def test_native_schema_legacy_identity_cannot_fabricate_a_new_buy(tmp_path) -> None:
    signal_date = "2026-01-05"
    identity = {
        "event_id": domain.derive_attribution_event_id(
            signal_date=signal_date,
            symbol="sz300502",
            target_weight=0.05,
            lifecycle=domain.Lifecycle.CORE.value,
            origin_lifecycle=domain.Lifecycle.CORE.value,
            origin_subsystem=domain.OriginSubsystem.LEGACY_MIGRATION.value,
            mechanism=domain.AttributionMechanism.LEGACY_MIGRATION.value,
            replaces_symbol=None,
            industry_at_entry="legacy_unmapped",
            industry_manifest_sha256="0" * 64,
            reduction_policy=domain.ReductionPolicy.FIFO.value,
            reason_code="strategy_target",
            exit_kind="strategy",
        ),
        "origin_subsystem": domain.OriginSubsystem.LEGACY_MIGRATION.value,
        "mechanism": domain.AttributionMechanism.LEGACY_MIGRATION.value,
        "origin_lifecycle": domain.Lifecycle.CORE.value,
        "industry_at_entry": "legacy_unmapped",
        "industry_manifest_sha256": "0" * 64,
    }
    pending = domain.PendingOrder(
        signal_date=signal_date,
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="fabricated native legacy BUY",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
        **identity,
    )
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=signal_date,
        submitted_date=signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.SUBMITTED.value,
        requested_shares=100,
        remaining_shares=100,
        **identity,
    )
    account = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
        data_hash="data",
        code_hash="code",
    )
    destination = tmp_path / "fabricated-native-legacy-buy.json"

    with pytest.raises(RuntimeError, match="legacy migration identity cannot create a BUY"):
        save_account(account, destination)

    destination.write_text(json.dumps(account.to_dict()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="legacy migration identity cannot create a BUY"):
        load_account(destination)

def test_broker_rejects_a_planned_buy_without_canonical_attribution() -> None:
    pending = domain.PendingOrder(
        signal_date="2026-01-05",
        symbol="sz300502",
        side=domain.Side.BUY.value,
        target_weight=0.05,
        reason="missing attribution",
        lifecycle=domain.Lifecycle.CORE.value,
        order_id="O000000001",
        remaining_shares=100,
    )
    ledger = domain.AccountOrder(
        order_id=pending.order_id,
        signal_date=pending.signal_date,
        submitted_date=pending.signal_date,
        symbol=pending.symbol,
        side=pending.side,
        target_weight=pending.target_weight,
        reason=pending.reason,
        lifecycle=pending.lifecycle,
        status=domain.OrderStatus.SUBMITTED.value,
        requested_shares=100,
        remaining_shares=100,
    )
    account = domain.AccountState(
        initial_cash=2_000_000.0,
        cash=2_000_000.0,
        pending_orders=[pending],
        order_ledger=[ledger],
        next_order_sequence=2,
        operating_peak=2_000_000.0,
        capital_peak=2_000_000.0,
    )
    before = account.to_dict()

    with pytest.raises(RuntimeError, match="invalid event_id"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-06",
                "cash": 2_000_000.0,
                "positions": [],
                "fills": [],
            },
        )

    assert account.to_dict() == before

def test_save_rejects_non_current_schema(tmp_path) -> None:
    account = domain.AccountState.empty(2_000_000.0)
    account.schema_version = domain.ACCOUNT_SCHEMA_VERSION - 1

    with pytest.raises(
        UnsupportedAccountSchemaError,
        match=(
            rf"unsupported account schema {domain.ACCOUNT_SCHEMA_VERSION - 1}; "
            rf"expected {domain.ACCOUNT_SCHEMA_VERSION}"
        ),
    ):
        save_account(account, tmp_path / "account.json")


def test_unmatched_broker_inventory_fails_closed_without_a_planned_buy() -> None:
    account = domain.AccountState.empty(2_000_000.0)
    account.data_hash = "data"
    account.code_hash = "code"
    before = account.to_dict()

    with pytest.raises(ValueError, match="exceeds known BUY lot inventory"):
        sync_broker_snapshot(
            account,
            {
                "as_of": "2026-01-07",
                "cash": 1_999_000.0,
                "positions": [
                    {
                        "symbol": "sz300502",
                        "shares": 100,
                        "sellable_shares": 100,
                        "avg_cost": 10.0,
                    }
                ],
                "fills": [],
            },
        )

    assert account.to_dict() == before
