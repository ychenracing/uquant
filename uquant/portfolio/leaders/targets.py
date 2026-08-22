"""Mechanical Task 8 leader owner extracted from the immutable policy."""

from __future__ import annotations

import math
from itertools import combinations
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ...features import scalar
from ...types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    RiskAssessment,
    Target,
)

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy


def _cap_opportunity_gross(
    self: LeaderPortfolioPolicy,
    *,
    proposed: dict[str, float],
    gross_cap: float,
    weights_now: dict[str, float],
    leaders: dict[str, LeaderScore],
    reasons: dict[str, str],
    opportunity: Opportunity,
) -> dict[str, float]:
    """Limit new opportunity risk without manufacturing incumbent sells.

    CHOPPY/WEAK are alpha-budget observations. Confirmed structural risk
    overlays own forced reductions. This distinction gives the continuous
    opportunity axis an economic hysteresis band: existing exposure may
    drift above the entry budget, while only proposed increments are
    sparsely removed.
    """
    capped = dict(proposed)
    increments = {
        symbol: max(0.0, weight - max(0.0, weights_now.get(symbol, 0.0)))
        for symbol, weight in capped.items()
    }
    baseline_total = sum(capped.values()) - sum(increments.values())
    allowed_total = max(gross_cap, baseline_total)
    excess = max(0.0, sum(max(0.0, value) for value in capped.values()) - allowed_total)
    if excess <= 1e-12:
        return capped
    symbols = tuple(sorted(symbol for symbol, weight in increments.items() if weight > 1e-12))
    feasible = [
        subset
        for size in range(1, len(symbols) + 1)
        for subset in combinations(symbols, size)
        if sum(increments[symbol] for symbol in subset) >= excess - 1e-12
    ]
    selected = min(
        feasible,
        key=lambda subset: (
            len(subset),
            sum(leaders[symbol].score if symbol in leaders else 0.0 for symbol in subset),
            -sum(increments[symbol] for symbol in subset),
            subset,
        ),
    )
    remaining = excess
    for symbol in sorted(
        selected,
        key=lambda item: (
            leaders[item].score if item in leaders else 0.0,
            -increments[item],
            item,
        ),
    ):
        reduction = min(increments[symbol], remaining)
        if reduction <= 1e-12:
            continue
        capped[symbol] = max(0.0, capped[symbol] - reduction)
        reasons[symbol] = f"{opportunity.value.lower()} opportunity gross contraction"
        remaining -= reduction
    if remaining > 1e-8:
        raise RuntimeError("leader opportunity cap could not be reconciled")
    return capped


def _leader_targets(
    self: LeaderPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    weights_now: dict[str, float],
    prices: dict[str, float],
) -> tuple[Target, ...] | None:
    """Build ordinary leader targets or decline when no admissible book exists."""

    if risk.state.value == "CRISIS":
        return None
    ranked = sorted(
        (
            item
            for item in leaders.values()
            if item.mature
            and item.confidence >= self.cfg.leader_min_confidence
            and self._structure_ok(user_panel[item.symbol], date)
        ),
        key=lambda item: (-item.score, item.symbol),
    )
    emerging = sorted(
        (
            item
            for item in leaders.values()
            if item.emerging and self._structure_ok(user_panel[item.symbol], date)
        ),
        key=lambda item: (-item.score, item.symbol),
    )
    held_symbols = {symbol for symbol, position in account.positions.items() if position.shares > 0}
    target_k = self._dynamic_k(
        date=date,
        opportunity=opportunity,
        risk=risk,
        candidates=ranked,
        user_panel=user_panel,
        account=account,
    )
    reasons: dict[str, str] = {}
    lifecycles: dict[str, Lifecycle] = {}
    mechanisms: dict[str, AttributionMechanism] = {}
    replaces_symbols: dict[str, str] = {}
    active = [symbol for symbol in account.active_leaders if symbol in held_symbols and symbol in leaders]
    for symbol in sorted(held_symbols - set(active)):
        position = account.positions[symbol]
        frame = user_panel.get(symbol)
        if frame is None or date not in frame.index or symbol not in leaders:
            continue
        row = frame.loc[date]
        proven_winner = (
            position.highest_close / max(position.avg_cost, 1e-12) - 1.0 >= 0.10
            and prices[symbol] >= position.avg_cost
            and scalar(row, "close") >= scalar(row, f"ma{self.cfg.trend_medium}")
        )
        if proven_winner:
            active.append(symbol)
            reasons[symbol] = "proven mature winner retained across rank drift"
    for symbol in sorted(held_symbols):
        position = account.positions[symbol]
        leader = leaders.get(symbol)
        if (
            position.lifecycle == Lifecycle.RECOVERY.value
            and leader is not None
            and leader.score >= self.cfg.leader_mature_score
            and leader.confidence >= self.cfg.leader_min_confidence
            and self._structure_ok(user_panel[symbol], date)
        ):
            if symbol not in active:
                active.append(symbol)
            position.lifecycle = Lifecycle.CORE.value
            for tranche in position.tranches:
                tranche.lifecycle = Lifecycle.CORE.value
            reasons[symbol] = "repaired recovery position graduated to core"
            mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_PROMOTION
    stable_k = max(0, min(account.dynamic_k, self.cfg.max_positions))
    if stable_k and len(active) > stable_k:
        proven: set[str] = set()
        for symbol in active:
            active_position = account.positions.get(symbol)
            if active_position is None:
                continue
            proven_winner = (
                active_position.highest_close / max(active_position.avg_cost, 1e-12) - 1.0 >= 0.10
                and prices[symbol] / max(active_position.avg_cost, 1e-12) - 1.0 >= 0
                and scalar(user_panel[symbol].loc[date], "close")
                >= scalar(
                    user_panel[symbol].loc[date],
                    f"ma{self.cfg.trend_medium}",
                )
            )
            if proven_winner:
                proven.add(symbol)
        keep_count = max(stable_k, len(proven))
        ranked_retention = sorted(
            active,
            key=lambda symbol: (
                -self._retention_score(symbol, leaders, account),
                symbol,
            ),
        )
        retained = sorted(
            proven,
            key=lambda symbol: (
                -self._retention_score(symbol, leaders, account),
                symbol,
            ),
        )
        retained.extend(symbol for symbol in ranked_retention if symbol not in proven)
        retained = retained[:keep_count]
        for symbol in set(active) - set(retained):
            reasons[symbol] = "dynamic K contraction after hysteresis"
            mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT
        active = retained
    available_ranked = [item for item in ranked if item.symbol not in active]
    while (
        len(active) < target_k
        and available_ranked
        and not risk.freeze_new_risk
        and risk.state.value != "RISK_OFF"
        and opportunity is not Opportunity.RECOVERY
    ):
        item = max(
            available_ranked,
            key=lambda candidate: (
                self._admission_utility(
                    candidate=candidate,
                    active=active,
                    leaders=leaders,
                    user_panel=user_panel,
                    date=date,
                    account=account,
                ),
                candidate.score,
                candidate.symbol,
            ),
        )
        active.append(item.symbol)
        available_ranked.remove(item)

    rotation_transfers: dict[str, float] = {}
    observed_rotation_key = ""
    if (
        active
        and len(active) >= target_k
        and ranked
        and not risk.freeze_new_risk
        and self._rotation_allowed(account, date, user_panel)
    ):
        challenger = next((item for item in ranked if item.symbol not in active), None)
        weakest = min(
            active,
            key=lambda symbol: (
                self._retention_score(symbol, leaders, account),
                symbol,
            ),
        )
        weakest_frame = user_panel[weakest]
        weakest_row = weakest_frame.loc[date]
        old_structure_broken = (
            scalar(weakest_row, "close") < scalar(weakest_row, f"ma{self.cfg.trend_fast}")
            and scalar(weakest_row, f"ret{self.cfg.trend_fast}", 0.0) < 0
        )
        old_position = account.positions.get(weakest)
        held_sessions = (
            len(weakest_frame.loc[pd.Timestamp(old_position.entry_date) : date])
            if old_position is not None and old_position.entry_date
            else 0
        )
        if challenger is not None and old_position is not None:
            peak_mfe = old_position.highest_close / max(old_position.avg_cost, 1e-12) - 1.0
            winner_penalty = min(0.20, 0.50 * max(0.0, peak_mfe))
            same_cluster_penalty = 0.15 if challenger.industry == leaders[weakest].industry else 0.0
            uncertainty_penalty = 0.05 * max(0.0, 1.0 - challenger.confidence)
            edge = (
                challenger.score
                - leaders[weakest].score
                - 0.01
                - winner_penalty
                - same_cluster_penalty
                - uncertainty_penalty
                + (0.08 if old_structure_broken else 0.0)
            )
            industry_handoff = self._industry_handoff(
                challenger=challenger,
                incumbent=leaders[weakest],
            )
            # Industry evidence confirms that the move is structurally
            # cross-group, but never discounts the ordinary replacement
            # edge.  A cheaper fast lane increased turnover and damaged
            # later-cycle wealth in continuous replays.
            required_edge = self.cfg.replacement_edge
            key = f"leader_rotation:{weakest}->{challenger.symbol}"
            observed_rotation_key = key
            account.replacement_tenure[key] = (
                account.replacement_tenure.get(key, 0) + 1
                if edge >= required_edge and old_structure_broken
                else 0
            )
            if (
                account.replacement_tenure[key] >= self.cfg.replacement_confirm_days
                and held_sessions >= self.cfg.min_hold_days
            ):
                active.remove(weakest)
                active.append(challenger.symbol)
                rotation_transfers[challenger.symbol] = min(
                    self.cfg.max_symbol_weight,
                    self.cfg.replacement_transfer_cap,
                    weights_now.get(weakest, 0.0),
                )
                account.rotation_dates.append(str(date.date()))
                account.replacement_events.append(
                    {
                        "signal_date": str(date.date()),
                        "old_symbol": weakest,
                        "new_symbol": challenger.symbol,
                        "old_close": prices[weakest],
                        "new_close": prices[challenger.symbol],
                        "edge": edge,
                        "industry_handoff": industry_handoff,
                    }
                )
                reasons[weakest] = f"rotation exit: {challenger.symbol} confirmed edge"
                reasons[challenger.symbol] = f"rotation entry: replaces {weakest}"
                lifecycles[challenger.symbol] = Lifecycle.CORE
                mechanisms[weakest] = AttributionMechanism.LEADER_ROTATION
                mechanisms[challenger.symbol] = AttributionMechanism.LEADER_ROTATION
                replaces_symbols[challenger.symbol] = weakest
                account.replacement_tenure[key] = 0

    for key in tuple(account.replacement_tenure):
        unscoped_rotation_key = "->" in key and ":" not in key
        if (
            key.startswith("leader_rotation:") or unscoped_rotation_key
        ) and key != observed_rotation_key:
            account.replacement_tenure[key] = 0

    # A leader can graduate to cash when no credible replacement exists.
    # This is a lifecycle exit, not a second risk controller: it requires
    # persistent loss of both maturity and price structure.
    for symbol in list(active):
        if self._leader_lifecycle_exit_confirmed(
            symbol=symbol,
            date=date,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
        ):
            active.remove(symbol)
            reasons[symbol] = "leader lifecycle exit: confirmed structural deterioration"
            mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_EXIT

    account.active_leaders = sorted(set(active), key=lambda symbol: (-leaders[symbol].score, symbol))
    proposed = {
        symbol: weights_now.get(symbol, 0.0)
        for symbol in account.active_leaders
        if weights_now.get(symbol, 0.0) > 0
    }
    new_core = [symbol for symbol in account.active_leaders if symbol not in proposed]
    if risk.freeze_new_risk:
        account.active_leaders = [symbol for symbol in account.active_leaders if symbol in held_symbols]
        new_core = []
    gross_cap = min(
        risk.target_gross_cap,
        self.cfg.strong_trend_gross
        if opportunity is Opportunity.STRONG_TREND
        else self.cfg.trend_target_gross
        if opportunity is Opportunity.TREND
        else self.cfg.weak_gross
        if opportunity is Opportunity.WEAK
        else self.cfg.choppy_target_gross,
    )
    projected_industry_cap = (
        gross_cap
        if account.candidate_tenure.get("evidence_concentration", 0) == 1
        else self.cfg.industry_weight_cap
    )
    satellite_reserve = sum(
        weights_now.get(symbol, 0.0)
        for symbol, position in account.positions.items()
        if position.shares > 0
        and position.lifecycle == Lifecycle.SATELLITE.value
        and symbol not in proposed
    )
    index_chase = (
        max(
            float(risk.evidence.get("broad_ret5", 0.0)),
            float(risk.evidence.get("tech_ret5", 0.0)),
        )
        >= self.cfg.add_index_chase_ret5
    )
    if not proposed and new_core:
        staged_handoff = account.candidate_tenure.get("leader_cycle_staged_handoff", 0) == 1
        high_confidence = bool(
            self.cfg.confidence_sizing_enabled
            and opportunity is Opportunity.STRONG_TREND
            and risk.state.value == "NORMAL"
            and not risk.freeze_new_risk
            and not index_chase
            and len(new_core) >= 2
            and float(risk.evidence.get("trend_health", 0.0)) >= 0.70
            and all(
                leaders[symbol].score >= self.cfg.high_confidence_entry_score
                and leaders[symbol].confidence >= self.cfg.leader_min_confidence
                and leaders[symbol].components.get("industry_breadth", 0.0)
                >= self.cfg.high_confidence_entry_breadth
                and scalar(user_panel[symbol].loc[date], "vol20", math.inf)
                <= self.cfg.high_confidence_entry_vol20
                for symbol in new_core
            )
        )
        exceptional = bool(
            high_confidence
            and min(leaders[symbol].score for symbol in new_core) >= 0.90
            and float(risk.evidence.get("trend_health", 0.0)) >= 0.82
        )
        account.candidate_tenure["confidence_sized_entry"] = int(high_confidence)
        configured_entry_gross = (
            self.cfg.exceptional_entry_gross
            if exceptional
            else self.cfg.high_confidence_entry_gross
            if high_confidence
            else self.cfg.trend_entry_gross
        )
        entry_gross = min(
            max(0.0, gross_cap - satellite_reserve),
            self.cfg.core_admission_weight if staged_handoff else configured_entry_gross,
        )
        conviction_qualified = self._conviction_evidence_qualified(
            symbols=new_core,
            leaders=leaders,
            user_panel=user_panel,
            date=date,
            high_confidence=high_confidence,
        )
        account.candidate_tenure["conviction_evidence_qualified"] = int(conviction_qualified)
        raw = self._conviction_shares(
            new_core,
            leaders,
            evidence_qualified=conviction_qualified,
        )
        for symbol, share in zip(new_core, raw, strict=True):
            entry_cap = (
                self.cfg.single_core_entry_cap if len(new_core) == 1 else self.cfg.max_symbol_weight
            )
            proposed[symbol] = min(entry_cap, entry_gross * float(share))
            lifecycles[symbol] = Lifecycle.CORE
            reasons.setdefault(
                symbol,
                "confirmed rearmed leader owner handoff"
                if staged_handoff
                else "confirmed mature leader core",
            )
        if proposed and staged_handoff:
            account.candidate_tenure["leader_cycle_staged_handoff"] = 0
            account.candidate_tenure["leader_cycle_handoff_epoch"] = (
                account.strategic_epochs_completed
            )
        for industry in {leaders[symbol].industry for symbol in proposed}:
            members = [symbol for symbol in proposed if leaders[symbol].industry == industry]
            industry_weight = sum(proposed[symbol] for symbol in members)
            if industry != "unknown" and industry_weight > projected_industry_cap:
                scale = projected_industry_cap / industry_weight
                for symbol in members:
                    proposed[symbol] *= scale
    elif new_core:
        available = max(
            0.0,
            gross_cap - satellite_reserve - sum(proposed.values()),
        )
        allocation = min(
            self.cfg.core_admission_weight,
            available / len(new_core) if new_core else 0.0,
        )
        for symbol in new_core:
            industry = leaders[symbol].industry
            industry_weight = sum(
                weight for held, weight in proposed.items() if leaders[held].industry == industry
            )
            admitted = min(
                rotation_transfers.get(symbol, allocation),
                available,
                max(0.0, projected_industry_cap - industry_weight),
            )
            if admitted > 0:
                proposed[symbol] = admitted
                available = max(0.0, available - admitted)
                lifecycles[symbol] = Lifecycle.CORE
                reasons.setdefault(symbol, "confirmed mature leader admission")
        for symbol in new_core:
            if symbol in proposed:
                continue
            industry = leaders[symbol].industry
            members = [item for item in account.active_leaders if leaders[item].industry == industry]
            incumbents = [item for item in members if item in proposed]
            industry_weight = sum(proposed[item] for item in incumbents)
            if (
                industry == "unknown"
                or leaders[symbol].components.get("unknown_industry", 0.0) >= 0.5
                or not incumbents
                or industry_weight <= 0
            ):
                continue
            scores = np.array(
                [max(0.01, leaders[item].score) for item in members],
                dtype=float,
            )
            scores /= scores.sum()
            redistributed = min(industry_weight, projected_industry_cap)
            for member_symbol, share in zip(members, scores, strict=True):
                proposed[member_symbol] = min(
                    self.cfg.max_symbol_weight,
                    redistributed * float(share),
                )
            lifecycles[symbol] = Lifecycle.CORE
            reasons[symbol] = "dynamic K expansion within industry cap"

    available = max(
        0.0,
        gross_cap - satellite_reserve - sum(proposed.values()),
    )
    for symbol in list(account.active_leaders):
        add_position = account.positions.get(symbol)
        if add_position is None or available < self.cfg.min_trade_weight:
            continue
        add_cooldown_complete = self._add_cooldown_complete(
            account=account,
            frame=user_panel[symbol],
            date=date,
            cooldown_sessions=self.cfg.add_tranche_cooldown_sessions,
        )
        tranche_lifecycles = {item.lifecycle for item in add_position.tranches if item.shares > 0}
        has_add1 = Lifecycle.ADD1.value in tranche_lifecycles
        has_add2 = Lifecycle.ADD2.value in tranche_lifecycles
        if not add_position.tranches:
            has_add1 = add_position.lifecycle == Lifecycle.ADD1.value
            has_add2 = add_position.lifecycle == Lifecycle.ADD2.value
        mfe = max(
            (
                max(
                    item.mfe,
                    prices[symbol] / max(item.avg_cost, 1e-12) - 1.0,
                )
                for item in add_position.tranches
                if item.shares > 0
            ),
            default=prices[symbol] / max(add_position.avg_cost, 1e-12) - 1.0,
        )
        industry = leaders[symbol].industry
        industry_weight = sum(
            weight for held, weight in proposed.items() if leaders[held].industry == industry
        )
        industry_room = max(0.0, projected_industry_cap - industry_weight)
        if (
            not has_add1
            and not has_add2
            and add_cooldown_complete
            and not index_chase
            and not risk.freeze_new_risk
            and account.candidate_tenure.get("confidence_sized_entry", 0) == 0
            and mfe >= self.cfg.add1_min_mfe
            and risk.state.value in {"NORMAL", "CAUTION"}
            and opportunity is not Opportunity.RECOVERY
            and proposed[symbol] < self.cfg.max_symbol_weight
        ):
            increment = min(
                self.cfg.add1_weight,
                available,
                industry_room,
                self.cfg.max_symbol_weight - proposed[symbol],
            )
            if increment <= 1e-12:
                continue
            proposed[symbol] = min(self.cfg.max_symbol_weight, proposed[symbol] + increment)
            available = max(
                0.0,
                gross_cap - satellite_reserve - sum(proposed.values()),
            )
            lifecycles[symbol] = Lifecycle.ADD1
            reasons[symbol] = "ADD1: positive MFE with normal risk"
            mechanisms[symbol] = AttributionMechanism.LEADER_PYRAMID
        elif (
            has_add1
            and not has_add2
            and add_cooldown_complete
            and not index_chase
            and not risk.freeze_new_risk
            and mfe >= self.cfg.add2_min_mfe
            and opportunity is Opportunity.STRONG_TREND
            and risk.state.value == "NORMAL"
            and proposed[symbol] < self.cfg.max_symbol_weight
        ):
            increment = min(
                self.cfg.add2_weight,
                available,
                industry_room,
                self.cfg.max_symbol_weight - proposed[symbol],
            )
            if increment <= 1e-12:
                continue
            proposed[symbol] = min(self.cfg.max_symbol_weight, proposed[symbol] + increment)
            available = max(
                0.0,
                gross_cap - satellite_reserve - sum(proposed.values()),
            )
            lifecycles[symbol] = Lifecycle.ADD2
            reasons[symbol] = "ADD2: high-confidence trend continuation"
            mechanisms[symbol] = AttributionMechanism.LEADER_PYRAMID

    satellites_now = [
        symbol
        for symbol, position in account.positions.items()
        if position.shares > 0
        and (
            position.lifecycle == Lifecycle.SATELLITE.value
            or any(
                item.shares > 0 and item.lifecycle == Lifecycle.SATELLITE.value
                for item in position.tranches
            )
        )
    ]
    for symbol in satellites_now:
        position = account.positions[symbol]
        held = len(user_panel[symbol].loc[pd.Timestamp(position.entry_date) : date])
        if leaders.get(symbol) and leaders[symbol].mature:
            proposed[symbol] = weights_now.get(symbol, 0.0)
            lifecycles[symbol] = Lifecycle.CORE
            reasons[symbol] = "satellite promoted to mature core"
            mechanisms[symbol] = AttributionMechanism.LEADER_LIFECYCLE_PROMOTION
            position.lifecycle = Lifecycle.CORE.value
            promoted_shares = 0
            for tranche in position.tranches:
                if tranche.lifecycle == Lifecycle.SATELLITE.value:
                    tranche.lifecycle = Lifecycle.CORE.value
                    promoted_shares += tranche.shares
            account.lifecycle_events.append(
                {
                    "date": str(date.date()),
                    "symbol": symbol,
                    "from": Lifecycle.SATELLITE.value,
                    "to": Lifecycle.CORE.value,
                    "shares": promoted_shares,
                    "reason": "challenger scout confirmed",
                }
            )
            if symbol not in account.active_leaders:
                account.active_leaders.append(symbol)
        elif held <= self.cfg.emerging_expiry_days and self._structure_ok(user_panel[symbol], date):
            proposed[symbol] = weights_now.get(symbol, 0.0)
            lifecycles[symbol] = Lifecycle.SATELLITE
            reasons[symbol] = "emerging leader satellite observation"
            mechanisms[symbol] = AttributionMechanism.CHALLENGER_SCOUT
        else:
            reasons[symbol] = "satellite expiry or failed confirmation"
            mechanisms[symbol] = AttributionMechanism.SATELLITE_EXPIRY
            account.satellite_entry_dates.pop(symbol, None)
    observed_scout_keys: set[str] = set()
    if (
        self.cfg.challenger_scout_enabled
        and not risk.freeze_new_risk
        and risk.state.value == "NORMAL"
        and opportunity in {Opportunity.STRONG_TREND, Opportunity.TREND}
        and len(proposed) < self.cfg.max_positions
    ):
        slots = min(
            self.cfg.max_satellites - len(satellites_now),
            self.cfg.max_positions - len(proposed),
        )
        active_industries = {
            leaders[symbol].industry
            for symbol in account.active_leaders
            if symbol in leaders and symbol in proposed
        }
        incumbent_scores = [
            leaders[symbol]
            for symbol in account.active_leaders
            if symbol in leaders and symbol in proposed
        ]
        idle_cash_weight = max(0.0, 1.0 - sum(weights_now.values()))
        incumbents_preserved = all(
            proposed.get(symbol, 0.0) + self.cfg.challenger_scout_incumbent_hysteresis
            >= weights_now.get(symbol, 0.0)
            for symbol, position in account.positions.items()
            if position.shares > 0 and symbol not in satellites_now
        )
        for item in emerging:
            if slots <= 0:
                break
            if item.symbol in proposed:
                continue
            weakest_score = min(
                (incumbent.score for incumbent in incumbent_scores),
                default=math.inf,
            )
            incumbent_fading = bool(
                incumbent_scores
                and any(
                    incumbent.components.get("acceleration", 0.5) < 0.50 for incumbent in incumbent_scores
                )
            )
            scout_evidence = bool(
                incumbent_scores
                and item.industry not in active_industries
                and item.industry != "unknown"
                and item.components.get("unknown_industry", 0.0) < 0.5
                and item.components.get("industry_rotation_strength", 0.0)
                >= self.cfg.industry_rotation_min_score
                and item.components.get("industry_breadth", 0.0) >= self.cfg.industry_rotation_breadth
                and item.score - weakest_score >= self.cfg.challenger_scout_score_edge
                and incumbent_fading
            )
            scout_key = f"challenger_scout:{item.industry}:{item.symbol}"
            observed_scout_keys.add(scout_key)
            account.replacement_tenure[scout_key] = (
                account.replacement_tenure.get(scout_key, 0) + 1 if scout_evidence else 0
            )
            if (
                account.replacement_tenure[scout_key] < self.cfg.challenger_scout_confirm_days
                or not incumbents_preserved
                or idle_cash_weight + 1e-12 < self.cfg.challenger_scout_weight
            ):
                continue
            scout_weight = min(
                self.cfg.challenger_scout_weight,
                idle_cash_weight,
                max(0.0, gross_cap - sum(proposed.values())),
            )
            if scout_weight < self.cfg.min_trade_weight:
                continue
            industry_weight = sum(
                weight for held, weight in proposed.items() if leaders[held].industry == item.industry
            )
            if industry_weight + scout_weight > projected_industry_cap:
                continue
            proposed[item.symbol] = scout_weight
            lifecycles[item.symbol] = Lifecycle.SATELLITE
            reasons[item.symbol] = "idle-cash challenger scout"
            mechanisms[item.symbol] = AttributionMechanism.CHALLENGER_SCOUT
            account.satellite_entry_dates[item.symbol] = str(date.date())
            account.scout_signature = scout_key
            account.scout_entry_date = str(date.date())
            idle_cash_weight -= scout_weight
            slots -= 1

    for key in tuple(account.replacement_tenure):
        if key.startswith("challenger_scout:") and key not in observed_scout_keys:
            account.replacement_tenure[key] = 0

    proposed = self._cap_opportunity_gross(
        proposed=proposed,
        gross_cap=gross_cap,
        weights_now=weights_now,
        leaders=leaders,
        reasons=reasons,
        opportunity=opportunity,
    )

    if not proposed and not held_symbols:
        return None
    for symbol in held_symbols - set(proposed):
        reasons.setdefault(symbol, "confirmed leader deterioration")
        mechanisms.setdefault(
            symbol,
            AttributionMechanism.LEADER_LIFECYCLE_EXIT,
        )
    return self._targets(
        proposed=proposed,
        leaders=leaders,
        account=account,
        lifecycle=Lifecycle.CORE,
        reason="mature leader lifecycle",
        origin_subsystem=OriginSubsystem.LEADER,
        mechanism=AttributionMechanism.LEADER_SELECTION,
        lifecycles=lifecycles,
        reasons=reasons,
        mechanisms=mechanisms,
        replaces_symbols=replaces_symbols,
    )
