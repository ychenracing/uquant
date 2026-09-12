"""Independent stock proof with bounded ordinary maturity admission."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from ..types import AccountState, LeaderScore, Opportunity, PendingOrder, Risk, RiskAssessment
from .strategic.qualification_candidates import candidate_entry

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def rearm_ordinary_market(*, account: AccountState, date: pd.Timestamp,
                         risk: RiskAssessment, market: dict[str, Any],
                         confirmation_days: int) -> None:
    """A sector exit invalidates mature-only market permission, not held capital."""
    tenure = account.candidate_tenure
    required_key = "ordinary_market_rearm_required"
    count_key = "ordinary_market_rearm_confirmation"
    session_key = "ordinary_market_rearm_session"
    guarded = bool(account.sector_guard_active or risk.evidence.get("sector_guard_active")
                   or risk.evidence.get("acute_sector_evacuation"))
    if guarded:
        tenure[required_key] = 1
    if not tenure.get(required_key, 0):
        return
    session, previous = date.toordinal(), tenure.get(session_key, 0)
    if session < previous:
        raise ValueError("ordinary market repair observations must be causal")
    healthy = market.get("mature_entry_open") is True and not guarded
    if not healthy:
        tenure[count_key] = 0
    elif session != previous:
        tenure[count_key] = min(confirmation_days, tenure.get(count_key, 0) + 1)
    tenure[session_key] = session
    count = tenure.get(count_key, 0)
    tenure[required_key] = int(count < confirmation_days)
    market["mature_entry_open"] = healthy and count >= confirmation_days
    market["mature_rearm_confirmation"] = {"observed": count, "required": confirmation_days}


def _ordinary_maturity_available(account: AccountState, date: pd.Timestamp,
                                 market: dict[str, Any] | None) -> bool:
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
    local_open = (market is not None and (market.get("mature_entry_open") is True
                  or market.get("persistent_mature_entry_open") is True)
                  and not strategic_claims and not forming_full)
    return local_open


def _credible_leaders(self: PortfolioAllocator, date: pd.Timestamp,
                      leaders: dict[str, LeaderScore],
                      user_panel: dict[str, pd.DataFrame]) -> list[str]:
    """Use the same current, mature stock witnesses for ordinary market routes."""
    return sorted(s for s, leader in leaders.items()
                  if s in user_panel and date in user_panel[s].index
                  and leader.mature and leader.score >= .82
                  and leader.confidence >= self.cfg.leader_min_confidence)


def _aligned_market_legs(legs: list[Any]) -> bool:
    return cast(bool, all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          and math.isfinite(v) for v in legs)
                and min(legs) >= -.01 and max(legs) >= .01)


def observe_persistent_maturity(self: PortfolioAllocator, *, account: AccountState,
                                date: pd.Timestamp, market: dict[str, Any],
                                opportunity: Opportunity, risk: RiskAssessment,
                                leaders: dict[str, LeaderScore],
                                user_panel: dict[str, pd.DataFrame]) -> None:
    """Reference leaders witness sustained market strength, never stock eligibility."""
    key, count_key = "ordinary_persistence_session", "ordinary_persistence_count"
    session, previous = date.toordinal(), account.candidate_tenure.get(key, 0)
    if session < previous:
        raise ValueError("ordinary persistence observations must be causal")
    clock = self._session_clock(user_panel, date)
    earlier = clock[clock < date]
    prior = earlier[-1].toordinal() if len(earlier) else 0
    witnesses = _credible_leaders(self, date, leaders, user_panel)
    legs = [risk.evidence.get(k) for k in ("broad_ret120", "tech_ret120")]
    aligned = _aligned_market_legs(legs)
    healthy = (market.get("as_of") == str(date.date())
               and not market.get("missing_market_fields") and aligned
               and opportunity is Opportunity.STRONG_TREND and risk.votes <= 1
               and risk.state is Risk.NORMAL and not risk.freeze_new_risk
               and not any(risk.evidence.get(k, False) for k in
                           ("freeze_new_risk", "sentinel_freeze_new_risk", "sector_guard_active"))
               and not account.sector_guard_active
               and len(witnesses) >= self.cfg.strategic_cohort_min_size)
    count = account.candidate_tenure.get(count_key, 0) if previous in {prior, session} else 0
    count = min(self.cfg.leader_tenure_days, count + int(previous != session)) if healthy else 0
    account.candidate_tenure.update({key: session, count_key: count})
    market["persistent_mature_entry_open"] = healthy and count >= self.cfg.leader_tenure_days
    market["persistent_maturity"] = {"witnesses": witnesses, "observed": count,
                                      "required": self.cfg.leader_tenure_days}


def observe_repair_maturity(self: PortfolioAllocator, *, account: AccountState,
                            date: pd.Timestamp, market: dict[str, Any],
                            user_panel: dict[str, pd.DataFrame]) -> None:
    """Confirm the repair route's own credible maturity, not weaker leader tenure."""
    key, prefix = "ordinary_repair_maturity_session", "ordinary_repair_maturity:"
    session, previous = date.toordinal(), account.candidate_tenure.get(key, 0)
    if session < previous:
        raise ValueError("repair maturity observations must be causal")
    clock = self._session_clock(user_panel, date)
    earlier = clock[clock < date]
    prior = earlier[-1].toordinal() if len(earlier) else 0
    credible = set(market.get("credible_symbols", ())) if market.get("as_of") == str(date.date()) else set()
    known = {name[len(prefix):] for name in account.replacement_tenure if name.startswith(prefix)}
    for symbol in known | credible:
        count = account.replacement_tenure.get(prefix + symbol, 0) if previous in {prior, session} else 0
        account.replacement_tenure[prefix + symbol] = (
            min(self.cfg.leader_tenure_days, count + int(previous != session)) if symbol in credible else 0)
    account.candidate_tenure[key] = session


def _mature_core_eligible(self: PortfolioAllocator, *, score: LeaderScore, tenure: int,
                          account: AccountState, market: dict[str, Any] | None,
                          date: pd.Timestamp, local_open: bool, symbol: str) -> bool:
    """Current own-stock maturity proof required for an ordinary certificate."""
    return (score.mature and tenure >= self.cfg.leader_tenure_days
            and not account.candidate_tenure.get("ordinary_market_rearm_required", 0)
            and market is not None and market.get("as_of") == str(date.date())
            and (market.get("impulse") is True or local_open)
            and (market.get("impulse") is True
                 or account.replacement_tenure.get("ordinary_repair_maturity:" + symbol, 0)
                 >= self.cfg.leader_tenure_days)
            and symbol in market.get("credible_symbols", ()))


def is_consumed_repair_order(account: AccountState, order: PendingOrder) -> bool:
    reference = account.strategic_cash_rearm.consumed_order
    return (reference is not None and order.order_id == reference.order_id
            and order.event_id == reference.event_id)


def ordinary_core_entry(
    self: PortfolioAllocator, *, symbol: str, score: LeaderScore, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], account: AccountState, confirmation_days: int,
    certificate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = account.strategic_cash_rearm.consumed_order
    repair_pending = reference is not None and any(
        order.symbol == symbol and is_consumed_repair_order(account, order)
        for order in account.pending_orders
    )
    tenure = account.leader_tenure.get(symbol, 0)
    local_open = _ordinary_maturity_available(account, date, market)
    persistent_only = bool(market and market.get("persistent_mature_entry_open") is True
                           and not market.get("mature_entry_open") and not market.get("impulse"))
    credible = account.replacement_tenure.get("ordinary_repair_maturity:" + symbol, 0)
    if persistent_only and credible < self.cfg.leader_tenure_days:
        local_open = False
    if repair_pending:
        if account.strategic_cash_rearm.qualification_quorum == "MATURE_CORE":
            return ordinary_repair_entry(
                self, symbol=symbol, score=score, date=date, user_panel=user_panel,
                account=account, confirmation_days=confirmation_days, market=market,
            )
        certificate = None  # The real repair order still needs its strict own proof.
    elif certificate is None and _mature_core_eligible(
        self, score=score, tenure=tenure, account=account, market=market,
        date=date, local_open=local_open, symbol=symbol,
    ):
        certificate = {
            "qualification_route": "mature_core", "qualification_quorum": "ORDINARY_CORE",
            "required_confirmation": self.cfg.leader_tenure_days,
            "confirmations": {"leader_tenure": tenure}, "as_of": str(date.date()),
        }
        if market is not None and market.get("impulse") is not True:
            certificate["confirmations"]["credible_maturity"] = credible
        if persistent_only:
            certificate["confirmations"].update(credible_maturity=credible,
                                                 market_persistence=cast(dict[str, Any], market)["persistent_maturity"])
    return candidate_entry(
        self, symbol=symbol, score=score, date=date, user_panel=user_panel,
        account=account, confirmation_days=confirmation_days, certificate=certificate,
    )


def ordinary_repair_entry(
    self: PortfolioAllocator, *, symbol: str, score: LeaderScore, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], account: AccountState, confirmation_days: int,
    market: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep ordinary maturity evidence distinct from independent strategic proof."""
    strict = candidate_entry(self, symbol=symbol, score=score, date=date,
                             user_panel=user_panel, account=account,
                             confirmation_days=confirmation_days)
    if strict.get("block") == "READY":
        return strict
    tenure = account.leader_tenure.get(symbol, 0)
    credible = account.replacement_tenure.get("ordinary_repair_maturity:" + symbol, 0)
    if (market is None or market.get("as_of") != str(date.date())
            or market.get("repair_mature_entry_open") is not True
            or symbol not in market.get("credible_symbols", ()) or not score.mature
            or tenure < self.cfg.leader_tenure_days or credible < self.cfg.leader_tenure_days):
        return strict
    return candidate_entry(
        self, symbol=symbol, score=score, date=date, user_panel=user_panel,
        account=account, confirmation_days=confirmation_days,
        certificate={"qualification_route": "mature_core", "qualification_quorum": "MATURE_CORE",
                     "required_confirmation": self.cfg.leader_tenure_days,
                     "confirmations": {"leader_tenure": tenure, "credible_maturity": credible}, "as_of": str(date.date()),
                     "ordinary_market": dict(market)},
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
    credible = _credible_leaders(self, date, leaders, user_panel)
    impulse, missing = _market_conditions(
        opportunity=opportunity, risk=risk, credible_count=len(credible))
    long_cycle_fields = ("breadth20", "broad_ret20", "tech_ret20")
    long_cycle_complete = all(
        isinstance(risk.evidence.get(key), (int, float))
        and not isinstance(risk.evidence[key], bool)
        and math.isfinite(risk.evidence[key]) for key in long_cycle_fields
    )
    return {
        "as_of": str(date.date()), "confirmed": impulse, "impulse": impulse,
        "credible_symbols": credible, "missing_market_fields": missing,
        # Observation only; the account repair authorizer retains every risk guard.
        "repair_mature_entry_open": (
            not missing and long_cycle_complete
            and risk.evidence["breadth20"] >= self.cfg.high_confidence_entry_breadth
            and min(risk.evidence["broad_ret20"], risk.evidence["tech_ret20"])
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and max(risk.evidence["broad_ret120"], risk.evidence["tech_ret120"])
            > self.cfg.strategic_long_cycle_max_tech_ret120
            and risk.state is Risk.NORMAL
            and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        ),
        "mature_entry_open": (
            not missing and long_cycle_complete
            and risk.evidence["breadth20"] >= self.cfg.high_confidence_entry_breadth
            and min(risk.evidence["broad_ret20"], risk.evidence["tech_ret20"])
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and max(risk.evidence["broad_ret120"], risk.evidence["tech_ret120"])
            > self.cfg.strategic_long_cycle_max_tech_ret120
            and risk.state is Risk.NORMAL and not risk.freeze_new_risk
            and not risk.evidence.get("freeze_new_risk", False)
            and not risk.evidence.get("sentinel_freeze_new_risk", False)
            and opportunity in {Opportunity.TREND, Opportunity.STRONG_TREND}
        ),
    }
