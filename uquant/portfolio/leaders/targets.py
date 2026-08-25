"""Mechanical Task 8 leader owner extracted from the immutable policy."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ...features import scalar
from ...types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    RiskAssessment,
    Target,
)

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy

from .extensions import apply_leader_extensions


@dataclass(slots=True)
class _LeaderTargetContext:
    policy: LeaderPortfolioPolicy
    date: pd.Timestamp
    opportunity: Opportunity
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    weights_now: dict[str, float]
    prices: dict[str, float]
    ranked: list[LeaderScore]
    emerging: list[LeaderScore]
    held_symbols: set[str]
    target_k: int
    active: list[str]
    reasons: dict[str, str] = field(default_factory=dict)
    lifecycles: dict[str, Lifecycle] = field(default_factory=dict)
    mechanisms: dict[str, AttributionMechanism] = field(default_factory=dict)
    replaces_symbols: dict[str, str] = field(default_factory=dict)
    rotation_transfers: dict[str, float] = field(default_factory=dict)
    proposed: dict[str, float] = field(default_factory=dict)
    new_core: list[str] = field(default_factory=list)
    gross_cap: float = 0.0
    projected_industry_cap: float = 0.0
    satellite_reserve: float = 0.0
    index_chase: bool = False


@dataclass(frozen=True, slots=True)
class _RotationEvidence:
    challenger: LeaderScore
    weakest: str
    held_sessions: int
    edge: float
    industry_handoff: bool
    key: str


def _cap_opportunity_gross(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    gross_cap: float,
    weights_now: dict[str, float],
    leaders: dict[str, LeaderScore],
    reasons: dict[str, str],
    opportunity: Opportunity,
) -> dict[str, float]:
    """Limit new opportunity risk without manufacturing incumbent sells.

    CHOPPY/WEAK are alpha-budget observations. Confirmed structural risk
    overlays own forced reductions. This distinction gives the continuous
    opportunity axis an economic hysteresis band: existing exposure may
    drift above the entry budget, while only proposed increments are
    sparsely removed.
    """
    capped = dict(proposed)
    increments = {
        symbol: max(0.0, weight - max(0.0, weights_now.get(symbol, 0.0))) for symbol, weight in capped.items()
    }
    baseline_total = sum(capped.values()) - sum(increments.values())
    allowed_total = max(gross_cap, baseline_total)
    excess = max(0.0, sum(max(0.0, value) for value in capped.values()) - allowed_total)
    if excess <= 1e-12:
        return capped
    symbols = tuple(sorted(symbol for symbol, weight in increments.items() if weight > 1e-12))
    feasible = [
        subset
        for size in range(1, len(symbols) + 1)
        for subset in combinations(symbols, size)
        if sum(increments[symbol] for symbol in subset) >= excess - 1e-12
    ]
    selected = min(
        feasible,
        key=lambda subset: (
            len(subset),
            sum(leaders[symbol].score if symbol in leaders else 0.0 for symbol in subset),
            -sum(increments[symbol] for symbol in subset),
            subset,
        ),
    )
    remaining = excess
    for symbol in sorted(
        selected,
        key=lambda item: (
            leaders[item].score if item in leaders else 0.0,
            -increments[item],
            item,
        ),
    ):
        reduction = min(increments[symbol], remaining)
        if reduction <= 1e-12:
            continue
        capped[symbol] = max(0.0, capped[symbol] - reduction)
        reasons[symbol] = f"{opportunity.value.lower()} opportunity gross contraction"
        remaining -= reduction
    if remaining > 1e-8:
        raise RuntimeError("leader opportunity cap could not be reconciled")
    return capped


def _leader_target_context(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    prices: dict[str, float],
) -> _LeaderTargetContext:
    ranked = sorted(
        (
            item
            for item in leaders.values()
            if item.mature
            and item.confidence >= self.cfg.leader_min_confidence
            and self._structure_ok(user_panel[item.symbol], date)
        ),
        key=lambda item: (-item.score, item.symbol),
    )
    emerging = sorted(
        (
            item
            for item in leaders.values()
            if item.emerging and self._structure_ok(user_panel[item.symbol], date)
        ),
        key=lambda item: (-item.score, item.symbol),
    )
    held = {symbol for symbol, position in account.positions.items() if position.shares > 0}
    target_k = self._dynamic_k(
        date=date,
        opportunity=opportunity,
        risk=risk,
        candidates=ranked,
        user_panel=user_panel,
        account=account,
    )
    active = [symbol for symbol in account.active_leaders if symbol in held and symbol in leaders]
    return _LeaderTargetContext(
        self,
        date,
        opportunity,
        risk,
        user_panel,
        leaders,
        account,
        weights_now,
        prices,
        ranked,
        emerging,
        held,
        target_k,
        active,
    )


def _restore_active_leaders(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    for symbol in sorted(ctx.held_symbols - set(ctx.active)):
        position = ctx.account.positions[symbol]
        frame = ctx.user_panel.get(symbol)
        if frame is None or ctx.date not in frame.index or symbol not in ctx.leaders:
            continue
        row = frame.loc[ctx.date]
        proven_winner = (
            position.highest_close / max(position.avg_cost, 1e-12) - 1.0 >= 0.10
            and ctx.prices[symbol] >= position.avg_cost
            and scalar(row, "close") >= scalar(row, f"ma{self.cfg.trend_medium}")
        )
        if proven_winner:
            ctx.active.append(symbol)
            ctx.reasons[symbol] = "proven mature winner retained across rank drift"
    for symbol in sorted(ctx.held_symbols):
        position = ctx.account.positions[symbol]
        leader = ctx.leaders.get(symbol)
        if (
            position.lifecycle == Lifecycle.RECOVERY.value
            and leader is not None
            and leader.score >= self.cfg.leader_mature_score
            and leader.confidence >= self.cfg.leader_min_confidence
            and self._structure_ok(ctx.user_panel[symbol], ctx.date)
        ):
            if symbol not in ctx.active:
                ctx.active.append(symbol)
            position.lifecycle = Lifecycle.CORE.value
            for tranche in position.tranches:
                tranche.lifecycle = Lifecycle.CORE.value
            ctx.reasons[symbol] = "repaired recovery position graduated to core"
            ctx.mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_PROMOTION


def _contract_active_leaders(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    stable_k = max(0, min(ctx.account.dynamic_k, self.cfg.max_positions))
    if not stable_k or len(ctx.active) <= stable_k:
        return
    proven: set[str] = set()
    for symbol in ctx.active:
        position = ctx.account.positions.get(symbol)
        if position is None:
            continue
        proven_winner = (
            position.highest_close / max(position.avg_cost, 1e-12) - 1.0 >= 0.10
            and ctx.prices[symbol] / max(position.avg_cost, 1e-12) - 1.0 >= 0
            and scalar(ctx.user_panel[symbol].loc[ctx.date], "close")
            >= scalar(ctx.user_panel[symbol].loc[ctx.date], f"ma{self.cfg.trend_medium}")
        )
        if proven_winner:
            proven.add(symbol)
    keep_count = max(stable_k, len(proven))

    def key(symbol: str) -> tuple[float, str]:
        return -self._retention_score(symbol, ctx.leaders, ctx.account), symbol

    ranked_retention = sorted(ctx.active, key=key)
    retained = sorted(proven, key=key)
    retained.extend(symbol for symbol in ranked_retention if symbol not in proven)
    retained = retained[:keep_count]
    for symbol in set(ctx.active) - set(retained):
        ctx.reasons[symbol] = "dynamic K contraction after hysteresis"
        ctx.mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT
    ctx.active = retained


def _admit_ranked_leaders(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    available = [item for item in ctx.ranked if item.symbol not in ctx.active]
    while (
        len(ctx.active) < ctx.target_k
        and available
        and not ctx.risk.freeze_new_risk
        and ctx.risk.state.value != "RISK_OFF"
        and ctx.opportunity is not Opportunity.RECOVERY
    ):
        item = max(
            available,
            key=lambda candidate: (
                self._admission_utility(
                    candidate=candidate,
                    active=ctx.active,
                    leaders=ctx.leaders,
                    user_panel=ctx.user_panel,
                    date=ctx.date,
                    account=ctx.account,
                ),
                candidate.score,
                candidate.symbol,
            ),
        )
        ctx.active.append(item.symbol)
        available.remove(item)


def _rotation_evidence(ctx: _LeaderTargetContext) -> _RotationEvidence | None:
    self = ctx.policy
    if not (
        ctx.active
        and len(ctx.active) >= ctx.target_k
        and ctx.ranked
        and not ctx.risk.freeze_new_risk
        and self._rotation_allowed(ctx.account, ctx.date, ctx.user_panel)
    ):
        return None
    challenger = next((item for item in ctx.ranked if item.symbol not in ctx.active), None)
    weakest = min(
        ctx.active,
        key=lambda symbol: (
            self._retention_score(symbol, ctx.leaders, ctx.account),
            symbol,
        ),
    )
    frame = ctx.user_panel[weakest]
    row = frame.loc[ctx.date]
    old_structure_broken = (
        scalar(row, "close") < scalar(row, f"ma{self.cfg.trend_fast}")
        and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
    )
    position = ctx.account.positions.get(weakest)
    held_sessions = (
        len(frame.loc[pd.Timestamp(position.entry_date) : ctx.date])
        if position is not None and position.entry_date
        else 0
    )
    if challenger is None or position is None:
        return None
    peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
    winner_penalty = min(0.20, 0.50 * max(0.0, peak_mfe))
    same_cluster_penalty = 0.15 if challenger.industry == ctx.leaders[weakest].industry else 0.0
    uncertainty_penalty = 0.05 * max(0.0, 1.0 - challenger.confidence)
    edge = (
        challenger.score
        - ctx.leaders[weakest].score
        - 0.01
        - winner_penalty
        - same_cluster_penalty
        - uncertainty_penalty
        + (0.08 if old_structure_broken else 0.0)
    )
    industry_handoff = self._industry_handoff(
        challenger=challenger,
        incumbent=ctx.leaders[weakest],
    )
    key = f"leader_rotation:{weakest}->{challenger.symbol}"
    ctx.account.replacement_tenure[key] = (
        ctx.account.replacement_tenure.get(key, 0) + 1
        if edge >= self.cfg.replacement_edge and old_structure_broken
        else 0
    )
    return _RotationEvidence(
        challenger,
        weakest,
        held_sessions,
        edge,
        industry_handoff,
        key,
    )


def _apply_leader_rotation(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    evidence = _rotation_evidence(ctx)
    observed_key = evidence.key if evidence is not None else ""
    if (
        evidence is not None
        and ctx.account.replacement_tenure[evidence.key] >= self.cfg.replacement_confirm_days
        and evidence.held_sessions >= self.cfg.min_hold_days
    ):
        challenger = evidence.challenger
        weakest = evidence.weakest
        ctx.active.remove(weakest)
        ctx.active.append(challenger.symbol)
        ctx.rotation_transfers[challenger.symbol] = min(
            self.cfg.max_symbol_weight,
            self.cfg.replacement_transfer_cap,
            ctx.weights_now.get(weakest, 0.0),
        )
        ctx.account.rotation_dates.append(str(ctx.date.date()))
        ctx.account.replacement_events.append(
            {
                "signal_date": str(ctx.date.date()),
                "old_symbol": weakest,
                "new_symbol": challenger.symbol,
                "old_close": ctx.prices[weakest],
                "new_close": ctx.prices[challenger.symbol],
                "edge": evidence.edge,
                "industry_handoff": evidence.industry_handoff,
            }
        )
        ctx.reasons[weakest] = f"rotation exit: {challenger.symbol} confirmed edge"
        ctx.reasons[challenger.symbol] = f"rotation entry: replaces {weakest}"
        ctx.lifecycles[challenger.symbol] = Lifecycle.CORE
        ctx.mechanisms[weakest] = AttributionMechanism.LEADER_ROTATION
        ctx.mechanisms[challenger.symbol] = AttributionMechanism.LEADER_ROTATION
        ctx.replaces_symbols[challenger.symbol] = weakest
        ctx.account.replacement_tenure[evidence.key] = 0
    for key in tuple(ctx.account.replacement_tenure):
        unscoped = "->" in key and ":" not in key
        if (key.startswith("leader_rotation:") or unscoped) and key != observed_key:
            ctx.account.replacement_tenure[key] = 0


def _exit_deteriorated_leaders(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    for symbol in list(ctx.active):
        if self._leader_lifecycle_exit_confirmed(
            symbol=symbol,
            date=ctx.date,
            user_panel=ctx.user_panel,
            leaders=ctx.leaders,
            account=ctx.account,
        ):
            ctx.active.remove(symbol)
            ctx.reasons[symbol] = "leader lifecycle exit: confirmed structural deterioration"
            ctx.mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT


def _initialize_leader_proposal(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    ctx.account.active_leaders = sorted(
        set(ctx.active), key=lambda symbol: (-ctx.leaders[symbol].score, symbol)
    )
    ctx.proposed = {
        symbol: ctx.weights_now.get(symbol, 0.0)
        for symbol in ctx.account.active_leaders
        if ctx.weights_now.get(symbol, 0.0) > 0
    }
    ctx.new_core = [symbol for symbol in ctx.account.active_leaders if symbol not in ctx.proposed]
    if ctx.risk.freeze_new_risk:
        ctx.account.active_leaders = [
            symbol for symbol in ctx.account.active_leaders if symbol in ctx.held_symbols
        ]
        ctx.new_core = []
    ctx.gross_cap = min(
        ctx.risk.target_gross_cap,
        self.cfg.strong_trend_gross
        if ctx.opportunity is Opportunity.STRONG_TREND
        else self.cfg.trend_target_gross
        if ctx.opportunity is Opportunity.TREND
        else self.cfg.weak_gross
        if ctx.opportunity is Opportunity.WEAK
        else self.cfg.choppy_target_gross,
    )
    ctx.projected_industry_cap = (
        ctx.gross_cap
        if ctx.account.candidate_tenure.get("evidence_concentration", 0) == 1
        else self.cfg.industry_weight_cap
    )
    ctx.satellite_reserve = sum(
        ctx.weights_now.get(symbol, 0.0)
        for symbol, position in ctx.account.positions.items()
        if position.shares > 0
        and position.lifecycle == Lifecycle.SATELLITE.value
        and symbol not in ctx.proposed
    )
    ctx.index_chase = (
        max(
            float(ctx.risk.evidence.get("broad_ret5", 0.0)),
            float(ctx.risk.evidence.get("tech_ret5", 0.0)),
        )
        >= self.cfg.add_index_chase_ret5
    )


def _scale_seed_industries(ctx: _LeaderTargetContext) -> None:
    for industry in {ctx.leaders[symbol].industry for symbol in ctx.proposed}:
        members = [symbol for symbol in ctx.proposed if ctx.leaders[symbol].industry == industry]
        industry_weight = sum(ctx.proposed[symbol] for symbol in members)
        if industry != "unknown" and industry_weight > ctx.projected_industry_cap:
            scale = ctx.projected_industry_cap / industry_weight
            for symbol in members:
                ctx.proposed[symbol] *= scale


def _high_confidence_seed(ctx: _LeaderTargetContext) -> tuple[bool, bool]:
    self = ctx.policy
    high_confidence = bool(
        self.cfg.confidence_sizing_enabled
        and ctx.opportunity is Opportunity.STRONG_TREND
        and ctx.risk.state.value == "NORMAL"
        and not ctx.risk.freeze_new_risk
        and not ctx.index_chase
        and len(ctx.new_core) >= 2
        and float(ctx.risk.evidence.get("trend_health", 0.0)) >= 0.70
        and all(
            ctx.leaders[symbol].score >= self.cfg.high_confidence_entry_score
            and ctx.leaders[symbol].confidence >= self.cfg.leader_min_confidence
            and ctx.leaders[symbol].components.get("industry_breadth", 0.0)
            >= self.cfg.high_confidence_entry_breadth
            and scalar(ctx.user_panel[symbol].loc[ctx.date], "vol20", math.inf)
            <= self.cfg.high_confidence_entry_vol20
            for symbol in ctx.new_core
        )
    )
    exceptional = bool(
        high_confidence
        and min(ctx.leaders[symbol].score for symbol in ctx.new_core) >= 0.90
        and float(ctx.risk.evidence.get("trend_health", 0.0)) >= 0.82
    )
    return high_confidence, exceptional


def _seed_leader_core(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    staged = ctx.account.candidate_tenure.get("leader_cycle_staged_handoff", 0) == 1
    high_confidence, exceptional = _high_confidence_seed(ctx)
    ctx.account.candidate_tenure["confidence_sized_entry"] = int(high_confidence)
    configured_gross = (
        self.cfg.exceptional_entry_gross
        if exceptional
        else self.cfg.high_confidence_entry_gross
        if high_confidence
        else self.cfg.trend_entry_gross
    )
    entry_gross = min(
        max(0.0, ctx.gross_cap - ctx.satellite_reserve),
        self.cfg.core_admission_weight if staged else configured_gross,
    )
    conviction = self._conviction_evidence_qualified(
        symbols=ctx.new_core,
        leaders=ctx.leaders,
        user_panel=ctx.user_panel,
        date=ctx.date,
        high_confidence=high_confidence,
    )
    ctx.account.candidate_tenure["conviction_evidence_qualified"] = int(conviction)
    raw = self._conviction_shares(
        ctx.new_core,
        ctx.leaders,
        evidence_qualified=conviction,
    )
    for symbol, share in zip(ctx.new_core, raw, strict=True):
        entry_cap = self.cfg.single_core_entry_cap if len(ctx.new_core) == 1 else self.cfg.max_symbol_weight
        ctx.proposed[symbol] = min(entry_cap, entry_gross * float(share))
        ctx.lifecycles[symbol] = Lifecycle.CORE
        ctx.reasons.setdefault(
            symbol,
            "confirmed rearmed leader owner handoff" if staged else "confirmed mature leader core",
        )
    if ctx.proposed and staged:
        ctx.account.candidate_tenure["leader_cycle_staged_handoff"] = 0
        ctx.account.candidate_tenure["leader_cycle_handoff_epoch"] = ctx.account.strategic_epochs_completed
    _scale_seed_industries(ctx)


def _redistribute_industry_core(ctx: _LeaderTargetContext, symbol: str) -> None:
    self = ctx.policy
    industry = ctx.leaders[symbol].industry
    members = [item for item in ctx.account.active_leaders if ctx.leaders[item].industry == industry]
    incumbents = [item for item in members if item in ctx.proposed]
    industry_weight = sum(ctx.proposed[item] for item in incumbents)
    if (
        industry == "unknown"
        or ctx.leaders[symbol].components.get("unknown_industry", 0.0) >= 0.5
        or not incumbents
        or industry_weight <= 0
    ):
        return
    scores = np.array([max(0.01, ctx.leaders[item].score) for item in members], dtype=float)
    scores /= scores.sum()
    redistributed = min(industry_weight, ctx.projected_industry_cap)
    for member, share in zip(members, scores, strict=True):
        ctx.proposed[member] = min(self.cfg.max_symbol_weight, redistributed * float(share))
    ctx.lifecycles[symbol] = Lifecycle.CORE
    ctx.reasons[symbol] = "dynamic K expansion within industry cap"


def _expand_leader_core(ctx: _LeaderTargetContext) -> None:
    self = ctx.policy
    available = max(0.0, ctx.gross_cap - ctx.satellite_reserve - sum(ctx.proposed.values()))
    allocation = min(
        self.cfg.core_admission_weight,
        available / len(ctx.new_core) if ctx.new_core else 0.0,
    )
    for symbol in ctx.new_core:
        industry = ctx.leaders[symbol].industry
        industry_weight = sum(
            weight for held, weight in ctx.proposed.items() if ctx.leaders[held].industry == industry
        )
        admitted = min(
            ctx.rotation_transfers.get(symbol, allocation),
            available,
            max(0.0, ctx.projected_industry_cap - industry_weight),
        )
        if admitted > 0:
            ctx.proposed[symbol] = admitted
            available = max(0.0, available - admitted)
            ctx.lifecycles[symbol] = Lifecycle.CORE
            ctx.reasons.setdefault(symbol, "confirmed mature leader admission")
    for symbol in ctx.new_core:
        if symbol not in ctx.proposed:
            _redistribute_industry_core(ctx, symbol)


def _allocate_new_leader_core(ctx: _LeaderTargetContext) -> None:
    if not ctx.proposed and ctx.new_core:
        _seed_leader_core(ctx)
    elif ctx.new_core:
        _expand_leader_core(ctx)


def _leader_targets(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    prices: dict[str, float],
) -> tuple[Target, ...] | None:
    """Build ordinary leader targets or decline when no admissible book exists."""

    if risk.state.value == "CRISIS":
        return None
    ctx = _leader_target_context(
        self,
        date=date,
        opportunity=opportunity,
        risk=risk,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        weights_now=weights_now,
        prices=prices,
    )
    _restore_active_leaders(ctx)
    _contract_active_leaders(ctx)
    _admit_ranked_leaders(ctx)
    _apply_leader_rotation(ctx)
    _exit_deteriorated_leaders(ctx)
    _initialize_leader_proposal(ctx)
    _allocate_new_leader_core(ctx)
    apply_leader_extensions(ctx)
    ctx.proposed = self._cap_opportunity_gross(
        proposed=ctx.proposed,
        gross_cap=ctx.gross_cap,
        weights_now=weights_now,
        leaders=leaders,
        reasons=ctx.reasons,
        opportunity=opportunity,
    )
    if not ctx.proposed and not ctx.held_symbols:
        return None
    for symbol in ctx.held_symbols - set(ctx.proposed):
        ctx.reasons.setdefault(symbol, "confirmed leader deterioration")
        ctx.mechanisms.setdefault(symbol, AttributionMechanism.LEADER_LIFECYCLE_EXIT)
    return self._targets(
        proposed=ctx.proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="mature leader lifecycle",
        origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_SELECTION,
        lifecycles=ctx.lifecycles,
        reasons=ctx.reasons,
        mechanisms=ctx.mechanisms,
        replaces_symbols=ctx.replaces_symbols,
    )


cap_opportunity_gross = _cap_opportunity_gross
leader_targets = _leader_targets
