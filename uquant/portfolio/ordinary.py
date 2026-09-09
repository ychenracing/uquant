"""Independent stock proof with current broad-market confirmation."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..config import SystemConfig
from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment
from .strategic.qualification_candidates import candidate_entry, independent_market_confirmation

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
    if repair_pending:
        certificate = None  # The real repair order still needs its strict own proof.
    elif (certificate is None and score.mature and tenure >= self.cfg.leader_tenure_days
          and market is not None and market.get("as_of") == str(date.date())
          and market.get("confirmed") is True and symbol in market.get("credible_symbols", ())):
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
                       credible_count: int, cfg: SystemConfig) -> tuple[bool, list[str]]:
    """Reuse current market confirmation without another clock or risk authority."""
    keys = ("breadth20", "broad_ret20", "tech_ret20", "broad_ret120", "tech_ret120")
    missing = [key for key in keys if not isinstance(risk.evidence.get(key), (int, float))
               or isinstance(risk.evidence.get(key), bool)
               or not math.isfinite(risk.evidence[key])]
    confirmed = (opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
                 and risk.votes <= 1 and credible_count > 0
                 and independent_market_confirmation(cfg=cfg, risk=risk))
    return confirmed, missing


def observe_ordinary_market(
    self: PortfolioAllocator, *, date: pd.Timestamp, opportunity: Opportunity,
    risk: RiskAssessment, leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """Observe current market proof without granting shared or strict-route capital."""
    credible = sorted(symbol for symbol, leader in leaders.items()
                      if symbol in user_panel and date in user_panel[symbol].index
                      and leader.mature and leader.score >= .82
                      and leader.confidence >= self.cfg.leader_min_confidence)
    confirmed, missing = _market_conditions(
        opportunity=opportunity, risk=risk, credible_count=len(credible), cfg=self.cfg)
    return {
        "as_of": str(date.date()), "confirmed": confirmed, "confirmation_route": "independent_market",
        "credible_symbols": credible, "missing_market_fields": missing,
    }
