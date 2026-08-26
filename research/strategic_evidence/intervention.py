"""Atomic research-only strategic-owner interventions over durable accounts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from math import isfinite
from typing import Any

from uquant.account import account_from_dict, economic_state_sha256
from uquant.data import normalize_symbol
from uquant.types import (
    AccountOrder,
    AccountState,
    Decision,
    PendingOrder,
    derive_attribution_event_id,
)


def _replace_symbol_list(values: list[str], old: str | None, new: str) -> list[str]:
    return [new if old is not None and value == old else value for value in values]


def _replace_symbol_map(values: dict[str, Any], old: str | None, new: str) -> dict[str, Any]:
    return {
        (new if old is not None and symbol == old else symbol): deepcopy(value)
        for symbol, value in values.items()
    }


def _assert_no_collision(values: dict[str, Any], old: str, new: str) -> None:
    if old != new and old in values and new in values:
        raise ValueError("strategic owner rewrite key collision")


def _rewrite_order(order: PendingOrder, old: str, new: str) -> None:
    """Move one pending strategic intent while preserving its native identity."""

    if order.symbol != old:
        return
    order.symbol = new
    if order.replaces_symbol == old:
        order.replaces_symbol = new
    if order.event_id:
        order.event_id = derive_attribution_event_id(
            signal_date=order.signal_date,
            symbol=order.symbol,
            target_weight=order.target_weight,
            lifecycle=order.lifecycle,
            origin_lifecycle=order.origin_lifecycle,
            origin_subsystem=order.origin_subsystem,
            mechanism=order.mechanism,
            replaces_symbol=order.replaces_symbol,
            industry_at_entry=order.industry_at_entry,
            industry_manifest_sha256=order.industry_manifest_sha256,
            reduction_policy=order.reduction_policy,
            reason_code=order.reason_code,
            exit_kind=order.exit_kind,
        )


def _rewrite_account_order(order: AccountOrder, old: str, new: str) -> None:
    if order.symbol != old:
        return
    pending = PendingOrder(
        signal_date=order.signal_date,
        symbol=order.symbol,
        side=order.side,
        target_weight=order.target_weight,
        reason=order.reason,
        lifecycle=order.lifecycle,
        reduction_policy=order.reduction_policy,
        reason_code=order.reason_code,
        exit_kind=order.exit_kind,
        event_id=order.event_id,
        origin_subsystem=order.origin_subsystem,
        mechanism=order.mechanism,
        origin_lifecycle=order.origin_lifecycle,
        replaces_symbol=order.replaces_symbol,
        industry_at_entry=order.industry_at_entry,
        industry_manifest_sha256=order.industry_manifest_sha256,
    )
    _rewrite_order(pending, old, new)
    order.symbol, order.event_id, order.replaces_symbol = (
        pending.symbol,
        pending.event_id,
        pending.replaces_symbol,
    )


class StrategicOwnerIntervention:
    """Replace one active strategic owner at exactly one replay decision point."""

    def __init__(self, *, owner: str, target_gross: float) -> None:
        normalized = normalize_symbol(owner)
        if not isinstance(target_gross, (int, float)) or isinstance(target_gross, bool):
            raise ValueError("strategic owner target gross must be numeric")
        if not isfinite(float(target_gross)) or not 0.0 <= float(target_gross) <= 1.0:
            raise ValueError("strategic owner target gross must be between zero and one")
        self.owner = normalized
        self.target_gross = float(target_gross)
        self._applied = False

    @property
    def applied(self) -> bool:
        return self._applied

    def apply(self, account: AccountState) -> dict[str, Any]:
        """Rewrite one owner all-or-nothing, then validate the full account codec."""

        if self._applied:
            raise RuntimeError("strategic owner intervention is one-shot")
        before = economic_state_sha256(account)
        source_owners = tuple(dict.fromkeys(account.strategic_cohort_symbols))
        if len(source_owners) > 1:
            raise ValueError("mixed strategic owner intervention is forbidden")
        source_owner = source_owners[0] if source_owners else None
        shadow = deepcopy(account)
        if source_owner is None:
            shadow.strategic_cohort_symbols = [self.owner]
            shadow.strategic_cohort_targets = {self.owner: self.target_gross}
        else:
            shadow.strategic_cohort_symbols = _replace_symbol_list(
                shadow.strategic_cohort_symbols, source_owner, self.owner
            )
            for name in (
                "strategic_cohort_targets",
                "strategic_exit_bands",
                "strategic_active_bands",
                "strategic_restore_weights",
            ):
                _assert_no_collision(getattr(shadow, name), source_owner, self.owner)
                setattr(shadow, name, _replace_symbol_map(getattr(shadow, name), source_owner, self.owner))
            shadow.active_leaders = _replace_symbol_list(shadow.active_leaders, source_owner, self.owner)
            shadow.strategic_previous_symbols = _replace_symbol_list(
                shadow.strategic_previous_symbols, source_owner, self.owner
            )
            shadow.risk_anchor_symbols = _replace_symbol_list(
                shadow.risk_anchor_symbols, source_owner, self.owner
            )
            for order in shadow.pending_orders:
                _rewrite_order(order, source_owner, self.owner)
            shadow.strategic_cohort_targets[self.owner] = self.target_gross
        try:
            account_from_dict(shadow.to_dict(), require_hashes=False)
        except RuntimeError as exc:
            raise ValueError("strategic owner rewrite violates account invariants") from exc
        for field in fields(AccountState):
            setattr(account, field.name, getattr(shadow, field.name))
        self._applied = True
        return {
            "applied": True,
            "source_owner": source_owner,
            "forced_owner": self.owner,
            "target_gross": self.target_gross,
            "before_account_sha256": before,
            "after_account_sha256": economic_state_sha256(account),
        }

    def preserve_activation(self, account: AccountState, decision: Decision) -> Decision:
        """Research-only activation boundary; preserve the forced owner into next-open execution."""

        strategic = tuple(
            target
            for target in decision.targets
            if target.origin_subsystem == "STRATEGIC" and target.mechanism == "STRATEGIC_COHORT"
        )
        if len(strategic) != 1:
            raise ValueError("forced owner activation requires exactly one production strategic target")
        original = strategic[0]
        if original.symbol == self.owner:
            return decision
        shadow = deepcopy(account)
        source_owner = original.symbol
        for name in (
            "strategic_cohort_targets",
            "strategic_exit_bands",
            "strategic_active_bands",
            "strategic_restore_weights",
        ):
            _assert_no_collision(getattr(shadow, name), source_owner, self.owner)
            setattr(shadow, name, _replace_symbol_map(getattr(shadow, name), source_owner, self.owner))
        shadow.strategic_cohort_symbols = _replace_symbol_list(
            shadow.strategic_cohort_symbols, source_owner, self.owner
        )
        shadow.strategic_cohort_targets[self.owner] = self.target_gross
        for order in shadow.order_ledger:
            _rewrite_account_order(order, source_owner, self.owner)
        account_from_dict(shadow.to_dict(), require_hashes=False)
        for field in fields(AccountState):
            setattr(account, field.name, getattr(shadow, field.name))
        forced_target = replace(original, symbol=self.owner, weight=self.target_gross)
        forced_target = replace(
            forced_target,
            event_id=derive_attribution_event_id(
                signal_date=decision.date,
                symbol=forced_target.symbol,
                target_weight=forced_target.weight,
                lifecycle=forced_target.lifecycle,
                origin_lifecycle=forced_target.origin_lifecycle,
                origin_subsystem=forced_target.origin_subsystem,
                mechanism=forced_target.mechanism,
                replaces_symbol=forced_target.replaces_symbol,
                industry_at_entry=forced_target.industry_at_entry,
                industry_manifest_sha256=forced_target.industry_manifest_sha256,
                reduction_policy=forced_target.reduction_policy,
                reason_code=forced_target.reason_code,
                exit_kind=forced_target.exit_kind,
            ),
        )
        forced_orders = tuple(
            replace(
                order, symbol=self.owner, target_weight=self.target_gross, event_id=forced_target.event_id
            )
            if order.symbol == source_owner and order.origin_subsystem == "STRATEGIC"
            else order
            for order in decision.pending_orders
        )
        return replace(
            decision,
            targets=tuple(forced_target if item is original else item for item in decision.targets),
            pending_orders=forced_orders,
        )


__all__ = ("StrategicOwnerIntervention",)
