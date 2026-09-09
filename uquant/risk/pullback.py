"""Explicit small new-capital permission from the final ordinary BaseRisk branch."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from ..config import SystemConfig
from ..models.strategic_grant import TERMINAL_STRATEGIC_GRANT_STATUSES
from ..models.trading import late_strategic_fill_allowed
from ..ordinary_pullback import current_pullback_proof, pullback_reentry_structure_open
from ..types import AccountState, LeaderScore, Risk, RiskAssessment


def pullback_risk_open(risk: RiskAssessment, account: AccountState) -> bool:
    """Current safety for fresh permission and a genuine original partial remainder."""
    return bool(
        risk.state in {Risk.NORMAL, Risk.CAUTION} and risk.reduction_level <= 1
        and risk.shock_state == "NONE" and account.capital_budget_level == 0
        and account.chronic_level == 0 and not account.sector_guard_active
        and risk.evidence.get("capital_budget_level") == 0
        and risk.evidence.get("chronic_level") == 0
        and all(risk.evidence.get(key) is False for key in (
            "independent_damage", "sector_guard_active", "acute_sector_evacuation", "strategic_damage_guard"))
        and not risk.evidence.get("sentinel_freeze_new_risk", False)
    )


def pullback_book_settled(account: AccountState) -> bool:
    """The initial batch may only use genuinely unowned, settled cash."""
    return bool(
        not any(p.shares > 0 for p in account.positions.values())
        and not account.pending_orders
        and not any(o.status not in {"FILLED", "CANCELLED", "REPLACED"}
                    or late_strategic_fill_allowed(o) for o in account.order_ledger)
        and not any(not epoch.terminal for epoch in account.strategic_epochs)
        and not account.active_strategic_epoch_id
        and (account.strategic_grant is None
             or account.strategic_grant.status in TERMINAL_STRATEGIC_GRANT_STATUSES)
        and not any(weight > 0 for field in (
            account.anchor_weights, account.protected_weights,
            account.strategic_restore_weights, account.strategic_cohort_targets,
        ) for weight in field.values())
        and not account.tactical_anchor_symbol and not account.recovery_conviction_symbol
    )


def authorize_pullback_entry(
    *, date: pd.Timestamp, risk: RiskAssessment, account: AccountState,
    user_panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore], cfg: SystemConfig,
) -> RiskAssessment:
    """Publish one dated shared budget without unfreezing any other entry route."""
    evidence = {k: v for k, v in risk.evidence.items() if k != "ordinary_pullback_permission"}
    if pullback_risk_open(risk, account) and pullback_book_settled(account):
        proofs = {}
        for symbol in sorted(user_panel.keys() & leaders.keys()):
            if not pullback_reentry_structure_open(
                account=account, symbol=symbol, date=date, frame=user_panel[symbol], cfg=cfg,
            ):
                continue
            proof = current_pullback_proof(symbol=symbol, date=date, frame=user_panel[symbol],
                                          leader=leaders[symbol], cfg=cfg)
            if (proof["block"] == "READY" and proof["values"]["median_amount"] * cfg.max_volume_participation
                    >= account.initial_cash * cfg.core_admission_weight):
                proofs[symbol] = proof
        if proofs:
            evidence["ordinary_pullback_permission"] = {
                "as_of": str(date.date()), "maximum_weight": min(cfg.core_admission_weight, risk.target_gross_cap),
                "proofs": proofs, "risk_state": risk.state.value,
            }
    return replace(risk, evidence=evidence)
