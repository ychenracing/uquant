"""Strategic grant expiry, retry, and causal revalidation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from ...models.strategic_epoch import settle_account_strategic_epoch
from ...models.strategic_grant import (
    MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS,
    StrategicGrantIntent,
    StrategicGrantStatus,
)
from ...models.strategic_universe import StrategicUniverseRoles
from ...types import AccountState, LeaderScore, RiskAssessment
from .discovery import (
    resolve_strategic_qualification_inputs,
    strategic_deployment_block_reason,
    strategic_qualification_evidence,
    strategic_qualification_evidence_sha256,
    strategic_qualification_snapshots,
    strategic_quorum_candidate_symbols,
    strategic_route_admission_open,
    strategic_route_signature,
)
from .ownership import release_expired_strategic_deployment
from .qualification_candidates import (
    StrategicRoute,
    reset_strategic_qualification_streaks,
    select_strategic_route,
    strategic_candidate_meets_route,
)
from .quorum import (
    StrategicQuorumResult,
    StrategicQuorumRoute,
    evaluate_strategic_quorum,
)

if TYPE_CHECKING:
    from .discovery import StrategicPortfolioPolicy


@dataclass(frozen=True, slots=True)
class _GrantRouteEvidence:
    snapshots: dict[str, dict[str, float]]
    route: StrategicRoute
    quorum: StrategicQuorumResult
    raw: bool
    symbols: list[str]
    signature: str
    synchronized_before_anchor: bool


def _expire_strategic_grant(
    account: AccountState,
    *,
    reason: str,
    weights_now: dict[str, float],
) -> None:
    grant = account.strategic_grant
    if grant is None or grant.terminal:
        return
    grant.status = StrategicGrantStatus.EXPIRED.value
    grant.expiry_reason = reason
    had_pending_execution = any(
        order.grant_id == grant.grant_id for order in account.pending_orders
    )
    account.pending_orders = [
        order for order in account.pending_orders if order.grant_id != grant.grant_id
    ]
    held = {
        symbol: 0.0
        for symbol, position in account.positions.items()
        if position.shares > 0 and position.grant_id == grant.grant_id
    }
    account.strategic_cohort_symbols = sorted(held)
    account.strategic_cohort_targets = dict(held)
    account.candidate_tenure["strategic_cohort_active"] = int(bool(held))
    account.strategic_restore_weights.clear()
    reset_strategic_qualification_streaks(account)
    account.candidate_tenure["strategic_cohort_qualification"] = 0
    observation = account.strategic_qualification
    observation.qualification_ready = False
    observation.deployment_blocked = True
    observation.deployment_block_reason = "qualification_invalid"
    observation.qualification_streak = 0
    observation.candidate_invalidation_reason = reason
    if not held and not had_pending_execution and grant.epoch_id:
        settled = settle_account_strategic_epoch(
            account,
            epoch_id=grant.epoch_id,
            closed_session=(
                observation.qualification_last_observed_session
                or grant.last_eligible_session
            ),
            close_reason=reason,
            expired=True,
        )
        if settled:
            release_expired_strategic_deployment(account)


def revalidate_strategic_grant(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    weights_now: dict[str, float],
    qualification_panel: dict[str, pd.DataFrame] | None = None,
    qualification_leaders: dict[str, LeaderScore] | None = None,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> bool:
    """Reconfirm a not-yet-active grant before every capital retry."""

    resolved_panel, resolved_leaders, resolved_universe = (
        resolve_strategic_qualification_inputs(
            date=date,
            user_panel=user_panel,
            leaders=leaders,
            qualification_panel=qualification_panel,
            qualification_leaders=qualification_leaders,
            strategic_universe=strategic_universe,
        )
    )
    grant = account.strategic_grant
    if grant is None or grant.terminal or grant.status in {
        StrategicGrantStatus.ACTIVE.value,
        StrategicGrantStatus.COMPLETED.value,
    }:
        return True
    if grant.candidate_symbol not in user_panel:
        _expire_strategic_grant(
            account,
            reason="candidate_removed_from_allowed_universe",
            weights_now=weights_now,
        )
        return False
    candidate_frame = user_panel[grant.candidate_symbol]
    if date not in candidate_frame.index:
        account.strategic_qualification.deployment_blocked = True
        account.strategic_qualification.deployment_block_reason = (
            "candidate_not_tradable"
        )
        return True
    evidence = _grant_route_evidence(
        self,
        grant=grant,
        date=date,
        user_panel=user_panel,
        resolved_panel=resolved_panel,
        leaders=resolved_leaders,
        risk=risk,
        universe=resolved_universe,
    )
    candidate_still_qualified = strategic_candidate_meets_route(
        candidate_symbol=grant.candidate_symbol,
        qualification_route=grant.qualification_route,
        snapshots=evidence.snapshots,
        leaders=resolved_leaders,
        risk=risk,
        cfg=self.cfg,
    )
    if not candidate_still_qualified:
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
    if _retain_reference_blocked_grant(
        account,
        grant=grant,
        evidence=evidence,
        date=date,
    ):
        return True
    if evidence.raw and (
        grant.candidate_symbol not in evidence.symbols
        or evidence.route.route != grant.qualification_route
    ):
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
    block_reason = _update_revalidated_grant(
        self,
        date=date,
        user_panel=user_panel,
        account=account,
        risk=risk,
        admission_open=admission_open,
        grant=grant,
        evidence=evidence,
        leaders=resolved_leaders,
    )
    if _grant_retry_window_elapsed(
        grant=grant,
        evidence=evidence,
        candidate_frame=candidate_frame,
        date=date,
    ):
        _expire_strategic_grant(
            account,
            reason="qualification_observation_window_elapsed",
            weights_now=weights_now,
        )
        return False
    if not block_reason:
        grant.healthy_retry_sessions = min(
            MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS,
            grant.healthy_retry_sessions + 1,
        )
    return True


def _grant_route_evidence(
    self: StrategicPortfolioPolicy,
    *,
    grant: StrategicGrantIntent,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    resolved_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    risk: RiskAssessment,
    universe: StrategicUniverseRoles,
) -> _GrantRouteEvidence:
    reference_snapshots = strategic_qualification_snapshots(
        self,
        date=date,
        user_panel=resolved_panel,
        leaders=leaders,
    )
    snapshots = {
        symbol: values
        for symbol, values in reference_snapshots.items()
        if symbol in user_panel and symbol in leaders
    }
    route = select_strategic_route(
        self,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    legacy_raw, synchronized_before_anchor = strategic_qualification_evidence(
        self,
        route=route,
        snapshots=snapshots,
        leaders=leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    quorum = evaluate_strategic_quorum(
        owner_symbol=grant.candidate_symbol,
        candidate_symbols=strategic_quorum_candidate_symbols(
            route=route,
            route_symbols=route_symbols,
        ),
        snapshots=reference_snapshots,
        leaders=leaders,
        risk=risk,
        universe=universe,
        cfg=self.cfg,
        synchronized_full_cohort=legacy_raw,
    )
    raw = bool(
        quorum.qualified
        and (
            quorum.route is not StrategicQuorumRoute.FULL_COHORT
            or legacy_raw
        )
    )
    symbols = route_symbols if raw else []
    _admission_state, signature = strategic_route_signature(
        route=route,
        symbols=symbols,
        leaders=leaders,
    )
    return _GrantRouteEvidence(
        snapshots=snapshots,
        route=route,
        quorum=quorum,
        raw=raw,
        symbols=symbols,
        signature=signature,
        synchronized_before_anchor=synchronized_before_anchor,
    )


def _retain_reference_blocked_grant(
    account: AccountState,
    *,
    grant: StrategicGrantIntent,
    evidence: _GrantRouteEvidence,
    date: pd.Timestamp,
) -> bool:
    if evidence.raw or not evidence.quorum.owner_absolute_quality:
        return False
    observation = account.strategic_qualification
    observation.qualification_ready = True
    observation.deployment_blocked = True
    observation.deployment_block_reason = "reference_coverage_or_confirmation"
    observation.qualification_last_observed_session = str(date.date())
    observation.qualification_quorum = grant.qualification_quorum
    observation.unavailable_reference_symbols = list(
        evidence.quorum.unavailable_references
    )
    return True


def _update_revalidated_grant(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool,
    grant: StrategicGrantIntent,
    evidence: _GrantRouteEvidence,
    leaders: dict[str, LeaderScore],
) -> str:
    route_admission_open = bool(
        admission_open
        if not evidence.raw
        else strategic_route_admission_open(
            self,
            route=evidence.route,
            symbols=evidence.symbols,
            snapshots=evidence.snapshots,
            admission_open=admission_open,
            synchronized_before_anchor=evidence.synchronized_before_anchor,
        )
    )
    block_reason = strategic_deployment_block_reason(
        self,
        date=date,
        user_panel=user_panel,
        account=account,
        risk=risk,
        admission_open=route_admission_open,
        live_general_leaders=set(),
    )
    observation = account.strategic_qualification
    observation.candidate_symbol = grant.candidate_symbol
    if evidence.raw:
        observation.qualification_signature = evidence.signature
        observation.qualification_route = evidence.route.route
        observation.qualification_evidence_sha256 = (
            strategic_qualification_evidence_sha256(
                date=date,
                route=evidence.route,
                symbols=evidence.symbols,
                signature=evidence.signature,
                snapshots=evidence.snapshots,
                leaders=leaders,
                risk=risk,
            )
        )
        observation.qualification_quorum = evidence.quorum.route.value
        observation.candidate_symbols = sorted(evidence.symbols)
        observation.unavailable_reference_symbols = list(
            evidence.quorum.unavailable_references
        )
        grant.last_eligible_session = str(date.date())
    observation.qualification_ready = True
    observation.deployment_blocked = bool(block_reason)
    observation.deployment_block_reason = block_reason
    observation.qualification_last_observed_session = str(date.date())
    observation.candidate_invalidation_reason = ""
    return block_reason


def _grant_retry_window_elapsed(
    *,
    grant: StrategicGrantIntent,
    evidence: _GrantRouteEvidence,
    candidate_frame: pd.DataFrame,
    date: pd.Timestamp,
) -> bool:
    if evidence.raw:
        return False
    visible_since_route = sum(
        1
        for session in candidate_frame.index
        if pd.Timestamp(grant.last_eligible_session) < session <= date
    )
    return bool(
        visible_since_route > MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
        or grant.healthy_retry_sessions
        >= MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS
    )


__all__ = ("revalidate_strategic_grant",)
