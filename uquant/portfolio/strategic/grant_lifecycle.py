"""Strategic grant expiry, retry, and causal revalidation."""

from __future__ import annotations

import math
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
from ...models.trading import late_strategic_fill_allowed, strategic_economic_remaining_shares
from ...types import AccountState, LeaderScore, RiskAssessment
from .discovery import (
    _route_confirmation,
    resolve_strategic_qualification_inputs,
    strategic_deployment_block_reason,
    strategic_qualification_evidence,
    strategic_qualification_evidence_sha256,
    strategic_qualification_snapshots,
    strategic_quorum_candidate_symbols,
    strategic_route_admission_open,
)
from .ownership import release_expired_strategic_deployment
from .qualification_candidates import (
    StrategicRoute,
    _reversal_candidates,
    decisive_reversal,
    established_route_durable,
    reset_strategic_qualification_streaks,
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

    def belongs_to_expired_epoch(item: object) -> bool:
        return bool(grant.epoch_id and getattr(item, "epoch_id", "") == grant.epoch_id) or (
            getattr(item, "grant_id", "") == grant.grant_id
        )

    had_pending_execution = any(
        belongs_to_expired_epoch(order) for order in account.pending_orders
    )
    account.pending_orders = [
        order for order in account.pending_orders
        if order.side != "BUY" or not belongs_to_expired_epoch(order)
    ]
    held = {
        symbol: min(weights_now.get(symbol, 0.0), account.strategic_cohort_targets.get(symbol, weights_now.get(symbol, 0.0)))
        for symbol, position in account.positions.items()
        if position.shares > 0 and belongs_to_expired_epoch(position)
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


def _owns_grant_position(account: AccountState, grant: StrategicGrantIntent) -> bool:
    position = account.positions.get(grant.candidate_symbol)
    return bool(position is not None and position.shares > 0
                and position.epoch_id == grant.epoch_id and position.grant_id == grant.grant_id
                and any(fill.shares > 0 and fill.side == "BUY" and fill.symbol == grant.candidate_symbol
                        and fill.epoch_id == grant.epoch_id and fill.grant_id == grant.grant_id
                        for fill in account.fills))


def _completed_core_entry(account: AccountState, grant: StrategicGrantIntent) -> bool:
    """Read executed admission from real ownership and economic order capacity."""
    epoch = next((item for item in account.strategic_epochs if item.epoch_id == grant.epoch_id), None)
    if not (epoch is not None and epoch.realized_status == "CORE" and epoch.first_fill_session
            and epoch.owner_symbol == grant.candidate_symbol and epoch.grant_id == grant.grant_id
            and _owns_grant_position(account, grant)):
        return False
    orders = [order for order in account.order_ledger
              if order.grant_id == grant.grant_id and order.epoch_id == grant.epoch_id
              and order.symbol == grant.candidate_symbol and order.side == "BUY"]
    if not orders or any(order.side == "BUY" and order.grant_id == grant.grant_id
                         for order in account.pending_orders):
        return False
    return all(order.event_id and not late_strategic_fill_allowed(order)
               and strategic_economic_remaining_shares(
                   order=order, orders=account.order_ledger, fills=account.fills) == 0
               for order in orders)


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
    completed_entry = _completed_core_entry(account, grant)
    if not candidate_still_qualified:
        if completed_entry:
            observation = account.strategic_qualification
            observation.qualification_ready = False
            observation.deployment_blocked = True
            observation.deployment_block_reason = "qualification_invalid"
            observation.candidate_invalidation_reason = "candidate_or_route_no_longer_qualified"
            return True
        _expire_strategic_grant(
            account,
            reason="candidate_or_route_no_longer_qualified",
            weights_now=weights_now,
        )
        return False
    if not completed_entry and _grant_retry_window_elapsed(grant=grant, evidence=evidence, candidate_frame=candidate_frame, date=date):
        _expire_strategic_grant(account, reason="qualification_observation_window_elapsed", weights_now=weights_now)
        return False
    if _retain_reference_blocked_grant(account, grant=grant, evidence=evidence, date=date):
        return True
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
        require_confirmation=completed_entry,
    )
    if not completed_entry and not block_reason and account.candidate_tenure.get("strategic_grant_healthy_retry_session", 0) != date.toordinal():
        grant.healthy_retry_sessions += 1
        account.candidate_tenure["strategic_grant_healthy_retry_session"] = date.toordinal()
    return True



def _original_reversal_witnesses(self: StrategicPortfolioPolicy, *, symbols: list[str],
                                 grant: StrategicGrantIntent, snapshots: dict[str, dict[str, float]],
                                 leaders: dict[str, LeaderScore], risk: RiskAssessment,
                                 available_symbols: tuple[str, ...]) -> tuple[list[str], bool]:
    ranked = _reversal_candidates(self, snapshots, leaders)
    owner_industry = leaders[grant.candidate_symbol].industry
    # Identity preserves the admitted members; evidence ordering follows the
    # same current route facts as discovery, never the signature spelling.
    additional = [symbol for symbol in ranked
                  if symbol not in symbols and symbol in available_symbols
                  and leaders[symbol].industry == owner_industry]
    members = set(symbols + additional[:max(0, self.cfg.strategic_cohort_size - len(symbols))])
    witnesses = [symbol for symbol in ranked if symbol in members]
    synchronized = bool(len(witnesses) >= self.cfg.strategic_cohort_min_size
                        and all(leaders[symbol].industry == owner_industry for symbol in witnesses)
                        and float(pd.Series([snapshots[symbol]["ret20"] for symbol in witnesses[:2]]).median())
                        >= self.cfg.strategic_reversal_min_median_ret20
                        and float(risk.evidence.get("tech_ret120", math.inf)) <= self.cfg.strategic_reversal_max_tech_ret120)
    return witnesses, synchronized


def _original_grant_route(
    self: StrategicPortfolioPolicy, *, grant: StrategicGrantIntent,
    snapshots: dict[str, dict[str, float]], leaders: dict[str, LeaderScore], risk: RiskAssessment,
    available_symbols: tuple[str, ...],
) -> tuple[StrategicRoute, bool]:
    """Read admitted membership from immutable identity; live competitors cannot rewrite it."""
    prefix, _, body = grant.qualification_signature.partition(":")
    _state, _, body = body.partition(":")
    members, separator, route_name = body.rpartition(":evidence=")
    symbols = [item.partition(":")[0] for item in members.split(",") if ":" in item]
    identity_valid = bool(prefix == "strategic_qualification" and separator
                          and route_name == grant.qualification_route and grant.candidate_symbol in symbols)
    complete = identity_valid and set(symbols) <= set(available_symbols) and all(
        strategic_candidate_meets_route(candidate_symbol=symbol, qualification_route=grant.qualification_route,
                                        snapshots=snapshots, leaders=leaders, risk=risk, cfg=self.cfg)
        for symbol in symbols
    )
    observed = "risk_anchor_symbols" in risk.evidence
    synchronized = False
    groups: list[list[str]] = []
    decisive = None
    if grant.qualification_route == "reversal_industry" and complete:
        # A concentrated reversal was admitted with its original pair plus a
        # synchronized witness group. Revalidate that pair, never a new winner.
        witnesses, synchronized = _original_reversal_witnesses(
            self, symbols=symbols, grant=grant, snapshots=snapshots, leaders=leaders,
            risk=risk, available_symbols=available_symbols)
        groups = [witnesses] if synchronized else []
        if len(symbols) == 2 and grant.qualification_quorum == StrategicQuorumRoute.FULL_COHORT.value:
            decisive, _pair = decisive_reversal(self, synchronized=synchronized, reversal_groups=groups,
                                                snapshots=snapshots, leaders=leaders, anchor_state_observed=observed)
            complete = decisive == grant.candidate_symbol
        else:
            complete = synchronized
    if grant.qualification_route == "established" and complete:
        complete = established_route_durable(self, symbols=symbols, snapshots=snapshots, leaders=leaders)
    return StrategicRoute(symbols, grant.qualification_route, decisive, synchronized, groups,
                          observed, bool(observed and not risk.evidence.get("risk_anchor_symbols", []))), complete


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
    route, original_complete = _original_grant_route(self, grant=grant, snapshots=snapshots, leaders=leaders,
                                                   risk=risk, available_symbols=universe.available_symbols)
    legacy_raw, synchronized_before_anchor = (
        strategic_qualification_evidence(self, route=route, snapshots=snapshots, leaders=leaders, risk=risk)
        if original_complete else (False, False)
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
        original_complete
        and quorum.qualified
        and quorum.route.value == grant.qualification_quorum
        and (
            quorum.route is not StrategicQuorumRoute.FULL_COHORT
            or legacy_raw
        )
    )
    symbols = route_symbols if raw else []
    return _GrantRouteEvidence(
        snapshots=snapshots,
        route=route,
        quorum=quorum,
        raw=raw,
        symbols=symbols,
        signature=grant.qualification_signature,
        synchronized_before_anchor=synchronized_before_anchor,
    )


def _retain_reference_blocked_grant(
    account: AccountState,
    *,
    grant: StrategicGrantIntent,
    evidence: _GrantRouteEvidence,
    date: pd.Timestamp,
) -> bool:
    if evidence.raw:
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
    require_confirmation: bool,
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
    )
    observation = account.strategic_qualification
    observation.candidate_symbol = grant.candidate_symbol
    if evidence.raw:
        witnesses = list(strategic_quorum_candidate_symbols(route=evidence.route, route_symbols=evidence.symbols))
        observation.qualification_signature = evidence.signature
        observation.qualification_route = evidence.route.route
        observation.qualification_evidence_sha256 = (
            strategic_qualification_evidence_sha256(
                date=date,
                route=evidence.route,
                symbols=witnesses,
                signature=evidence.signature,
                snapshots=evidence.snapshots,
                leaders=leaders,
                risk=risk,
            )
        )
        observation.qualification_quorum = evidence.quorum.route.value
        observation.candidate_symbols = sorted(witnesses)
        observation.unavailable_reference_symbols = list(
            evidence.quorum.unavailable_references
        )
        grant.last_eligible_session = str(date.date())
    observation.qualification_ready = not require_confirmation or _route_confirmation(
        account=account, candidate=grant.candidate_symbol, route=grant.qualification_route,
        quorum=evidence.quorum,
    ) >= evidence.quorum.required_confirm_days
    if not observation.qualification_ready:
        block_reason = block_reason or "reference_coverage_or_confirmation"
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
    if grant.healthy_retry_sessions >= MAX_STRATEGIC_GRANT_HEALTHY_RETRY_SESSIONS:
        return True
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
