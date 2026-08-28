"""Read-only strategic successor observation under an active owner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from ...models.strategic_epoch import StrategicEpoch
from ...models.strategic_grant import StrategicQualificationObservation
from ...models.strategic_universe import (
    StrategicUniverseRoles,
    build_strategic_universe_roles,
)
from ...types import AccountState, LeaderScore, RiskAssessment
from .discovery import (
    strategic_candidate_symbol,
    strategic_qualification_evidence,
    strategic_qualification_evidence_sha256,
    strategic_qualification_snapshots,
    strategic_quorum_candidate_symbols,
    strategic_route_signature,
)
from .qualification_candidates import StrategicRoute, select_strategic_route
from .quorum import (
    StrategicQuorumResult,
    StrategicQuorumRoute,
    evaluate_strategic_quorum,
)

if TYPE_CHECKING:
    from .discovery import StrategicPortfolioPolicy


@dataclass(frozen=True, slots=True)
class _SuccessorEvidence:
    reference_snapshots: dict[str, dict[str, float]]
    candidate_snapshots: dict[str, dict[str, float]]
    route: StrategicRoute
    candidate: str
    quorum: StrategicQuorumResult | None


def observe_strategic_successor(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    qualification_panel: dict[str, pd.DataFrame],
    qualification_leaders: dict[str, LeaderScore],
    tradable_symbols: set[str],
    account: AccountState,
    risk: RiskAssessment,
    strategic_universe: StrategicUniverseRoles | None = None,
) -> None:
    """Persist qualification streaks while an incumbent keeps all capital rights."""

    active_epoch = _active_strategic_epoch(account)
    if active_epoch is None:
        return
    evidence = _successor_evidence(
        self,
        date=date,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        eligible_symbols=tradable_symbols - {active_epoch.owner_symbol},
        tradable_symbols=tradable_symbols,
        risk=risk,
        strategic_universe=strategic_universe,
    )
    route_symbols = list(evidence.route.symbols)
    symbols = (
        route_symbols
        if evidence.quorum is not None and evidence.quorum.qualified
        else []
    )
    if not symbols:
        _record_unqualified_successor(
            date=date,
            evidence=evidence,
            route_symbols=route_symbols,
            qualification_leaders=qualification_leaders,
            account=account,
            risk=risk,
        )
        return
    _record_qualified_successor(
        date=date,
        evidence=evidence,
        symbols=symbols,
        qualification_leaders=qualification_leaders,
        account=account,
        risk=risk,
    )


def _active_strategic_epoch(account: AccountState) -> StrategicEpoch | None:
    return next(
        (
            epoch
            for epoch in account.strategic_epochs
            if epoch.epoch_id == account.active_strategic_epoch_id
            and epoch.active
        ),
        None,
    )


def _successor_evidence(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    qualification_panel: dict[str, pd.DataFrame],
    qualification_leaders: dict[str, LeaderScore],
    eligible_symbols: set[str],
    tradable_symbols: set[str],
    risk: RiskAssessment,
    strategic_universe: StrategicUniverseRoles | None,
) -> _SuccessorEvidence:
    reference_snapshots = strategic_qualification_snapshots(
        self,
        date=date,
        user_panel=qualification_panel,
        leaders=qualification_leaders,
    )
    candidate_snapshots = {
        symbol: snapshot
        for symbol, snapshot in reference_snapshots.items()
        if symbol in eligible_symbols and symbol in qualification_leaders
    }
    route = select_strategic_route(
        self,
        snapshots=candidate_snapshots,
        leaders={
            symbol: qualification_leaders[symbol]
            for symbol in candidate_snapshots
        },
        risk=risk,
    )
    legacy_raw, _synchronized = strategic_qualification_evidence(
        self,
        route=route,
        snapshots=candidate_snapshots,
        leaders=qualification_leaders,
        risk=risk,
    )
    route_symbols = list(route.symbols)
    candidate = (
        strategic_candidate_symbol(
            route=route,
            symbols=route_symbols,
            leaders=qualification_leaders,
        )
        if route_symbols
        else ""
    )
    universe = strategic_universe or _successor_universe(
        date=date,
        qualification_panel=qualification_panel,
        qualification_leaders=qualification_leaders,
        tradable_symbols=tradable_symbols,
    )
    quorum = (
        evaluate_strategic_quorum(
            owner_symbol=candidate,
            candidate_symbols=strategic_quorum_candidate_symbols(
                route=route,
                route_symbols=route_symbols,
            ),
            snapshots=reference_snapshots,
            leaders=qualification_leaders,
            risk=risk,
            universe=universe,
            cfg=self.cfg,
            synchronized_full_cohort=legacy_raw,
        )
        if candidate
        else None
    )
    return _SuccessorEvidence(
        reference_snapshots=reference_snapshots,
        candidate_snapshots=candidate_snapshots,
        route=route,
        candidate=candidate,
        quorum=quorum,
    )


def _successor_universe(
    *,
    date: pd.Timestamp,
    qualification_panel: dict[str, pd.DataFrame],
    qualification_leaders: dict[str, LeaderScore],
    tradable_symbols: set[str],
) -> StrategicUniverseRoles:
    return build_strategic_universe_roles(
        as_of=str(date.date()),
        tradable_symbols=tradable_symbols,
        qualification_reference_symbols=qualification_panel,
        risk_reference_symbols=(),
        industries={
            symbol: qualification_leaders[symbol].industry
            for symbol in qualification_panel
            if symbol in qualification_leaders
        },
        available_symbols=qualification_panel,
    )


def _record_unqualified_successor(
    *,
    date: pd.Timestamp,
    evidence: _SuccessorEvidence,
    route_symbols: list[str],
    qualification_leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
) -> None:
    previous = account.strategic_successor_qualification
    _admission_state, attempted_signature = strategic_route_signature(
        route=evidence.route,
        symbols=route_symbols,
        leaders=qualification_leaders,
    )
    owner_quality_retained = bool(
        evidence.quorum is not None
        and evidence.quorum.owner_absolute_quality
        and evidence.candidate
    )
    if not owner_quality_retained and not previous.candidate_symbol:
        account.strategic_successor_qualification = (
            StrategicQualificationObservation()
        )
        return
    account.strategic_successor_qualification = StrategicQualificationObservation(
        candidate_symbol=(
            evidence.candidate
            if owner_quality_retained
            else previous.candidate_symbol
        ),
        qualification_signature=(
            attempted_signature
            if owner_quality_retained
            else previous.qualification_signature
        ),
        qualification_route=(
            evidence.route.route
            if owner_quality_retained
            else previous.qualification_route
        ),
        qualification_evidence_sha256=(
            strategic_qualification_evidence_sha256(
                date=date,
                route=evidence.route,
                symbols=route_symbols,
                signature=attempted_signature,
                snapshots=evidence.candidate_snapshots,
                leaders=qualification_leaders,
                risk=risk,
            )
            if owner_quality_retained
            else previous.qualification_evidence_sha256
        ),
        qualification_ready=False,
        deployment_blocked=True,
        deployment_block_reason="active_epoch_read_only",
        qualification_streak=(
            previous.qualification_streak
            if owner_quality_retained
            and previous.candidate_symbol == evidence.candidate
            and previous.qualification_signature == attempted_signature
            else 0
        ),
        qualification_last_observed_session=str(date.date()),
        candidate_invalidation_reason=(
            "successor_reference_coverage_or_confirmation"
            if owner_quality_retained
            else "successor_qualification_not_ready"
        ),
        qualification_quorum=(
            evidence.quorum.route.value
            if evidence.quorum is not None
            else StrategicQuorumRoute.NONE.value
        ),
        candidate_symbols=sorted(route_symbols),
        unavailable_reference_symbols=(
            list(evidence.quorum.unavailable_references)
            if evidence.quorum is not None
            else []
        ),
    )


def _record_qualified_successor(
    *,
    date: pd.Timestamp,
    evidence: _SuccessorEvidence,
    symbols: list[str],
    qualification_leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
) -> None:
    quorum = evidence.quorum
    if quorum is None:
        raise RuntimeError("qualified successor requires quorum evidence")
    _admission_state, signature = strategic_route_signature(
        route=evidence.route,
        symbols=symbols,
        leaders=qualification_leaders,
    )
    streak_key = f"strategic_successor:{signature}"
    for key in tuple(account.replacement_tenure):
        if key.startswith("strategic_successor:") and key != streak_key:
            account.replacement_tenure[key] = 0
    streak = account.replacement_tenure.get(streak_key, 0) + 1
    account.replacement_tenure[streak_key] = streak
    account.strategic_successor_qualification = StrategicQualificationObservation(
        candidate_symbol=evidence.candidate,
        qualification_signature=signature,
        qualification_route=evidence.route.route,
        qualification_evidence_sha256=strategic_qualification_evidence_sha256(
            date=date,
            route=evidence.route,
            symbols=symbols,
            signature=signature,
            snapshots=evidence.candidate_snapshots,
            leaders=qualification_leaders,
            risk=risk,
        ),
        qualification_ready=streak >= quorum.required_confirm_days,
        deployment_blocked=True,
        deployment_block_reason="active_epoch_read_only",
        qualification_streak=streak,
        qualification_last_observed_session=str(date.date()),
        qualification_quorum=quorum.route.value,
        candidate_symbols=sorted(symbols),
        unavailable_reference_symbols=list(quorum.unavailable_references),
        evidence_family_status={
            "INDUSTRY_CONFIRMATION": (
                "CONFIRMED" if quorum.industry_confirmation else "FAILED"
            ),
            "MARKET_CONFIRMATION": (
                "CONFIRMED" if quorum.market_confirmation else "FAILED"
            ),
            "OWNER_ABSOLUTE_QUALITY": (
                "CONFIRMED" if quorum.owner_absolute_quality else "FAILED"
            ),
            "ROBUSTNESS_CONFIRMATION": (
                "CONFIRMED" if quorum.robustness_confirmation else "DEGRADED"
            ),
        },
    )


__all__ = ("observe_strategic_successor",)
