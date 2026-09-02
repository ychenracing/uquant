"""Strategic grant construction and owner-capital activation."""

from __future__ import annotations

import hashlib
from typing import Protocol

import pandas as pd

from ...config import SystemConfig, config_fingerprint
from ...models.strategic_epoch import (
    StrategicEpoch,
    StrategicEpochStatus,
    derive_strategic_epoch_id,
    validate_strategic_epoch,
)
from ...models.strategic_grant import (
    StrategicGrantIntent,
    StrategicGrantStatus,
    derive_strategic_grant_id,
)
from ...types import AccountState, LeaderScore, RiskAssessment
from .qualification_candidates import QualifiedStrategicRoute
from .quorum import StrategicQuorumRoute
from .rearm import (
    consume_strategic_cash_rearm_authorization,
    strategic_cash_rearm_weight,
)


class StrategicOwnershipPolicy(Protocol):
    """Allocator surface required to construct one strategic owner."""

    cfg: SystemConfig

    def _release_recovery_anchor(self, account: AccountState) -> None: ...


def _strategic_target_weights(
    self: StrategicOwnershipPolicy,
    *,
    symbols: list[str],
    weighted_symbols: list[str],
    dominant_symbol: str | None,
    owner_symbol: str,
    quorum_route: str,
    restricted_initial_weight: float | None,
) -> dict[str, float]:
    if quorum_route in {
        StrategicQuorumRoute.STRONG_PAIR.value,
        StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
    }:
        return {
            owner_symbol: max(0.0, restricted_initial_weight or 0.0),
        }
    if dominant_symbol is not None:
        return {dominant_symbol: self.cfg.strategic_dominant_max_weight}
    if len(symbols) == 1:
        return {
            weighted_symbols[0]: min(
                self.cfg.max_symbol_weight,
                self.cfg.strategic_one_name_gross,
            )
        }
    if len(symbols) == 2:
        cohort_gross = min(self.cfg.max_gross, self.cfg.strategic_two_name_gross)
        lead_weight = min(self.cfg.max_symbol_weight, 0.60 * cohort_gross)
        return {
            weighted_symbols[0]: lead_weight,
            weighted_symbols[1]: max(0.0, cohort_gross - lead_weight),
        }
    weight = min(self.cfg.max_symbol_weight, self.cfg.max_gross / len(symbols))
    return {symbol: weight for symbol in weighted_symbols}


def activate_strategic_cohort(
    self: StrategicOwnershipPolicy,
    *,
    qualified: QualifiedStrategicRoute,
    snapshots: dict[str, dict[str, float]],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    date: pd.Timestamp,
    risk: RiskAssessment,
) -> None:
    """Create one immutable grant and unfilled epoch after qualification."""

    prepared, dominant_symbol = _prepare_strategic_owner_targets(
        self,
        qualified=qualified,
        leaders=leaders,
        account=account,
        risk=risk,
    )
    if not prepared:
        return
    _initialize_strategic_owner_lifecycle(
        self,
        qualified=qualified,
        snapshots=snapshots,
        account=account,
        dominant_symbol=dominant_symbol,
    )
    grant = _create_strategic_owner_grant(
        account,
        qualified=qualified,
        date=date,
    )
    epoch = _create_strategic_owner_epoch(
        self,
        account=account,
        qualified=qualified,
        grant=grant,
        date=date,
    )
    grant.epoch_id = epoch.epoch_id
    account.strategic_grant = grant
    account.strategic_epochs.append(epoch)
    if grant.authorization_id:
        consume_strategic_cash_rearm_authorization(
            account,
            grant_id=grant.grant_id,
        )


def _prepare_strategic_owner_targets(
    self: StrategicOwnershipPolicy,
    *,
    qualified: QualifiedStrategicRoute,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
) -> tuple[bool, str | None]:
    live_anchors = {
        symbol
        for symbol in account.anchor_weights
        if account.positions.get(symbol) is not None
        and account.positions[symbol].shares > 0
    }
    locked_recovery = bool(
        live_anchors
        and account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
    )
    live_qualified_positions = {
        symbol
        for symbol in qualified.symbols
        if account.positions.get(symbol) is not None
        and account.positions[symbol].shares > 0
    }
    if locked_recovery or live_anchors & set(qualified.symbols) or live_qualified_positions:
        account.candidate_tenure["strategic_deferred_to_recovery"] = 1
        return False, None
    self._release_recovery_anchor(account)
    account.tactical_anchor_symbol = ""
    account.candidate_tenure["tactical_active"] = 0
    account.candidate_tenure["tactical_promotable"] = 0
    account.candidate_tenure["strategic_deferred_to_recovery"] = 0
    account.candidate_tenure["strategic_cohort_evaluated"] = 1
    weighted_symbols = sorted(
        qualified.symbols,
        key=lambda symbol: (-leaders[symbol].score, symbol),
    )
    dominant_symbol = (
        qualified.decisive_reversal_symbol
        if qualified.route == "reversal_industry" and len(weighted_symbols) == 2
        else None
    )
    restricted_owner = qualified.quorum_route in {
        StrategicQuorumRoute.STRONG_PAIR.value,
        StrategicQuorumRoute.ABSOLUTE_SINGLE.value,
    } or qualified.cash_rearm_authorized
    account.strategic_cohort_symbols = (
        [account.strategic_qualification.candidate_symbol]
        if restricted_owner
        else [dominant_symbol]
        if dominant_symbol is not None
        else list(weighted_symbols)
    )
    account.strategic_cohort_targets = _strategic_target_weights(
        self,
        symbols=qualified.symbols,
        weighted_symbols=weighted_symbols,
        dominant_symbol=dominant_symbol,
        owner_symbol=account.strategic_qualification.candidate_symbol,
        quorum_route=qualified.quorum_route,
        restricted_initial_weight=qualified.restricted_initial_weight,
    )
    if qualified.cash_rearm_authorized:
        account.strategic_cohort_targets = {
            account.strategic_qualification.candidate_symbol: strategic_cash_rearm_weight(
                account=account,
                risk=risk,
                cfg=self.cfg,
            )
        }
    return True, dominant_symbol


def _initialize_strategic_owner_lifecycle(
    self: StrategicOwnershipPolicy,
    *,
    qualified: QualifiedStrategicRoute,
    snapshots: dict[str, dict[str, float]],
    account: AccountState,
    dominant_symbol: str | None,
) -> None:
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_external_risk_epoch"] = 0
    account.candidate_tenure["strategic_cohort_active"] = 1
    account.candidate_tenure["strategic_cohort_completed"] = 0
    account.candidate_tenure["strategic_cohort_started"] = 0
    account.candidate_tenure["strategic_cohort_days"] = 0
    account.candidate_tenure["strategic_profit_armed"] = 0
    account.candidate_tenure["strategic_tail_armed"] = 1
    pending_epoch_number = account.strategic_epoch + 1
    account.candidate_tenure["strategic_early_cycle_epoch"] = (
        pending_epoch_number
        if qualified.symbols
        and all(
            snapshots[symbol]["persistent_ret240"]
            >= self.cfg.strategic_cohort_min_ret240
            and snapshots[symbol]["ret120"] < 0.0
            for symbol in qualified.symbols
        )
        else 0
    )
    account.candidate_tenure["strategic_dominant_epoch"] = (
        pending_epoch_number if dominant_symbol is not None else 0
    )
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = 0
    account.strategic_candidate_signature = qualified.signature


def _create_strategic_owner_grant(
    account: AccountState,
    *,
    qualified: QualifiedStrategicRoute,
    date: pd.Timestamp,
) -> StrategicGrantIntent:
    observation = account.strategic_qualification
    previous_grant_id = (
        account.strategic_grant.grant_id
        if account.strategic_grant is not None and account.strategic_grant.terminal
        else ""
    )
    if not account.account_identity:
        identity_payload = "|".join(
            (
                float(account.initial_cash).hex(),
                account.code_hash or "unbound-production-source",
                observation.qualification_last_observed_session,
            )
        )
        account.account_identity = (
            "account_" + hashlib.sha256(identity_payload.encode()).hexdigest()
        )
    production_source_identity = account.code_hash or "unbound-production-source"
    candidate_weight = account.strategic_cohort_targets.get(
        observation.candidate_symbol, 0.0
    )
    authorization_id = (
        account.strategic_cash_rearm.authorization_id
        if qualified.cash_rearm_authorized
        else ""
    )
    grant_id = derive_strategic_grant_id(
        account_identity=account.account_identity,
        candidate_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_evidence_sha256=observation.qualification_evidence_sha256,
        created_session=str(date.date()),
        previous_grant_id=previous_grant_id,
        production_source_identity=production_source_identity,
        authorization_id=authorization_id,
    )
    return StrategicGrantIntent(
        grant_id=grant_id,
        candidate_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_evidence_sha256=observation.qualification_evidence_sha256,
        created_session=str(date.date()),
        last_eligible_session=str(date.date()),
        target_weight=candidate_weight,
        status=StrategicGrantStatus.QUALIFIED.value,
        previous_grant_id=previous_grant_id,
        account_identity=account.account_identity,
        production_source_identity=production_source_identity,
        qualification_quorum=qualified.quorum_route,
        authorization_id=authorization_id,
    )


def _create_strategic_owner_epoch(
    self: StrategicOwnershipPolicy,
    *,
    account: AccountState,
    qualified: QualifiedStrategicRoute,
    grant: StrategicGrantIntent,
    date: pd.Timestamp,
) -> StrategicEpoch:
    previous_epoch_id = (
        account.strategic_epochs[-1].epoch_id if account.strategic_epochs else ""
    )
    if account.strategic_epochs and not account.strategic_epochs[-1].terminal:
        raise RuntimeError("new strategic grant requires the prior epoch to be terminal")
    observation = account.strategic_qualification
    config_identity = "config:" + config_fingerprint(self.cfg)
    epoch_id = derive_strategic_epoch_id(
        account_identity=account.account_identity,
        owner_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        grant_id=grant.grant_id,
        opened_session=str(date.date()),
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity=config_identity,
        evidence_sha256=observation.qualification_evidence_sha256,
    )
    full_weight = max(
        grant.target_weight,
        min(self.cfg.max_symbol_weight, self.cfg.strategic_one_name_gross),
    )
    epoch = StrategicEpoch(
        epoch_id=epoch_id,
        owner_symbol=observation.candidate_symbol,
        qualification_signature=observation.qualification_signature,
        qualification_route=observation.qualification_route,
        qualification_quorum=qualified.quorum_route,
        grant_id=grant.grant_id,
        opened_session=str(date.date()),
        previous_epoch_id=previous_epoch_id,
        source_identity=grant.production_source_identity,
        config_identity=config_identity,
        evidence_sha256=observation.qualification_evidence_sha256,
        realized_status=StrategicEpochStatus.PROBE.value,
        target_weight=grant.target_weight,
        full_weight=full_weight,
        account_identity=account.account_identity,
    )
    validate_strategic_epoch(epoch)
    return epoch


def release_expired_strategic_deployment(account: AccountState) -> None:
    """Release capital authority after an expired probe is fully settled."""

    account.strategic_cohort_symbols.clear()
    account.strategic_cohort_targets.clear()
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.strategic_restore_epoch_ids.clear()
    account.strategic_candidate_signature = ""
    for key in (
        "strategic_cohort_active",
        "strategic_cohort_completed",
        "strategic_cohort_started",
        "strategic_cohort_days",
        "strategic_profit_armed",
        "strategic_tail_armed",
        "strategic_dominant_epoch",
    ):
        account.candidate_tenure[key] = 0


__all__ = (
    "StrategicOwnershipPolicy",
    "activate_strategic_cohort",
    "release_expired_strategic_deployment",
)
