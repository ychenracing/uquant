"""Mature leader confirmation and capital, funded by the shared account book."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

from ...features import scalar
from ...types import AttributionMechanism, Lifecycle, Opportunity, Risk

if TYPE_CHECKING:
    from ..allocation_book import AllocationBook


def observe_mature_cycle(book: AllocationBook, opportunity: Opportunity, market: dict[str, Any]) -> None:
    """Count distinct consecutive sessions of the leader cycle's own evidence."""
    account = book.account
    key, clock = 'leader_cycle_evidence', 'leader_cycle_observed_session'
    session = book.date.toordinal()
    previous = account.candidate_tenure.get(clock, 0)
    if previous > session:
        raise ValueError('leader cycle observations must be causal')
    sessions = book.policy._session_clock(book.user_panel, book.date)
    earlier = sessions[sessions < book.date]
    prior = earlier[-1].toordinal() if len(earlier) else 0
    legs = [book.risk.evidence.get(k) for k in ('broad_ret120', 'tech_ret120')]
    complete = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in legs)
    credible = market['credible_symbols']
    frozen = (book.risk.freeze_new_risk or book.risk.evidence.get('freeze_new_risk', False)
              or book.risk.state in {Risk.RISK_OFF, Risk.CRISIS})
    evidence = (complete and min(cast(float, v) for v in legs) >= .01 and opportunity is Opportunity.STRONG_TREND
                and book.risk.votes <= 1 and len(credible) >= 2)
    count = account.candidate_tenure.get(key, 0) if previous in {prior, session} else 0
    if frozen:
        count = 0
        account.candidate_tenure['leader_cycle_armed'] = 0
    elif previous != session:
        count = min(3, count + 1) if evidence else 0
    elif not evidence:
        count = 0
    if not frozen and (count >= 3 or market.get('impulse') is True):
        account.candidate_tenure['leader_cycle_armed'] = 1
    account.candidate_tenure.update({key: count, clock: session})
    market['leader_cycle_armed'] = bool(account.candidate_tenure.get('leader_cycle_armed')) and not frozen
    market['leader_cycle_confirmation'] = {'observed': count, 'required': 3}


def mature_cycle_weights(book: AllocationBook, symbols: list[str], opportunity: Opportunity) -> dict[str, float]:
    """Restore cohort seed, conviction and expansion amounts for qualified leaders."""
    policy, cfg, account = book.policy, book.policy.cfg, book.account
    if not symbols or not account.candidate_tenure.get('leader_cycle_armed'):
        return {}
    session = book.date.toordinal()
    marker = 'leader_capacity_observed_session'
    previous = account.candidate_tenure.get(marker, 0)
    if previous > session:
        raise ValueError('leader capacity observations must be causal')
    if previous != session:
        policy._dynamic_k(date=book.date, opportunity=opportunity, risk=book.risk,
                          candidates=[book.leaders[s] for s in symbols], user_panel=book.user_panel, account=account)
        account.candidate_tenure[marker] = session
    occupied = {s for s, w in book.committed.items() if w > 0} | book.owned
    selected = symbols[:max(0, account.dynamic_k - len(occupied))]
    if not selected:
        return {}
    gross = min(book.gross_cap, cfg.strong_trend_gross if opportunity is Opportunity.STRONG_TREND
                else cfg.trend_target_gross)
    if occupied:
        return {s: min(cfg.core_admission_weight, max(0.0, gross-sum(book.committed.values()))/len(selected))
                for s in selected}
    chasing = max(float(book.risk.evidence.get(k, 0.0)) for k in ('broad_ret5', 'tech_ret5')) >= cfg.add_index_chase_ret5
    high = (cfg.confidence_sizing_enabled and opportunity is Opportunity.STRONG_TREND
            and book.risk.state is Risk.NORMAL and not chasing and len(selected) >= 2
            and float(book.risk.evidence.get('trend_health', 0.0)) >= .70
            and all(book.leaders[s].score >= cfg.high_confidence_entry_score
                    and book.leaders[s].components.get('industry_breadth', 0.0) >= cfg.high_confidence_entry_breadth
                    and scalar(book.user_panel[s].loc[book.date], 'vol20', math.inf) <= cfg.high_confidence_entry_vol20
                    for s in selected))
    exceptional = high and min(book.leaders[s].score for s in selected) >= .90 and float(
        book.risk.evidence.get('trend_health', 0.0)) >= .82
    gross = min(gross, cfg.exceptional_entry_gross if exceptional else cfg.high_confidence_entry_gross
                if high else cfg.trend_entry_gross)
    account.candidate_tenure['confidence_sized_entry'] = int(high)
    conviction = policy._conviction_evidence_qualified(symbols=selected, leaders=book.leaders,
        user_panel=book.user_panel, date=book.date, high_confidence=high)
    shares = policy._conviction_shares(selected, book.leaders, evidence_qualified=conviction)
    cap = cfg.single_core_entry_cap if len(selected) == 1 else cfg.max_symbol_weight
    return {s: min(cap, gross*float(w)) for s, w in zip(selected, shares, strict=True)}


def add_mature_leaders(book: AllocationBook, opportunity: Opportunity) -> None:
    """Pyramid actual profitable tranches only with fresh permission and cash."""
    cfg, account = book.policy.cfg, book.account
    if (not account.candidate_tenure.get('leader_cycle_armed') or book.risk.state is not Risk.NORMAL
            or book.risk.freeze_new_risk or book.risk.evidence.get('freeze_new_risk', False)
            or opportunity is Opportunity.RECOVERY or account.pending_orders
            or account.candidate_tenure.get('ordinary_repair_capital_active')):
        return
    if max(float(book.risk.evidence.get(k, 0.0)) for k in ('broad_ret5', 'tech_ret5')) >= cfg.add_index_chase_ret5:
        return
    for symbol in sorted(account.positions):
        position = account.positions[symbol]
        if (symbol in book.owned or symbol in account.anchor_weights or position.shares <= 0
                or symbol not in book.leaders or not book.leaders[symbol].mature
                or book.proposed.get(symbol, 0.0) < book.weights_now.get(symbol, 0.0)
                or book.record(symbol).get('entry', {}).get('block') != 'READY'
                or not book.policy._add_cooldown_complete(account=account, frame=book.user_panel[symbol], date=book.date, cooldown_sessions=cfg.add_tranche_cooldown_sessions)):
            continue
        lifecycles = {t.lifecycle for t in position.tranches if t.shares > 0} or {position.lifecycle}
        mfe = max((max(t.mfe, book.prices[symbol]/max(t.avg_cost, 1e-12)-1) for t in position.tranches
                   if t.shares > 0), default=book.prices[symbol]/max(position.avg_cost, 1e-12)-1)
        if (Lifecycle.ADD1.value not in lifecycles and Lifecycle.ADD2.value not in lifecycles
                and not account.candidate_tenure.get('confidence_sized_entry') and mfe >= cfg.add1_min_mfe):
            stage, increment = Lifecycle.ADD1, cfg.add1_weight
        elif (Lifecycle.ADD1.value in lifecycles and Lifecycle.ADD2.value not in lifecycles
              and opportunity is Opportunity.STRONG_TREND and mfe >= cfg.add2_min_mfe):
            stage, increment = Lifecycle.ADD2, cfg.add2_weight
        else:
            continue
        if book.fund(symbol, min(cfg.max_symbol_weight, book.weights_now[symbol]+increment),
                     phase='LEADER_PYRAMID', minimum=cfg.min_trade_weight):
            book.mechanisms[symbol] = AttributionMechanism.LEADER_PYRAMID
            book.record(symbol)['leader_lifecycle'] = stage.value
            book.reasons[symbol] = f'{stage.value}: positive MFE with normal risk'
