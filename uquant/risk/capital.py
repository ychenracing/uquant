"""Capital drawdown and persistent budget-ladder ownership."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import SystemConfig
from ..risk_sector import SectorGuardTransition
from ..types import AccountState
from .strategic_guard import (
    strategic_grace_supported as _strategic_grace_supported,
)
from .strategic_guard import (
    strategic_guard_level2_overlay_required as _strategic_guard_level2_overlay_required,
)


@dataclass(frozen=True, slots=True)
class CapitalObservation:
    """Read-only outputs from the existing budget-observation slice."""

    independent_damage: bool
    worsening_damage: bool
    observed_budget_level: int


@dataclass(frozen=True, slots=True)
class CapitalOverlays:
    """Read-only outputs from the existing capital-overlay slice."""

    strategic_guard_level2_overlay: bool
    freeze_new_risk: bool
    overlay_cap: float
    overlay_reduction_level: int


def _portfolio_drawdowns(account: AccountState, equity: float) -> tuple[float, float]:
    if account.positions:
        account.operating_peak = max(account.operating_peak or equity, equity)
    else:
        # Operating drawdown belongs to the currently deployed risk cohort.
        # Once the book is flat, the next cohort starts from the preserved cash
        # equity instead of inheriting an obsolete underwater high-water mark.
        account.operating_peak = equity
    account.capital_peak = max(account.capital_peak or account.initial_cash, equity)
    operating = max(0.0, 1.0 - equity / max(account.operating_peak, 1e-12))
    capital = max(0.0, 1.0 - equity / max(account.capital_peak, 1e-12))
    return operating, capital


portfolio_drawdowns = _portfolio_drawdowns


def deployed_drawdown(account: AccountState, equity: float) -> float:
    """Measure continuous exposure without resetting losses on a repair label.

    The operating peak can restart during recovery; the lifetime capital peak
    never restarts. This risk-only peak ends when the actual book is flat.
    Legacy held books conservatively inherit the recorded capital high water.
    """
    if any(position.shares > 0 for position in account.positions.values()):
        account.deployed_peak = max(account.deployed_peak or account.capital_peak or equity, equity)
    else:
        account.deployed_peak = equity
    return max(0.0, 1.0 - equity / max(account.deployed_peak, 1e-12))


def _update_capital_budget_ladder(
    account: AccountState,
    *,
    observed_level: int,
    repair_confirmed: bool,
    repair_days: int,
) -> None:
    """Escalate immediately; after confirmation release one tier per session."""
    current = account.capital_budget_level
    if observed_level > current:
        account.capital_budget_level = observed_level
        account.capital_budget_repair_streak = 0
        return
    if observed_level < current and repair_confirmed:
        account.capital_budget_repair_streak = min(account.capital_budget_repair_streak + 1, repair_days)
        if account.capital_budget_repair_streak >= repair_days:
            account.capital_budget_level = max(observed_level, current - 1)
        return
    account.capital_budget_repair_streak = 0


def _capital_budget_repair_drawdown_confirmed(
    *,
    level: int,
    deployed_drawdown: float,
    cfg: SystemConfig,
) -> bool:
    """Repair current deployed risk, without requiring cash to earn back history.

    Historical losses and the capital high-water mark remain account facts.
    Current-book repair uses continuous deployed exposure. Requiring
    historical high-water recovery made a settled cash account's freeze circular:
    no new exposure until profits, but no profits without new exposure.
    Market confirmation and gradual tier release remain in the shared ladder.
    """

    threshold = (
        cfg.capital_dd_crisis
        if level >= 4
        else cfg.capital_budget_level3_dd
        if level >= 3
        else cfg.capital_budget_level2_dd
        if level >= 2
        else cfg.operating_dd_caution
    )
    return deployed_drawdown < threshold


def _independent_capital_damage(
    *,
    sector_guard: SectorGuardTransition,
    reference_anchor_break: bool,
    held_damage_ratio: float,
    transition_damage: float,
    votes: int,
    cfg: SystemConfig,
) -> bool:
    return bool(
        sector_guard.active
        or (
            held_damage_ratio >= cfg.concentrated_break_ratio
            and transition_damage >= cfg.transition_damage_freeze
            and votes >= 2
        )
        or (
            reference_anchor_break
            and held_damage_ratio >= cfg.concentrated_break_ratio
            and transition_damage >= cfg.transition_damage_freeze
            and votes >= 4
        )
    )


def _observed_capital_budget_level(
    *,
    capital_dd: float,
    deployed_dd: float,
    worsening_damage: bool,
    independent_damage: bool,
    votes: int,
    sector_stress: float,
    transition_damage: float,
    held_damage_ratio: float,
    cfg: SystemConfig,
) -> int:
    if (
        capital_dd >= cfg.capital_dd_crisis
        and worsening_damage
        and votes >= 4
        and sector_stress >= 0.50
        and transition_damage >= 0.68
    ):
        return 4
    if (
        capital_dd >= cfg.capital_budget_level3_dd
        and worsening_damage
        and votes >= 4
        and transition_damage >= cfg.transition_damage_freeze
    ):
        return 3
    if capital_dd >= cfg.capital_budget_level2_dd and independent_damage:
        return 2
    if deployed_dd >= cfg.operating_dd_caution and (
        votes >= 2 or (votes >= 1 and held_damage_ratio > 0)
    ):
        return 1
    return 0


def _young_strategic_cohort(
    *,
    account: AccountState,
    cfg: SystemConfig,
    strategic_active: bool,
) -> bool:
    grace_days = (
        cfg.capital_budget_emerging_cohort_grace_days
        if account.strategic_candidate_signature.startswith("strategic_qualification:EMERGING_SECULAR:")
        else cfg.capital_budget_new_cohort_grace_days
    )
    return bool(
        strategic_active
        and _strategic_grace_supported(account=account)
        and account.strategic_candidate_signature.startswith(
            (
                "strategic_qualification:SECULAR:",
                "strategic_qualification:EMERGING_SECULAR:",
            )
        )
        and account.candidate_tenure.get("strategic_cohort_days", 0) < grace_days
    )


def _observe_capital_budget(
    *,
    account: AccountState,
    cfg: SystemConfig,
    sector_guard: SectorGuardTransition,
    reference_anchor_break: bool,
    held_damage_ratio: float,
    transition_damage: float,
    votes: int,
    capital_dd: float,
    deployed_dd: float,
    sector_stress: float,
    strategic_active: bool,
) -> CapitalObservation:
    """Run the existing capital-damage and budget-observation slice in order."""

    independent_damage = _independent_capital_damage(
        sector_guard=sector_guard,
        reference_anchor_break=reference_anchor_break,
        held_damage_ratio=held_damage_ratio,
        transition_damage=transition_damage,
        votes=votes,
        cfg=cfg,
    )
    worsening_damage = bool(
        independent_damage
        and (votes >= 3 or transition_damage >= 0.68 or held_damage_ratio >= cfg.concentrated_break_ratio)
    )
    observed_budget_level = 0
    if cfg.capital_budget_ladder_enabled:
        observed_budget_level = _observed_capital_budget_level(
            capital_dd=capital_dd,
            deployed_dd=deployed_dd,
            worsening_damage=worsening_damage,
            independent_damage=independent_damage,
            votes=votes,
            sector_stress=sector_stress,
            transition_damage=transition_damage,
            held_damage_ratio=held_damage_ratio,
            cfg=cfg,
        )
        young_strategic_cohort = _young_strategic_cohort(
            account=account,
            cfg=cfg,
            strategic_active=strategic_active,
        )
        young_cohort_systemic_break = bool(votes >= 5 and sector_stress >= 0.50 and transition_damage >= 0.80)
        if young_strategic_cohort and not young_cohort_systemic_break:
            # Early cohort volatility is already owned by the strategic damage
            # guard and independent market/live-book families. Do not let the
            # same immature high-water mark manufacture a second cap authority,
            # regardless of unrelated universe size.
            observed_budget_level = 0
    return CapitalObservation(
        independent_damage=independent_damage,
        worsening_damage=worsening_damage,
        observed_budget_level=observed_budget_level,
    )


def _apply_capital_overlays(
    *,
    account: AccountState,
    cfg: SystemConfig,
    observed_budget_level: int,
    transition_damage: float,
    votes: int,
    held_damage_ratio: float,
    deployed_dd: float,
    strategic_damage_guard: bool,
) -> CapitalOverlays:
    """Apply the existing persistent ladder and cap overlays in order."""

    # A settled damaged cash book belongs to the bounded flat-book repair
    # owner. Do not let the short deployed-risk streak bypass its certificate.
    _update_capital_budget_ladder(
        account,
        observed_level=observed_budget_level,
        repair_confirmed=(
            transition_damage <= cfg.transition_damage_repair
            and votes <= 1
            and held_damage_ratio < 0.50
            and _capital_budget_repair_drawdown_confirmed(
                level=account.capital_budget_level,
                deployed_drawdown=(
                    deployed_dd
                    if any(position.shares > 0 for position in account.positions.values())
                    else max(0.0, 1.0 - account.cash / max(account.capital_peak, 1e-12))
                ),
                cfg=cfg,
            )
        ),
        repair_days=cfg.capital_budget_repair_days,
    )
    strategic_guard_level2_overlay = _strategic_guard_level2_overlay_required(account)
    if strategic_guard_level2_overlay:
        account.candidate_tenure["strategic_guard_level2_epoch"] = account.strategic_epoch
    freeze_new_risk = bool(
        strategic_damage_guard or account.capital_budget_level >= 1 or account.chronic_level >= 1
    )
    overlay_cap = cfg.max_gross
    if account.capital_budget_level >= 4:
        overlay_cap = min(overlay_cap, cfg.market_crisis_gross)
    elif account.capital_budget_level >= 3:
        overlay_cap = min(overlay_cap, cfg.capital_budget_level3_cap)
    elif account.capital_budget_level >= 2:
        overlay_cap = min(overlay_cap, cfg.capital_budget_level2_cap)
        if strategic_guard_level2_overlay:
            overlay_cap = min(overlay_cap, cfg.strategic_guard_level2_cap)
    if strategic_damage_guard:
        overlay_cap = min(overlay_cap, cfg.strategic_damage_guard_gross)
    if account.chronic_level >= 3:
        overlay_cap = min(overlay_cap, cfg.chronic_severe_cap)
    elif account.chronic_level >= 2:
        overlay_cap = min(overlay_cap, cfg.chronic_moderate_cap)
    overlay_reduction_level = (
        3
        if account.capital_budget_level >= 4
        else 2
        if overlay_cap < cfg.max_gross - 1e-12
        else 1
        if freeze_new_risk
        else 0
    )
    return CapitalOverlays(
        strategic_guard_level2_overlay=strategic_guard_level2_overlay,
        freeze_new_risk=freeze_new_risk,
        overlay_cap=overlay_cap,
        overlay_reduction_level=overlay_reduction_level,
    )


apply_capital_overlays = _apply_capital_overlays
capital_budget_repair_drawdown_confirmed = _capital_budget_repair_drawdown_confirmed
observe_capital_budget = _observe_capital_budget
update_capital_budget_ladder = _update_capital_budget_ladder


__all__ = (
    "CapitalObservation",
    "CapitalOverlays",
    "_apply_capital_overlays",
    "_capital_budget_repair_drawdown_confirmed",
    "_observe_capital_budget",
    "_portfolio_drawdowns",
    "_update_capital_budget_ladder",
    "apply_capital_overlays",
    "capital_budget_repair_drawdown_confirmed",
    "observe_capital_budget",
    "portfolio_drawdowns",
    "update_capital_budget_ladder",
)
