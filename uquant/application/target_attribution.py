"""Causal target-attribution ownership for application decisions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

from ..config import DEFAULT_CONFIG, SystemConfig
from ..contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, AIUniverse, default_ai_universe
from ..types import PendingOrder, Side, Target, derive_attribution_event_id


def _retained_full_exit(target: Target, retained: PendingOrder | None) -> bool:
    return bool(
        retained is not None
        and retained.side == Side.SELL.value
        and abs(retained.target_weight) <= 1e-12
        and abs(target.weight) <= 1e-12
        and retained.lifecycle == target.lifecycle
        and retained.reduction_policy == target.reduction_policy
    )


def _retained_partial_buy(
    target: Target,
    retained: PendingOrder | None,
    cfg: SystemConfig,
) -> bool:
    return bool(
        retained is not None
        and retained.side == Side.BUY.value
        and target.weight > 1e-12
        and abs(retained.target_weight - target.weight) < cfg.min_trade_weight
        and retained.lifecycle == target.lifecycle
        and retained.reduction_policy == target.reduction_policy
        and retained.reason_code == target.reason_code
        and retained.exit_kind == target.exit_kind
        and retained.origin_subsystem == target.origin_subsystem
        and retained.origin_lifecycle == target.origin_lifecycle
        and retained.replaces_symbol == target.replaces_symbol
    )


def _retained_target_identity(
    target: Target,
    retained: PendingOrder | None,
    cfg: SystemConfig,
) -> bool:
    return bool(
        retained is not None
        and abs(retained.target_weight - target.weight) < cfg.min_trade_weight
        and retained.lifecycle == target.lifecycle
        and retained.reduction_policy == target.reduction_policy
        and retained.origin_subsystem == target.origin_subsystem
        and retained.mechanism == target.mechanism
        and retained.origin_lifecycle == target.origin_lifecycle
        and retained.replaces_symbol == target.replaces_symbol
    )


def _reuse_retained_attribution(
    target: Target,
    retained: PendingOrder | None,
    cfg: SystemConfig,
) -> Target | None:
    if _retained_full_exit(target, retained):
        retained_identity = cast(PendingOrder, retained)
        return replace(
            target,
            event_id=retained_identity.event_id,
            origin_subsystem=retained_identity.origin_subsystem,
            mechanism=retained_identity.mechanism,
            origin_lifecycle=retained_identity.origin_lifecycle,
            replaces_symbol=retained_identity.replaces_symbol,
            industry_at_entry=retained_identity.industry_at_entry,
            industry_manifest_sha256=retained_identity.industry_manifest_sha256,
        )
    if _retained_partial_buy(target, retained, cfg):
        retained_identity = cast(PendingOrder, retained)
        return replace(
            target,
            event_id=retained_identity.event_id,
            origin_subsystem=retained_identity.origin_subsystem,
            mechanism=retained_identity.mechanism,
            origin_lifecycle=retained_identity.origin_lifecycle,
            replaces_symbol=retained_identity.replaces_symbol,
            industry_at_entry=retained_identity.industry_at_entry,
            industry_manifest_sha256=retained_identity.industry_manifest_sha256,
        )
    if _retained_target_identity(target, retained, cfg):
        retained_identity = cast(PendingOrder, retained)
        return replace(
            target,
            event_id=retained_identity.event_id,
            industry_at_entry=retained_identity.industry_at_entry,
            industry_manifest_sha256=retained_identity.industry_manifest_sha256,
        )
    return None


def _attribute_target(
    *,
    legacy_industry: str,
    legacy_manifest_sha256: str,
    signal_date: str,
    target: Target,
    retained: PendingOrder | None,
    universe: AIUniverse,
    cfg: SystemConfig,
) -> Target:
    if target.event_id:
        return target
    reused = _reuse_retained_attribution(target, retained, cfg)
    if reused is not None:
        return reused
    industry = universe.industry_of(target.symbol, signal_date)
    manifest = REQUIRED_AI_UNIVERSE_SHA256
    if industry == "unknown":
        industry = legacy_industry
        manifest = legacy_manifest_sha256
    event_id = derive_attribution_event_id(
        signal_date=signal_date,
        symbol=target.symbol,
        target_weight=target.weight,
        lifecycle=target.lifecycle,
        origin_lifecycle=target.origin_lifecycle,
        origin_subsystem=target.origin_subsystem,
        mechanism=target.mechanism,
        replaces_symbol=target.replaces_symbol,
        industry_at_entry=industry,
        industry_manifest_sha256=manifest,
        reduction_policy=target.reduction_policy,
        reason_code=target.reason_code,
        exit_kind=target.exit_kind,
    )
    return replace(
        target,
        event_id=event_id,
        industry_at_entry=industry,
        industry_manifest_sha256=manifest,
    )


def attach_target_attribution(
    legacy_industry: str,
    legacy_manifest_sha256: str,
    *,
    signal_date: str,
    targets: tuple[Target, ...],
    retained_orders: Iterable[PendingOrder] = (),
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> tuple[Target, ...]:
    """Finalize deterministic IDs and PIT industry for newly causal targets."""

    universe = default_ai_universe()
    retained_by_symbol = {order.symbol: order for order in retained_orders if order.event_id}
    return tuple(
        _attribute_target(
            legacy_industry=legacy_industry,
            legacy_manifest_sha256=legacy_manifest_sha256,
            signal_date=signal_date,
            target=target,
            retained=retained_by_symbol.get(target.symbol),
            universe=universe,
            cfg=cfg,
        )
        for target in targets
    )
