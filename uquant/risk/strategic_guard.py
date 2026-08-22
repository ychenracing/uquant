"""Strategic grace, damage-guard, and crisis-severity ownership."""

from __future__ import annotations

from ..config import SystemConfig
from ..types import AccountState


def _strategic_grace_supported(
    *,
    account: AccountState,
) -> bool:
    """Protect only an evidenced early-cycle strategic reset."""
    return bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_early_cycle_epoch", -1) == account.strategic_epoch
    )


def _strategic_damage_guard_required(
    *,
    account: AccountState,
    operating_drawdown: float,
    transition_damage: float,
    votes: int,
    cfg: SystemConfig,
) -> bool:
    """Trim an immature concentrated handoff while preserving its lifecycle owner."""
    guard_already_claimed = bool(
        account.strategic_epoch > 0
        and account.strategic_epoch
        in {
            account.candidate_tenure.get("strategic_damage_guard_active_epoch", -1),
            account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1),
            account.candidate_tenure.get("strategic_damage_trim_epoch", -1),
        }
    )
    external_risk_already_claimed = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_external_risk_epoch", -1) == account.strategic_epoch
    )
    emerging = account.strategic_candidate_signature.startswith("strategic_qualification:EMERGING_SECULAR:")
    grace_days = (
        cfg.capital_budget_emerging_cohort_grace_days
        if emerging
        else cfg.capital_budget_new_cohort_grace_days
    )
    return bool(
        account.candidate_tenure.get("strategic_cohort_active", 0) == 1
        and account.candidate_tenure.get("strategic_cohort_started", 0) == 1
        and not guard_already_claimed
        and not external_risk_already_claimed
        and account.candidate_tenure.get("strategic_cohort_days", 0) < grace_days
        and operating_drawdown >= cfg.strategic_damage_guard_dd
        # This is an early-warning owner.  Once ordinary operating caution is
        # reached, the independent capital ladder already owns the reduction;
        # a second, tighter strategic cap would double-count the same damage.
        and operating_drawdown < cfg.operating_dd_caution
        and transition_damage >= cfg.strategic_damage_guard_transition
        # The live-book drawdown and transition-damage thresholds are already
        # two separate causal gates.  Require one corroborating evidence
        # family, but do not make a small configured universe wait for a
        # second correlated family while its actually funded book is falling.
        and votes >= 1
    )


def _strategic_damage_guard_persists(
    account: AccountState,
    cfg: SystemConfig,
) -> bool:
    """Keep the cap active only while a concentrated handoff is unfinished."""
    positive_targets = [
        float(weight) for weight in account.strategic_cohort_targets.values() if float(weight) > 1e-12
    ]
    return bool(
        len(positive_targets) <= 1 or max(positive_targets, default=0.0) > cfg.max_symbol_weight + 1e-12
    )


def _strategic_guard_level2_overlay_required(account: AccountState) -> bool:
    """Let an active strategic guard own a bounded level-2 refinement."""
    return bool(
        account.strategic_epoch > 0
        and account.capital_budget_level >= 2
        and account.candidate_tenure.get("strategic_damage_guard_active_epoch", -1) == account.strategic_epoch
        and account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1)
        != account.strategic_epoch
    )


def _strategic_damage_guard_active(account: AccountState) -> bool:
    """Keep a claimed guard authoritative until the strategy records repair."""
    return bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_damage_guard_active_epoch", -1) == account.strategic_epoch
        and account.candidate_tenure.get("strategic_damage_guard_complete_epoch", -1)
        != account.strategic_epoch
    )


def _strategic_crisis_severity(
    *,
    strategic_active: bool,
    reference_anchor_confirmed: bool,
    live_core_positions: int,
) -> str:
    """Classify strategic crises by live-book concentration, not its label."""

    if not strategic_active:
        return "NORMAL"
    if live_core_positions <= 1:
        return "CONCENTRATED"
    del reference_anchor_confirmed
    return "MARKET"


def _update_strategic_damage_guard(
    *,
    account: AccountState,
    operating_drawdown: float,
    transition_damage: float,
    votes: int,
    cfg: SystemConfig,
) -> bool:
    """Run the existing strategic damage-guard ownership slice in order."""

    strategic_damage_guard_triggered = _strategic_damage_guard_required(
        account=account,
        operating_drawdown=operating_drawdown,
        transition_damage=transition_damage,
        votes=votes,
        cfg=cfg,
    )
    persistent_strategic_damage_guard = bool(
        strategic_damage_guard_triggered and _strategic_damage_guard_persists(account, cfg)
    )
    if strategic_damage_guard_triggered and account.strategic_epoch > 0:
        if persistent_strategic_damage_guard:
            account.candidate_tenure["strategic_damage_guard_active_epoch"] = account.strategic_epoch
        else:
            # A diversified cohort needs one sparse de-risking observation,
            # not a persistent aggregate cap that repeatedly forces healthy
            # members out.  Record the one-shot owner for this epoch while a
            # concentrated handoff keeps the durable guard lifecycle above.
            account.candidate_tenure["strategic_damage_trim_epoch"] = account.strategic_epoch
    strategic_damage_guard = bool(strategic_damage_guard_triggered or _strategic_damage_guard_active(account))
    return strategic_damage_guard


__all__ = (
    "_strategic_crisis_severity",
    "_strategic_damage_guard_active",
    "_strategic_damage_guard_persists",
    "_strategic_damage_guard_required",
    "_strategic_grace_supported",
    "_strategic_guard_level2_overlay_required",
    "_update_strategic_damage_guard",
)
