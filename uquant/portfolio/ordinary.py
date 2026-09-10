"""Independent stock proof with bounded ordinary maturity admission."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..types import AccountState, LeaderScore, Opportunity, Risk, RiskAssessment
from .strategic.qualification_candidates import candidate_entry

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def ordinary_core_entry(
    self: PortfolioAllocator, *, symbol: str, score: LeaderScore, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], account: AccountState, confirmation_days: int,
    certificate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = account.strategic_cash_rearm.consumed_order
    repair_pending = reference is not None and any(
        order.symbol == symbol and order.order_id == reference.order_id
        and order.event_id == reference.event_id for order in account.pending_orders
    )
    tenure = account.leader_tenure.get(symbol, 0)
    qualification = account.strategic_qualification
    strategic_claims = (
        bool(account.strategic_cohort_targets)
        or any(p.shares > 0 and (p.grant_id or p.epoch_id) for p in account.positions.values())
        or any(o.grant_id or o.epoch_id for o in account.pending_orders)
    )
    forming_full = (
        qualification.qualification_last_observed_session == str(date.date())
        and qualification.qualification_quorum == "FULL_COHORT"
        and qualification.qualification_streak > 0
        and not qualification.qualification_ready
        and not qualification.deployment_blocked
    )
    local_open = (market is not None and market.get("mature_entry_open") is True
                  and not strategic_claims and not forming_full)
    if repair_pending:
        certificate = None  # The real repair order still needs its strict own proof.
    elif (certificate is None and score.mature and tenure >= self.cfg.leader_tenure_days
          and market is not None and market.get("as_of") == str(date.date())
          and (market.get("impulse") is True or local_open)
          and symbol in market.get("credible_symbols", ())):
        certificate = {
            "qualification_route": "mature_core", "qualification_quorum": "ORDINARY_CORE",
            "required_confirmation": self.cfg.leader_tenure_days,
            "confirmations": {"leader_tenure": tenure}, "as_of": str(date.date()),
        }
    return candidate_entry(
        self, symbol=symbol, score=score, date=date, user_panel=user_panel,
        account=account, confirmation_days=confirmation_days, certificate=certificate,
    )


def _market_conditions(*, opportunity: Opportunity, risk: RiskAssessment,
                       credible_count: int) -> tuple[bool, list[str]]:
    """Current impulse evidence; persistence belongs to the existing stock routes."""
    keys = ("broad_ret120", "tech_ret120", "ai_fast_return", "declining_ratio",
            "below_ma20_ratio", "tech_speed", "broad_speed")
    missing = [key for key in keys if not isinstance(risk.evidence.get(key), (int, float))
               or isinstance(risk.evidence.get(key), bool)
               or not math.isfinite(risk.evidence[key])]
    impulse = False
    if not missing:
        broad, tech, fast, declining, below, tech_speed, broad_speed = (
            float(risk.evidence[key]) for key in keys)
        impulse = (min(broad, tech) >= -.01 and max(broad, tech) >= .01
                   and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
                   and risk.votes <= 1 and credible_count > 0 and fast >= .15
                   and declining <= .10 and below <= .10 and max(tech_speed, broad_speed) >= .15)
    return impulse, missing


def observe_ordinary_market(
    self: PortfolioAllocator, *, date: pd.Timestamp, opportunity: Opportunity,
    risk: RiskAssessment, leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Observe today's fast-entry proof without granting shared or strict-route capital."""
    credible = sorted(symbol for symbol, leader in leaders.items()
                      if symbol in user_panel and date in user_panel[symbol].index
                      and leader.mature and leader.score >= .82
                      and leader.confidence >= self.cfg.leader_min_confidence)
    impulse, missing = _market_conditions(
        opportunity=opportunity, risk=risk, credible_count=len(credible))
    return {
        "as_of": str(date.date()), "confirmed": impulse, "impulse": impulse,
        "credible_symbols": credible, "missing_market_fields": missing,
        "mature_entry_open": (
            not missing and risk.state is Risk.NORMAL and not risk.freeze_new_risk
            and not risk.evidence.get("freeze_new_risk", False)
            and not risk.evidence.get("sentinel_freeze_new_risk", False)
            and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        ),
    }
