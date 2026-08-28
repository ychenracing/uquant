"""Atomic research-only strategic-owner interventions over durable accounts."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import fields, replace
from math import isfinite
from typing import Any

from uquant.account import account_from_dict, economic_state_sha256
from uquant.contracts.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe
from uquant.data import normalize_symbol
from uquant.models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    close_strategic_epoch,
    derive_strategic_epoch_id,
)
from uquant.types import (
    AccountOrder,
    AccountState,
    Decision,
    PendingOrder,
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_attribution_event_id,
    derive_strategic_grant_id,
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
        grant_id=order.grant_id,
    )
    _rewrite_order(pending, old, new)
    order.symbol, order.event_id, order.replaces_symbol = (
        pending.symbol,
        pending.event_id,
        pending.replaces_symbol,
    )


def _rewrite_grant(
    account: AccountState,
    *,
    old: str,
    new: str,
    target_weight: float,
    session: str,
) -> str:
    prior = account.strategic_grant
    if prior is None:
        return ""
    evidence = hashlib.sha256(
        "|".join((prior.qualification_evidence_sha256, old, new, session)).encode("utf-8")
    ).hexdigest()
    signature = prior.qualification_signature.replace(old, new)
    previous_grant_id = prior.grant_id
    grant_id = derive_strategic_grant_id(
        account_identity=prior.account_identity,
        candidate_symbol=new,
        qualification_signature=signature,
        qualification_route=prior.qualification_route,
        qualification_evidence_sha256=evidence,
        created_session=session,
        previous_grant_id=previous_grant_id,
        production_source_identity=prior.production_source_identity,
    )
    submitted = [order.order_id for order in account.order_ledger if order.symbol == old]
    acknowledged = [
        order.order_id
        for order in account.order_ledger
        if order.symbol == old and order.status != "SUBMITTED"
    ]
    held_position = account.positions.get(old)
    held_shares = held_position.shares if held_position is not None else 0
    account.strategic_grant = StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=new,
        qualification_signature=signature,
        qualification_route=prior.qualification_route,
        qualification_evidence_sha256=evidence,
        created_session=session,
        last_eligible_session=session,
        first_submission_session=(prior.first_submission_session if submitted else ""),
        last_submission_session=(prior.last_submission_session if submitted else ""),
        healthy_retry_sessions=prior.healthy_retry_sessions,
        submitted_order_ids=submitted,
        acknowledged_order_ids=acknowledged,
        filled_shares=max(prior.filled_shares, held_shares),
        target_weight=target_weight,
        status=(
            StrategicGrantStatus.ACTIVE.value
            if held_shares > 0
            or account.candidate_tenure.get("strategic_cohort_active", 0) == 1
            else StrategicGrantStatus.PENDING_EXECUTION.value
            if submitted
            else StrategicGrantStatus.QUALIFIED.value
        ),
        previous_grant_id=previous_grant_id,
        account_identity=prior.account_identity,
        production_source_identity=prior.production_source_identity,
        qualification_quorum=prior.qualification_quorum,
    )
    observation = account.strategic_qualification
    if observation.candidate_symbol == old:
        observation.candidate_symbol = new
        observation.qualification_signature = signature
        observation.qualification_evidence_sha256 = evidence
        observation.qualification_last_observed_session = session
    return grant_id


def _replace_counterfactual_epoch(
    account: AccountState,
    *,
    old: str,
    new: str,
    grant_id: str,
    session: str,
    target_weight: float,
) -> str:
    """Rewrite the epoch only inside the atomic research counterfactual fork."""

    grant = account.strategic_grant
    if grant is None or not grant.previous_grant_id:
        return ""
    matches = [
        epoch
        for epoch in account.strategic_epochs
        if not epoch.terminal
        and epoch.owner_symbol == old
        and epoch.grant_id == grant.previous_grant_id
    ]
    if not matches:
        return ""
    if len(matches) != 1:
        raise ValueError("forced owner found duplicate nonterminal epochs")
    previous = matches[0]
    realized = bool(previous.first_fill_session)
    if not realized and previous.realized_status != StrategicEpochStatus.PROBE.value:
        raise ValueError("forced owner found an invalid unfilled strategic epoch")
    if realized and previous.realized_status not in {
        StrategicEpochStatus.CORE.value,
        StrategicEpochStatus.ACTIVE.value,
    }:
        raise ValueError("forced owner found an invalid realized strategic epoch")
    opened_session = previous.opened_session if realized else session
    previous_epoch_id = previous.previous_epoch_id if realized else previous.epoch_id
    if not realized:
        close_strategic_epoch(
            previous,
            closed_session=session,
            close_reason="research owner intervention",
            expired=True,
        )
    epoch_id = derive_strategic_epoch_id(
        account_identity=grant.account_identity,
        owner_symbol=new,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        grant_id=grant_id,
        opened_session=opened_session,
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity=previous.config_identity,
        evidence_sha256=grant.qualification_evidence_sha256,
    )
    epoch = StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=new,
        qualification_signature=grant.qualification_signature,
        qualification_route=grant.qualification_route,
        qualification_quorum=grant.qualification_quorum,
        grant_id=grant_id,
        opened_session=opened_session,
        first_fill_session=previous.first_fill_session if realized else "",
        active_session=previous.active_session if realized else "",
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity=previous.config_identity,
        evidence_sha256=grant.qualification_evidence_sha256,
        realized_status=(
            previous.realized_status if realized else StrategicEpochStatus.PROBE.value
        ),
        target_weight=target_weight,
        full_weight=max(previous.full_weight, target_weight),
        account_identity=grant.account_identity,
    )
    epoch.validate()
    grant.epoch_id = epoch_id
    if realized:
        index = account.strategic_epochs.index(previous)
        account.strategic_epochs[index] = epoch
        if account.active_strategic_epoch_id == previous.epoch_id:
            account.active_strategic_epoch_id = epoch_id
        for ownership in (
            account.protected_weight_epoch_ids,
            account.strategic_restore_epoch_ids,
        ):
            for symbol, owner_epoch_id in tuple(ownership.items()):
                if owner_epoch_id == previous.epoch_id:
                    ownership[symbol] = epoch_id
        if account.recovery_owner_epoch_id == previous.epoch_id:
            account.recovery_owner_epoch_id = epoch_id
    else:
        account.strategic_epochs.append(epoch)
    return epoch_id


def _rewrite_account_identity_chain(
    account: AccountState,
    *,
    old: str,
    new: str,
    forced_industry: str,
    grant_id: str,
    epoch_id: str = "",
) -> None:
    event_rewrites: dict[str, str] = {}
    for pending_order in account.pending_orders:
        if pending_order.symbol != old:
            continue
        prior_event = pending_order.event_id
        _rewrite_order(pending_order, old, new)
        pending_order.industry_at_entry = forced_industry
        pending_order.industry_manifest_sha256 = REQUIRED_AI_UNIVERSE_SHA256
        pending_order.grant_id = grant_id
        pending_order.epoch_id = epoch_id
        pending_order.event_id = derive_attribution_event_id(
            signal_date=pending_order.signal_date,
            symbol=pending_order.symbol,
            target_weight=pending_order.target_weight,
            lifecycle=pending_order.lifecycle,
            origin_lifecycle=pending_order.origin_lifecycle,
            origin_subsystem=pending_order.origin_subsystem,
            mechanism=pending_order.mechanism,
            replaces_symbol=pending_order.replaces_symbol,
            industry_at_entry=pending_order.industry_at_entry,
            industry_manifest_sha256=pending_order.industry_manifest_sha256,
            reduction_policy=pending_order.reduction_policy,
            reason_code=pending_order.reason_code,
            exit_kind=pending_order.exit_kind,
        )
        event_rewrites[prior_event] = pending_order.event_id
    ledger = {order.order_id: order for order in account.order_ledger}
    for account_order in account.order_ledger:
        if account_order.symbol != old:
            continue
        prior_event = account_order.event_id
        _rewrite_account_order(account_order, old, new)
        account_order.industry_at_entry = forced_industry
        account_order.industry_manifest_sha256 = REQUIRED_AI_UNIVERSE_SHA256
        account_order.grant_id = grant_id
        account_order.epoch_id = epoch_id
        account_order.event_id = derive_attribution_event_id(
            signal_date=account_order.signal_date,
            symbol=account_order.symbol,
            target_weight=account_order.target_weight,
            lifecycle=account_order.lifecycle,
            origin_lifecycle=account_order.origin_lifecycle,
            origin_subsystem=account_order.origin_subsystem,
            mechanism=account_order.mechanism,
            replaces_symbol=account_order.replaces_symbol,
            industry_at_entry=account_order.industry_at_entry,
            industry_manifest_sha256=account_order.industry_manifest_sha256,
            reduction_policy=account_order.reduction_policy,
            reason_code=account_order.reason_code,
            exit_kind=account_order.exit_kind,
        )
        event_rewrites[prior_event] = account_order.event_id
    for fill in account.fills:
        if fill.symbol != old:
            continue
        ledger_order = ledger.get(fill.order_id)
        if ledger_order is None:
            raise ValueError("forced owner cannot rewrite an unlinked historical fill")
        fill.symbol = new
        fill.event_id = ledger_order.event_id
        fill.origin_subsystem = ledger_order.origin_subsystem
        fill.mechanism = ledger_order.mechanism
        fill.origin_lifecycle = ledger_order.origin_lifecycle
        fill.replaces_symbol = ledger_order.replaces_symbol
        fill.industry_at_entry = ledger_order.industry_at_entry
        fill.industry_manifest_sha256 = ledger_order.industry_manifest_sha256
        fill.grant_id = grant_id
        fill.epoch_id = epoch_id
        for allocation in fill.sold_tranches:
            prior_event = str(allocation.get("event_id", ""))
            if prior_event in event_rewrites:
                allocation["event_id"] = event_rewrites[prior_event]
                allocation["industry_at_entry"] = forced_industry
                allocation["industry_manifest_sha256"] = REQUIRED_AI_UNIVERSE_SHA256
                allocation["grant_id"] = grant_id
                allocation["epoch_id"] = epoch_id
    if old in account.positions:
        _assert_no_collision(account.positions, old, new)
        position = account.positions.pop(old)
        position.symbol = new
        position.grant_id = grant_id
        position.epoch_id = epoch_id
        for tranche in position.tranches:
            if tranche.event_id not in event_rewrites:
                raise ValueError("forced owner tranche lacks a rewritten originating event")
            tranche.event_id = event_rewrites[tranche.event_id]
            tranche.industry_at_entry = forced_industry
            tranche.industry_manifest_sha256 = REQUIRED_AI_UNIVERSE_SHA256
            tranche.grant_id = grant_id
            tranche.epoch_id = epoch_id
        account.positions[new] = position
class StrategicOwnerIntervention:
    """Replace one active strategic owner at exactly one replay decision point."""

    def __init__(
        self,
        *,
        owner: str,
        target_gross: float,
        intervention_date: str | None = None,
    ) -> None:
        normalized = normalize_symbol(owner)
        if not isinstance(target_gross, (int, float)) or isinstance(target_gross, bool):
            raise ValueError("strategic owner target gross must be numeric")
        if not isfinite(float(target_gross)) or not 0.0 <= float(target_gross) <= 1.0:
            raise ValueError("strategic owner target gross must be between zero and one")
        self.owner = normalized
        self.target_gross = float(target_gross)
        self.intervention_date = intervention_date
        self._source_owner: str | None = None
        self._applied = False
        self._provenance: dict[str, Any] | None = None

    @property
    def applied(self) -> bool:
        return self._applied

    @property
    def provenance(self) -> dict[str, Any] | None:
        """Return the immutable one-shot audit even if later replay work fails."""

        return None if self._provenance is None else dict(self._provenance)

    def apply(self, account: AccountState) -> dict[str, Any]:
        """Rewrite one owner all-or-nothing, then validate the full account codec."""

        if self._applied:
            raise RuntimeError("strategic owner intervention is one-shot")
        before = economic_state_sha256(account)
        source_owners = tuple(dict.fromkeys(account.strategic_cohort_symbols))
        if len(source_owners) > 1:
            raise ValueError("mixed strategic owner intervention is forbidden")
        source_owner = source_owners[0] if source_owners else None
        self._source_owner = source_owner
        shadow = deepcopy(account)
        if source_owner is None:
            # The production decision for this close has not yet created its
            # grant or epoch.  Pre-seeding capital containers would be orphan
            # authority and can change risk/qualification state.  The
            # post-decision hook below rewrites the fully formed production
            # identity chain atomically when the requested owner differs.
            pass
        elif source_owner == self.owner:
            shadow.strategic_cohort_targets[self.owner] = self.target_gross
        else:
            session = self.intervention_date or shadow.last_successful_run or "2023-01-04"
            forced_industry = default_ai_universe().industry_of(self.owner, session)
            if forced_industry == "unknown":
                raise ValueError("forced owner has no point-in-time industry membership")
            grant_id = _rewrite_grant(
                shadow,
                old=source_owner,
                new=self.owner,
                target_weight=self.target_gross,
                session=session,
            )
            epoch_id = _replace_counterfactual_epoch(
                shadow,
                old=source_owner,
                new=self.owner,
                grant_id=grant_id,
                session=session,
                target_weight=self.target_gross,
            )
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
            _rewrite_account_identity_chain(
                shadow,
                old=source_owner,
                new=self.owner,
                forced_industry=forced_industry,
                grant_id=grant_id,
                epoch_id=epoch_id,
            )
            shadow.strategic_cohort_targets[self.owner] = self.target_gross
        try:
            account_from_dict(shadow.to_dict(), require_hashes=False)
        except RuntimeError as exc:
            raise ValueError("strategic owner rewrite violates account invariants") from exc
        for field in fields(AccountState):
            setattr(account, field.name, getattr(shadow, field.name))
        self._applied = True
        evidence = {
            "applied": True,
            "source_owner": source_owner,
            "forced_owner": self.owner,
            "target_gross": self.target_gross,
            "before_account_sha256": before,
            "after_account_sha256": economic_state_sha256(account),
        }
        self._provenance = evidence
        return evidence

    def preserve_activation(self, account: AccountState, decision: Decision) -> Decision:
        """Research-only activation boundary; preserve the forced owner into next-open execution."""

        strategic = tuple(
            target
            for target in decision.targets
            if target.origin_subsystem == "STRATEGIC" and target.mechanism == "STRATEGIC_COHORT"
        )
        source_owners = tuple(dict.fromkeys(account.strategic_cohort_symbols))
        if source_owners == (self.owner,) and not strategic:
            grant = account.strategic_grant
            durable_forced_owner = bool(
                grant is not None
                and grant.candidate_symbol == self.owner
                and grant.status == StrategicGrantStatus.ACTIVE.value
            )
            if self._source_owner == self.owner or durable_forced_owner:
                return decision
        if len(strategic) != 1:
            raise ValueError("forced owner activation requires exactly one production strategic target")
        original = strategic[0]
        if original.symbol == self.owner:
            return decision
        shadow = deepcopy(account)
        source_owner = original.symbol
        forced_industry = default_ai_universe().industry_of(self.owner, decision.date)
        if forced_industry == "unknown":
            raise ValueError("forced owner has no point-in-time industry membership")
        grant_id = _rewrite_grant(
            shadow,
            old=source_owner,
            new=self.owner,
            target_weight=self.target_gross,
            session=decision.date,
        )
        epoch_id = _replace_counterfactual_epoch(
            shadow,
            old=source_owner,
            new=self.owner,
            grant_id=grant_id,
            session=decision.date,
            target_weight=self.target_gross,
        )
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
        _rewrite_account_identity_chain(
            shadow,
            old=source_owner,
            new=self.owner,
            forced_industry=forced_industry,
            grant_id=grant_id,
            epoch_id=epoch_id,
        )
        account_from_dict(shadow.to_dict(), require_hashes=False)
        for field in fields(AccountState):
            setattr(account, field.name, getattr(shadow, field.name))
        forced_target = replace(
            original,
            symbol=self.owner,
            weight=self.target_gross,
            industry_at_entry=forced_industry,
            industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
            grant_id=grant_id,
            epoch_id=epoch_id,
        )
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
                order,
                symbol=self.owner,
                target_weight=self.target_gross,
                event_id=forced_target.event_id,
                industry_at_entry=forced_industry,
                industry_manifest_sha256=REQUIRED_AI_UNIVERSE_SHA256,
                grant_id=grant_id,
                epoch_id=epoch_id,
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
