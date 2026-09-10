"""Independent stock proof with current impulse or mature-industry corroboration."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment
from .strategic.qualification_candidates import candidate_entry, candidate_market_block

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
    industry_witnesses: tuple[str, ...] = ()
    if (market is not None and market.get("as_of") == str(date.date())
            and not market.get("missing_market_fields", ("unavailable",))):
        members = market.get("mature_industry_groups", {}).get(score.industry, ())
        if (symbol in members and len(members) >= self.cfg.strategic_cohort_min_size
                and all(account.leader_tenure.get(peer, 0) >= self.cfg.leader_tenure_days for peer in members)):
            industry_witnesses = tuple(members)
    if repair_pending:
        certificate = None  # The real repair order still needs its strict own proof.
    elif (certificate is None and score.mature and tenure >= self.cfg.leader_tenure_days
          and market is not None and market.get("as_of") == str(date.date())
          and ((market.get("impulse") is True and symbol in market.get("credible_symbols", ()))
               or industry_witnesses)):
        certificate = {
            "qualification_route": "mature_core", "qualification_quorum": "ORDINARY_CORE",
            "required_confirmation": self.cfg.leader_tenure_days,
            "confirmations": {"leader_tenure": tenure}, "as_of": str(date.date()),
        }
        if market.get("impulse") is not True:
            certificate.update(confirmation_basis="mature_industry", industry_witnesses=list(industry_witnesses))
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
    """Observe current ordinary witnesses without granting shared or strict-route capital."""
    credible = sorted(symbol for symbol, leader in leaders.items()
                      if symbol in user_panel and date in user_panel[symbol].index
                      and leader.mature and leader.score >= .82
                      and leader.confidence >= self.cfg.leader_min_confidence)
    impulse, missing = _market_conditions(
        opportunity=opportunity, risk=risk, credible_count=len(credible))
    industries: dict[str, list[str]] = {}
    for symbol in credible:
        if candidate_market_block(self, symbol=symbol, score=leaders[symbol], date=date,
                                  user_panel=user_panel) == "READY":
            industries.setdefault(leaders[symbol].industry, []).append(symbol)
    return {
        "as_of": str(date.date()), "confirmed": impulse, "impulse": impulse,
        "credible_symbols": credible, "missing_market_fields": missing,
        "mature_industry_groups": {industry: tuple(symbols) for industry, symbols in sorted(industries.items())
                                   if len(symbols) >= self.cfg.strategic_cohort_min_size},
    }
