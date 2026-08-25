"""Same-call allocation carriers; AccountState remains the sole mutable authority."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AllocationState:
    """Values computed once and consumed by later allocation stages."""

    weights_now: dict[str, float]
    equity: float
    freeze_active: bool
    general_core_symbols: set[str]
    risk_neutral_recovery_handoff: bool
    risk_neutral_recovery_transfer: bool
    level1_recovery_repair: bool
    synchronized_protected_restore: bool
    bounded_recovery_repair: bool
    confirmed_recovery_trail: bool
    confirmed_hard_risk_trail: bool
    reason_clean_caution_anchor_cap: bool


@dataclass(frozen=True, slots=True)
class ProtectedRestoration:
    """One protected-book proposal and its completion decision."""

    proposed: dict[str, float]
    pending_replacement_members: set[str]
    fully_repaired: bool
    restore_complete_key: str
    restore_submitted_key: str
    restore_deferred_key: str
    pending_restore_buys: set[str]
    restore_confirmation_ready: bool
    restore_expansion_deferred: bool
    economic_restore_complete: bool
    restore_submission_has_buy: bool
