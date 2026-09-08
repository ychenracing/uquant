"""Current stock proof and one non-latching ordinary market confirmation."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment
from .strategic.qualification_candidates import candidate_entry

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def ordinary_core_entry(
    self: PortfolioAllocator, *, symbol: str, score: LeaderScore, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], account: AccountState, confirmation_days: int,
    certificate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = account.strategic_cash_rearm.consumed_order
    repair_pending = reference is not None and any(
        order.symbol == symbol and order.order_id == reference.order_id
        and order.event_id == reference.event_id for order in account.pending_orders
    )
    tenure = account.leader_tenure.get(symbol, 0)
    if repair_pending:
        certificate = None  # The real repair order still needs its strict own proof.
    elif certificate is None and score.mature and tenure >= self.cfg.leader_tenure_days:
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
                       credible_count: int) -> tuple[bool, bool, list[str]]:
    """Current market evidence, independent of the observation clock."""
    keys = ("broad_ret120", "tech_ret120", "ai_fast_return", "declining_ratio",
            "below_ma20_ratio", "tech_speed", "broad_speed")
    missing = [key for key in keys if not isinstance(risk.evidence.get(key), (int, float))
               or isinstance(risk.evidence.get(key), bool)
               or not math.isfinite(risk.evidence[key])]
    sustained = impulse = False
    if not missing:
        broad, tech, fast, declining, below, tech_speed, broad_speed = (
            float(risk.evidence[key]) for key in keys)
        sustained = (min(broad, tech) >= .01 and opportunity is Opportunity.STRONG_TREND
                     and risk.votes <= 1 and credible_count >= 2)
        impulse = (min(broad, tech) >= -.01 and max(broad, tech) >= .01
                   and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
                   and risk.votes <= 1 and credible_count > 0 and fast >= .15
                   and declining <= .10 and below <= .10 and max(tech_speed, broad_speed) >= .15)
    return sustained, impulse, missing


def observe_ordinary_market(
    self: PortfolioAllocator, *, date: pd.Timestamp, opportunity: Opportunity,
    risk: RiskAssessment, leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame], account: AccountState,
) -> dict[str, Any]:
    """Observe fixed common evidence; actual risk/cash permission stays in the book."""
    credible = sorted(symbol for symbol, leader in leaders.items()
                      if symbol in user_panel and date in user_panel[symbol].index
                      and leader.mature and leader.score >= .82
                      and leader.confidence >= self.cfg.leader_min_confidence)
    sustained, impulse, missing = _market_conditions(
        opportunity=opportunity, risk=risk, credible_count=len(credible))
    session = date.toordinal()
    last = account.candidate_tenure.get("ordinary_market_session", 0)
    if last > session:
        raise ValueError("ordinary market observation moved backwards")
    previous_dates = [frame.index[frame.index < date][-1] for frame in user_panel.values()
                      if date in frame.index and (frame.index < date).any()]
    previous = max(previous_dates).toordinal() if previous_dates else 0
    streak = account.candidate_tenure.get("ordinary_market_streak", 0)
    if not sustained:
        streak = 0
    elif last != session:
        streak = (streak if last == previous else 0) + 1
    account.candidate_tenure["ordinary_market_session"] = session
    account.candidate_tenure["ordinary_market_streak"] = streak
    return {
        "as_of": str(date.date()), "confirmed": bool(impulse or (sustained and streak >= 3)),
        "sustained": sustained, "impulse": impulse, "streak": streak,
        "credible_symbols": credible, "missing_market_fields": missing,
    }
