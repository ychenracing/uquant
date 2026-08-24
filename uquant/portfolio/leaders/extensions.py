"""Leader pyramid, satellite, and challenger-scout stages."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Protocol

import pandas as pd

from ...types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    RiskAssessment,
)

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy


class LeaderExtensionContext(Protocol):
    policy: LeaderPortfolioPolicy
    date: pd.Timestamp
    opportunity: Opportunity
    risk: RiskAssessment
    user_panel: dict[str, pd.DataFrame]
    leaders: dict[str, LeaderScore]
    account: AccountState
    weights_now: dict[str, float]
    prices: dict[str, float]
    emerging: list[LeaderScore]
    proposed: dict[str, float]
    lifecycles: dict[str, Lifecycle]
    reasons: dict[str, str]
    mechanisms: dict[str, AttributionMechanism]
    gross_cap: float
    projected_industry_cap: float
    satellite_reserve: float
    index_chase: bool


def _leader_add_state(ctx: LeaderExtensionContext, symbol: str) -> tuple[bool, bool, float, float]:
    position = ctx.account.positions[symbol]
    tranche_lifecycles = {item.lifecycle for item in position.tranches if item.shares > 0}
    has_add1 = Lifecycle.ADD1.value in tranche_lifecycles
    has_add2 = Lifecycle.ADD2.value in tranche_lifecycles
    if not position.tranches:
        has_add1 = position.lifecycle == Lifecycle.ADD1.value
        has_add2 = position.lifecycle == Lifecycle.ADD2.value
    mfe = max(
        (
            max(item.mfe, ctx.prices[symbol] / max(item.avg_cost, 1e-12) - 1.0)
            for item in position.tranches
            if item.shares > 0
        ),
        default=ctx.prices[symbol] / max(position.avg_cost, 1e-12) - 1.0,
    )
    industry = ctx.leaders[symbol].industry
    industry_weight = sum(
        weight for held, weight in ctx.proposed.items() if ctx.leaders[held].industry == industry
    )
    return has_add1, has_add2, mfe, max(0.0, ctx.projected_industry_cap - industry_weight)


def _first_leader_add(
    ctx: LeaderExtensionContext,
    *,
    symbol: str,
    available: float,
    cooldown_complete: bool,
    has_add1: bool,
    has_add2: bool,
    mfe: float,
    industry_room: float,
) -> float | None:
    self = ctx.policy
    if not (
        not has_add1
        and not has_add2
        and cooldown_complete
        and not ctx.index_chase
        and not ctx.risk.freeze_new_risk
        and ctx.account.candidate_tenure.get("confidence_sized_entry", 0) == 0
        and mfe >= self.cfg.add1_min_mfe
        and ctx.risk.state.value in {"NORMAL", "CAUTION"}
        and ctx.opportunity is not Opportunity.RECOVERY
        and ctx.proposed[symbol] < self.cfg.max_symbol_weight
    ):
        return None
    increment = min(
        self.cfg.add1_weight,
        available,
        industry_room,
        self.cfg.max_symbol_weight - ctx.proposed[symbol],
    )
    if increment <= 1e-12:
        return available
    ctx.proposed[symbol] = min(self.cfg.max_symbol_weight, ctx.proposed[symbol] + increment)
    ctx.lifecycles[symbol] = Lifecycle.ADD1
    ctx.reasons[symbol] = "ADD1: positive MFE with normal risk"
    ctx.mechanisms[symbol] = AttributionMechanism.LEADER_PYRAMID
    return max(0.0, ctx.gross_cap - ctx.satellite_reserve - sum(ctx.proposed.values()))


def _second_leader_add(
    ctx: LeaderExtensionContext,
    *,
    symbol: str,
    available: float,
    cooldown_complete: bool,
    has_add1: bool,
    has_add2: bool,
    mfe: float,
    industry_room: float,
) -> float:
    self = ctx.policy
    if not (
        has_add1
        and not has_add2
        and cooldown_complete
        and not ctx.index_chase
        and not ctx.risk.freeze_new_risk
        and mfe >= self.cfg.add2_min_mfe
        and ctx.opportunity is Opportunity.STRONG_TREND
        and ctx.risk.state.value == "NORMAL"
        and ctx.proposed[symbol] < self.cfg.max_symbol_weight
    ):
        return available
    increment = min(
        self.cfg.add2_weight,
        available,
        industry_room,
        self.cfg.max_symbol_weight - ctx.proposed[symbol],
    )
    if increment <= 1e-12:
        return available
    ctx.proposed[symbol] = min(self.cfg.max_symbol_weight, ctx.proposed[symbol] + increment)
    ctx.lifecycles[symbol] = Lifecycle.ADD2
    ctx.reasons[symbol] = "ADD2: high-confidence trend continuation"
    ctx.mechanisms[symbol] = AttributionMechanism.LEADER_PYRAMID
    return max(0.0, ctx.gross_cap - ctx.satellite_reserve - sum(ctx.proposed.values()))


def _pyramid_leader_core(ctx: LeaderExtensionContext) -> None:
    self = ctx.policy
    available = max(0.0, ctx.gross_cap - ctx.satellite_reserve - sum(ctx.proposed.values()))
    for symbol in list(ctx.account.active_leaders):
        position = ctx.account.positions.get(symbol)
        if position is None or available < self.cfg.min_trade_weight:
            continue
        cooldown = self._add_cooldown_complete(
            account=ctx.account,
            frame=ctx.user_panel[symbol],
            date=ctx.date,
            cooldown_sessions=self.cfg.add_tranche_cooldown_sessions,
        )
        has_add1, has_add2, mfe, industry_room = _leader_add_state(ctx, symbol)
        first = _first_leader_add(
            ctx,
            symbol=symbol,
            available=available,
            cooldown_complete=cooldown,
            has_add1=has_add1,
            has_add2=has_add2,
            mfe=mfe,
            industry_room=industry_room,
        )
        if first is not None:
            available = first
            continue
        available = _second_leader_add(
            ctx,
            symbol=symbol,
            available=available,
            cooldown_complete=cooldown,
            has_add1=has_add1,
            has_add2=has_add2,
            mfe=mfe,
            industry_room=industry_room,
        )


def _existing_satellites(ctx: LeaderExtensionContext) -> list[str]:
    return [
        symbol
        for symbol, position in ctx.account.positions.items()
        if position.shares > 0
        and (
            position.lifecycle == Lifecycle.SATELLITE.value
            or any(
                item.shares > 0 and item.lifecycle == Lifecycle.SATELLITE.value for item in position.tranches
            )
        )
    ]


def _manage_existing_satellites(ctx: LeaderExtensionContext) -> list[str]:
    self = ctx.policy
    satellites = _existing_satellites(ctx)
    for symbol in satellites:
        position = ctx.account.positions[symbol]
        held = len(ctx.user_panel[symbol].loc[pd.Timestamp(position.entry_date) : ctx.date])
        if ctx.leaders.get(symbol) and ctx.leaders[symbol].mature:
            ctx.proposed[symbol] = ctx.weights_now.get(symbol, 0.0)
            ctx.lifecycles[symbol] = Lifecycle.CORE
            ctx.reasons[symbol] = "satellite promoted to mature core"
            ctx.mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_PROMOTION
            position.lifecycle = Lifecycle.CORE.value
            promoted_shares = 0
            for tranche in position.tranches:
                if tranche.lifecycle == Lifecycle.SATELLITE.value:
                    tranche.lifecycle = Lifecycle.CORE.value
                    promoted_shares += tranche.shares
            ctx.account.lifecycle_events.append(
                {
                    "date": str(ctx.date.date()),
                    "symbol": symbol,
                    "from": Lifecycle.SATELLITE.value,
                    "to": Lifecycle.CORE.value,
                    "shares": promoted_shares,
                    "reason": "challenger scout confirmed",
                }
            )
            if symbol not in ctx.account.active_leaders:
                ctx.account.active_leaders.append(symbol)
        elif held <= self.cfg.emerging_expiry_days and self._structure_ok(ctx.user_panel[symbol], ctx.date):
            ctx.proposed[symbol] = ctx.weights_now.get(symbol, 0.0)
            ctx.lifecycles[symbol] = Lifecycle.SATELLITE
            ctx.reasons[symbol] = "emerging leader satellite observation"
            ctx.mechanisms[symbol] = AttributionMechanism.CHALLENGER_SCOUT
        else:
            ctx.reasons[symbol] = "satellite expiry or failed confirmation"
            ctx.mechanisms[symbol] = AttributionMechanism.SATELLITE_EXPIRY
            ctx.account.satellite_entry_dates.pop(symbol, None)
    return satellites


def _scout_evidence(
    ctx: LeaderExtensionContext,
    *,
    item: LeaderScore,
    active_industries: set[str],
    incumbents: list[LeaderScore],
) -> bool:
    self = ctx.policy
    weakest_score = min((incumbent.score for incumbent in incumbents), default=math.inf)
    fading = bool(
        incumbents and any(incumbent.components.get("acceleration", 0.5) < 0.50 for incumbent in incumbents)
    )
    return bool(
        incumbents
        and item.industry not in active_industries
        and item.industry != "unknown"
        and item.components.get("unknown_industry", 0.0) < 0.5
        and item.components.get("industry_rotation_strength", 0.0) >= self.cfg.industry_rotation_min_score
        and item.components.get("industry_breadth", 0.0) >= self.cfg.industry_rotation_breadth
        and item.score - weakest_score >= self.cfg.challenger_scout_score_edge
        and fading
    )


def _admit_scout_candidate(
    ctx: LeaderExtensionContext,
    *,
    item: LeaderScore,
    active_industries: set[str],
    incumbents: list[LeaderScore],
    incumbents_preserved: bool,
    idle_cash: float,
    observed_keys: set[str],
) -> tuple[float, bool]:
    self = ctx.policy
    if item.symbol in ctx.proposed:
        return idle_cash, False
    evidence = _scout_evidence(
        ctx,
        item=item,
        active_industries=active_industries,
        incumbents=incumbents,
    )
    key = f"challenger_scout:{item.industry}:{item.symbol}"
    observed_keys.add(key)
    ctx.account.replacement_tenure[key] = ctx.account.replacement_tenure.get(key, 0) + 1 if evidence else 0
    if (
        ctx.account.replacement_tenure[key] < self.cfg.challenger_scout_confirm_days
        or not incumbents_preserved
        or idle_cash + 1e-12 < self.cfg.challenger_scout_weight
    ):
        return idle_cash, False
    weight = min(
        self.cfg.challenger_scout_weight,
        idle_cash,
        max(0.0, ctx.gross_cap - sum(ctx.proposed.values())),
    )
    if weight < self.cfg.min_trade_weight:
        return idle_cash, False
    industry_weight = sum(
        value for held, value in ctx.proposed.items() if ctx.leaders[held].industry == item.industry
    )
    if industry_weight + weight > ctx.projected_industry_cap:
        return idle_cash, False
    ctx.proposed[item.symbol] = weight
    ctx.lifecycles[item.symbol] = Lifecycle.SATELLITE
    ctx.reasons[item.symbol] = "idle-cash challenger scout"
    ctx.mechanisms[item.symbol] = AttributionMechanism.CHALLENGER_SCOUT
    ctx.account.satellite_entry_dates[item.symbol] = str(ctx.date.date())
    ctx.account.scout_signature = key
    ctx.account.scout_entry_date = str(ctx.date.date())
    return idle_cash - weight, True


def _admit_challenger_scouts(ctx: LeaderExtensionContext, satellites: list[str]) -> None:
    self = ctx.policy
    observed_keys: set[str] = set()
    if (
        self.cfg.challenger_scout_enabled
        and not ctx.risk.freeze_new_risk
        and ctx.risk.state.value == "NORMAL"
        and ctx.opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
        and len(ctx.proposed) < self.cfg.max_positions
    ):
        slots = min(
            self.cfg.max_satellites - len(satellites),
            self.cfg.max_positions - len(ctx.proposed),
        )
        industries = {
            ctx.leaders[symbol].industry
            for symbol in ctx.account.active_leaders
            if symbol in ctx.leaders and symbol in ctx.proposed
        }
        incumbents = [
            ctx.leaders[symbol]
            for symbol in ctx.account.active_leaders
            if symbol in ctx.leaders and symbol in ctx.proposed
        ]
        idle_cash = max(0.0, 1.0 - sum(ctx.weights_now.values()))
        preserved = all(
            ctx.proposed.get(symbol, 0.0) + self.cfg.challenger_scout_incumbent_hysteresis
            >= ctx.weights_now.get(symbol, 0.0)
            for symbol, position in ctx.account.positions.items()
            if position.shares > 0 and symbol not in satellites
        )
        for item in ctx.emerging:
            if slots <= 0:
                break
            idle_cash, admitted = _admit_scout_candidate(
                ctx,
                item=item,
                active_industries=industries,
                incumbents=incumbents,
                incumbents_preserved=preserved,
                idle_cash=idle_cash,
                observed_keys=observed_keys,
            )
            if admitted:
                slots -= 1
    for key in tuple(ctx.account.replacement_tenure):
        if key.startswith("challenger_scout:") and key not in observed_keys:
            ctx.account.replacement_tenure[key] = 0


def apply_leader_extensions(ctx: LeaderExtensionContext) -> None:
    """Apply pyramids, existing satellite lifecycle, then new scouts."""

    _pyramid_leader_core(ctx)
    satellites = _manage_existing_satellites(ctx)
    _admit_challenger_scouts(ctx, satellites)


__all__ = ("LeaderExtensionContext", "apply_leader_extensions")
