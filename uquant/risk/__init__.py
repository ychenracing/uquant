"""Compatibility surface for the fixed-order Base Risk package."""
# ruff: noqa: I001 - compatibility imports remain grouped by historical owner
from __future__ import annotations

import pandas as pd

from ..config import SystemConfig
from ..market_risk import (  # noqa: F401 - historical compatibility re-export
    build_base_market_family_snapshot as build_base_market_family_snapshot,
    evidence_family_votes as _evidence_family_votes,
)
from ..reference import ReferenceContext
from ..risk_sentinel.models import SentinelAssessment
from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment
from .anchors import dynamic_anchor_candidate as _dynamic_anchor_candidate
from .anchors import update_dynamic_anchors as _update_dynamic_anchors
from .assessment import (
    BaseRiskAssessor,
    DynamicAnchorUpdater,
    REFERENCE_ANCHORS,
    assess_base_risk as _assess_base_risk,
    assess_risk_with_capabilities,
)
from .capital import (
    capital_budget_repair_drawdown_confirmed as _capital_budget_repair_drawdown_confirmed,
    portfolio_drawdowns as _portfolio_drawdowns,
    update_capital_budget_ladder as _update_capital_budget_ladder,
)
from .recovery_state import persistent_crisis_cap as _persistent_crisis_cap
from .recovery_state import reset_recovery_owner_rearm as _reset_recovery_owner_rearm
from .strategic_guard import (
    strategic_crisis_severity as _strategic_crisis_severity,
    strategic_damage_guard_active as _strategic_damage_guard_active,
    strategic_damage_guard_persists as _strategic_damage_guard_persists,
    strategic_damage_guard_required as _strategic_damage_guard_required,
    strategic_grace_supported as _strategic_grace_supported,
    strategic_guard_level2_overlay_required as _strategic_guard_level2_overlay_required,
)
from .transitions import acute_sector_evacuation_required as _acute_sector_evacuation_required


def base_risk_assessor() -> BaseRiskAssessor:
    return _assess_base_risk


def dynamic_anchor_updater() -> DynamicAnchorUpdater:
    return _update_dynamic_anchors


def assess_risk(
    *,
    date: pd.Timestamp,
    broad: pd.DataFrame,
    tech: pd.DataFrame,
    reference_panel: dict[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    equity: float,
    cfg: SystemConfig,
    reference_context: ReferenceContext | None = None,
    configured_universe_size: int | None = None,
    sentinel_assessment: SentinelAssessment | None = None,
    sentinel_opportunity: Opportunity | str | None = None,
) -> RiskAssessment:
    """Return formal uquant risk with the optional freeze-only Sentinel overlay.

    The base assessor remains the sole owner of state, severity, reductions,
    and gross caps.  Integration is deliberately applied only to its immutable
    result, so Sentinel cannot mutate the durable account or create a parallel
    risk transition.
    """

    return assess_risk_with_capabilities(
        date=date,
        broad=broad,
        tech=tech,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        user_panel=user_panel,
        leaders=leaders,
        account=account,
        equity=equity,
        cfg=cfg,
        reference_context=reference_context,
        configured_universe_size=configured_universe_size,
        sentinel_assessment=sentinel_assessment,
        sentinel_opportunity=sentinel_opportunity,
        base_risk_assessor=base_risk_assessor(),
        dynamic_anchor_updater=dynamic_anchor_updater(),
    )


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
