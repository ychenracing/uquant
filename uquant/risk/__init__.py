"""Compatibility surface for the fixed-order Base Risk package."""

from __future__ import annotations

from ..market_risk import (
    build_base_market_family_snapshot as build_base_market_family_snapshot,
)
from ..market_risk import (  # noqa: F401 - historical compatibility re-export
    evidence_family_votes as _evidence_family_votes,
)
from .anchors import _dynamic_anchor_candidate as _dynamic_anchor_candidate
from .anchors import _update_dynamic_anchors as _update_dynamic_anchors
from .assessment import (
    REFERENCE_ANCHORS,
    _assess_base_risk,
    assess_risk,
)
from .assessment import _risk_runtime_seam as _risk_runtime_seam
from .capital import (
    _capital_budget_repair_drawdown_confirmed,
    _portfolio_drawdowns,
    _update_capital_budget_ladder,
)
from .recovery_state import _persistent_crisis_cap, _reset_recovery_owner_rearm
from .strategic_guard import (
    _strategic_crisis_severity,
    _strategic_damage_guard_active,
    _strategic_damage_guard_persists,
    _strategic_damage_guard_required,
    _strategic_grace_supported,
    _strategic_guard_level2_overlay_required,
)
from .transitions import _acute_sector_evacuation_required

# Preserve the historical reflection and pickle surface after physical relocation.
for _legacy_function in (
    _acute_sector_evacuation_required,
    _assess_base_risk,
    _capital_budget_repair_drawdown_confirmed,
    _dynamic_anchor_candidate,
    _persistent_crisis_cap,
    _portfolio_drawdowns,
    _reset_recovery_owner_rearm,
    _strategic_crisis_severity,
    _strategic_damage_guard_active,
    _strategic_damage_guard_persists,
    _strategic_damage_guard_required,
    _strategic_grace_supported,
    _strategic_guard_level2_overlay_required,
    _update_capital_budget_ladder,
    _update_dynamic_anchors,
    assess_risk,
):
    _legacy_function.__module__ = __name__
del _legacy_function


__all__ = ("REFERENCE_ANCHORS", "assess_risk")
