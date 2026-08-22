"""Crash-repair admission preserved as one immutable decision stage."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...portfolio_leaders import LeaderPortfolioPolicy
from ...types import (
    AccountState,
    LeaderScore,
    Opportunity,
    Risk,
    RiskAssessment,
    Target,
)
from .targets import (
    _awaiting_recovery_cohort_targets,
    _controlled_oversold_rebound_targets,
    _locked_recovery_cohort_targets,
    _overextended_pullback_targets,
    _recovery_cohort_targets,
)


class RecoveryPortfolioPolicy(LeaderPortfolioPolicy):
    """Replace a damaged recovery secondary without creating a second book."""

    if TYPE_CHECKING:

        def _confirmed_recovery_gross(
            self,
            *,
            risk: RiskAssessment,
            account: AccountState,
        ) -> float: ...

        def _recovery_anchor_substitution(
            self,
            *,
            date: pd.Timestamp,
            risk: RiskAssessment,
            user_panel: dict[str, pd.DataFrame],
            leaders: dict[str, LeaderScore],
            account: AccountState,
            weights_now: dict[str, float],
            anchor_elapsed: int,
            risk_neutral_only: bool = False,
        ) -> tuple[Target, ...] | None: ...


RecoveryPortfolioPolicy.__module__ = "uquant.portfolio_recovery"


def _recovery_admission_targets(
    self: RecoveryPortfolioPolicy,
    *,
    date: pd.Timestamp,
    opportunity: Opportunity,
    risk: RiskAssessment,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    prices: dict[str, float],
    weights_now: dict[str, float],
    anchored_held: dict[str, float],
    bounded_recovery_repair: bool,
    freeze_active: bool,
    general_core_symbols: set[str],
    level1_recovery_repair: bool,
    risk_neutral_recovery_handoff: bool,
    risk_neutral_recovery_transfer: bool,
    tactical_recovery_market: bool,
    transitional_recovery_market: bool,
    weak_secular_market: bool,
) -> tuple[Target, ...] | None:
    has_general_core = not account.anchor_weights and bool(general_core_symbols)
    if (
        opportunity is Opportunity.RECOVERY
        and has_general_core
        and not risk_neutral_recovery_handoff
    ):
        recovery_hold = self._leader_targets(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            weights_now=weights_now,
            prices=prices,
        )
        if recovery_hold is not None:
            return recovery_hold

    if (
        not account.positions
        and not account.anchor_weights
        and account.candidate_tenure.get("tactical_active", 0) == 0
        and account.candidate_tenure.get("tactical_cooldown", 0) == 0
        and (
            not bool(risk.evidence.get("freeze_new_risk", False))
            or level1_recovery_repair
            or bounded_recovery_repair
        )
        and opportunity in {Opportunity.CHOPPY, Opportunity.WEAK}
        and risk.state.value in {"NORMAL", "CAUTION"}
    ):
        deep_recovery: list[tuple[LeaderScore, float, float]] = []
        rebound_evidence: list[
            tuple[LeaderScore, float, float, float, float, bool]
        ] = []
        fast_rebound: list[tuple[LeaderScore, float, float]] = []
        overextended_pullback = False
        required_notional = account.initial_cash * self.cfg.tactical_probe_weight * 0.90
        for symbol, score in leaders.items():
            if symbol not in user_panel or date not in user_panel[symbol].index:
                continue
            row = user_panel[symbol].loc[date]
            close = scalar(row, "close")
            ma120 = scalar(row, f"ma{self.cfg.trend_slow}")
            ret5 = scalar(row, "ret5", -1.0)
            ret20 = scalar(row, f"ret{self.cfg.trend_fast}", -1.0)
            ret60 = scalar(row, f"ret{self.cfg.trend_medium}", -1.0)
            ret120 = scalar(row, f"ret{self.cfg.trend_slow}", math.nan)
            ret1 = float(user_panel[symbol].loc[:date, "close"].pct_change(fill_method=None).iloc[-1])
            if (
                ret120 <= -0.35
                and ret20 >= -0.12
                and ret5 >= -0.06
                and ret1 <= -0.05
                and self._liquidity_confirmed(user_panel[symbol], date)
                and self._capacity_confirmed(user_panel[symbol], date, required_notional)
            ):
                deep_recovery.append((score, ret20, ret120))
            pullback_structure = bool(
                ret20 <= self.cfg.tactical_rebound_breadth_max_ret20
                and math.isfinite(close)
                and math.isfinite(ma120)
                and math.isfinite(ret120)
                and close >= ma120
                and self._liquidity_confirmed(user_panel[symbol], date)
                and self._capacity_confirmed(user_panel[symbol], date, required_notional)
            )
            current_reversal = bool(
                ret5 >= self.cfg.fast_v_recovery_return
                and ret60 >= self.cfg.tactical_rebound_min_ret60
            )
            qualified_current_reversal = bool(
                current_reversal
                and score.score >= self.cfg.high_confidence_entry_score
            )
            if (
                pullback_structure
                and ret120 > self.cfg.tactical_rebound_max_ret120
                and not qualified_current_reversal
            ):
                overextended_pullback = True
            shallow_rebound = bool(
                pullback_structure
                and ret120 <= self.cfg.tactical_rebound_max_ret120
            )
            if shallow_rebound:
                secular = bool(
                    score.confidence >= self.cfg.leader_min_confidence
                    and math.isfinite(ret120)
                    and ret120 >= 0.0
                    and score.score >= self.cfg.recovery_reserve_min_score
                )
                rebound_evidence.append(
                    (score, ret20, ret5, ret60, ret120, secular)
                )
            elif pullback_structure and qualified_current_reversal:
                secular = bool(
                    score.confidence >= self.cfg.leader_min_confidence
                    and ret120 >= 0.0
                    and score.score >= self.cfg.recovery_reserve_min_score
                )
                rebound_evidence.append(
                    (score, ret20, ret5, ret60, ret120, secular)
                )
            if (
                account.candidate_tenure.get("fast_v_recovery", 0) == 1
                and ret5 >= 0.10
                and ret20 < 0
                and math.isfinite(close)
                and math.isfinite(ma120)
                and close >= ma120
                and self._liquidity_confirmed(user_panel[symbol], date)
                and self._capacity_confirmed(user_panel[symbol], date, required_notional)
            ):
                fast_rebound.append((score, ret5, ret20))
        if (
            overextended_pullback
            and not rebound_evidence
            and not deep_recovery
            and not fast_rebound
        ):
            account.candidate_tenure["tactical_cooldown"] = max(
                account.candidate_tenure.get("tactical_cooldown", 0),
                self.cfg.tactical_overheat_cooldown_days,
            )
            account.candidate_tenure["tactical_overheat_cooldown"] = 1
            return _overextended_pullback_targets(
                self=self,
                leaders=leaders,
                account=account,
            )
        rebound_breadth = {
            score.industry
            for score, _, _, _, _, _ in rebound_evidence
            if score.industry != "unknown"
        }
        breadth_confirmed = bool(
            len(rebound_breadth) >= self.cfg.tactical_rebound_min_industries
        )
        rebound = [
            score
            for score, ret20, ret5, ret60, ret120, _ in rebound_evidence
            if (
                ret20 <= self.cfg.tactical_rebound_max_ret20
                and ret60 >= self.cfg.tactical_rebound_min_ret60
                and (
                    ret5 <= 0.0
                    or score.score >= self.cfg.high_confidence_entry_score
                )
            )
            or (
                ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                and ret60 >= self.cfg.tactical_rebound_oversold_min_ret60
            )
            or (
                ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                and ret60 >= self.cfg.recovery_transition_weak_leg_ret120
                and ret120 <= self.cfg.strategic_long_cycle_max_tech_ret120
                and score.score >= self.cfg.recovery_reserve_min_score
            )
            or (
                ret20 <= self.cfg.tactical_rebound_max_ret20
                and score.score >= self.cfg.high_confidence_entry_score
                and ret60 <= -self.cfg.recovery_crash_drawdown
            )
            or (
                ret5 >= self.cfg.fast_v_recovery_return
                and ret60 >= self.cfg.tactical_rebound_min_ret60
                and score.score >= self.cfg.high_confidence_entry_score
            )
            or breadth_confirmed
        ]
        secular_rebound = [
            score
            for score, ret20, ret5, ret60, ret120, secular in rebound_evidence
            if secular
            and (
                (
                    ret20 <= self.cfg.tactical_rebound_max_ret20
                    and ret60 >= self.cfg.tactical_rebound_min_ret60
                    and (
                        ret5 <= 0.0
                        or score.score >= self.cfg.high_confidence_entry_score
                    )
                )
                or (
                    ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                    and ret60 >= self.cfg.tactical_rebound_oversold_min_ret60
                )
                or (
                    ret5 <= self.cfg.tactical_rebound_oversold_max_ret5
                    and ret60 >= self.cfg.recovery_transition_weak_leg_ret120
                    and ret120
                    <= self.cfg.strategic_long_cycle_max_tech_ret120
                    and score.score >= self.cfg.recovery_reserve_min_score
                )
                or (
                    ret20 <= self.cfg.tactical_rebound_max_ret20
                    and score.score >= self.cfg.high_confidence_entry_score
                    and ret60 <= -self.cfg.recovery_crash_drawdown
                )
                or (
                    ret5 >= self.cfg.fast_v_recovery_return
                    and ret60 >= self.cfg.tactical_rebound_min_ret60
                    and score.score >= self.cfg.high_confidence_entry_score
                )
                or breadth_confirmed
            )
        ]
        if len(deep_recovery) < 2:
            deep_recovery = [
                item
                for item in deep_recovery
                if item[0].confidence >= self.cfg.leader_min_confidence
                and item[0].score >= self.cfg.recovery_reserve_min_score
            ]
        if not tactical_recovery_market:
            # A single-name route is still admissible after an observable
            # 35% collapse plus a fresh lower-limit-like washout.  That is
            # independent crash evidence, not a generic market dip.  The
            # shallower rebound/fast-V routes continue to require broad or
            # transitional six-month weakness.
            rebound = secular_rebound
            fast_rebound = []
        if transitional_recovery_market and not weak_secular_market:
            # A divergent index transition is a narrow exception for an
            # independently promotable, deep-crash repair.  It must not
            # turn the ordinary rebound or fast-V branches into dip-buy
            # shortcuts inside a market whose stronger leg is not
            # secularly weak.  The broad weak-market route intentionally
            # keeps its existing candidate set and shorter graduation.
            rebound = secular_rebound
            fast_rebound = []
        if deep_recovery or rebound or fast_rebound:
            if fast_rebound:
                pick = max(
                    fast_rebound,
                    key=lambda item: (
                        item[1],
                        item[2],
                        item[0].score,
                        item[0].symbol,
                    ),
                )[0]
                account.candidate_tenure["tactical_promotable"] = 1
                account.tactical_anchor_symbol = pick.symbol
            elif deep_recovery:
                # Recovery probes are meant to capture convexity after a
                # genuine crash. Rank by observable crash depth, then by
                # stabilization and leader quality; no future price enters.
                pick = max(
                    deep_recovery,
                    key=lambda item: (
                        -item[2],
                        item[1],
                        item[0].score,
                        item[0].symbol,
                    ),
                )[0]
                account.candidate_tenure["tactical_promotable"] = 1
                account.tactical_anchor_symbol = pick.symbol
            else:
                pick = max(rebound, key=lambda item: (item.score, item.symbol))
                fast_v_candidate = account.candidate_tenure.get("fast_v_recovery", 0) == 1
                account.candidate_tenure["tactical_promotable"] = int(fast_v_candidate)
                account.tactical_anchor_symbol = pick.symbol if fast_v_candidate else ""
            account.candidate_tenure["tactical_active"] = 1
            return _controlled_oversold_rebound_targets(
                self=self,
                pick=pick,
                risk=risk,
                leaders=leaders,
                account=account,
            )

    if opportunity is Opportunity.RECOVERY:
        proposed = dict(anchored_held)
        if account.candidate_tenure.get("recovery_cohort_locked", 0) == 1:
            pending_buys = {
                order.symbol
                for order in account.pending_orders
                if order.side == "BUY" and order.symbol in account.anchor_weights
            }
            unfinished = {
                symbol: min(self.cfg.max_symbol_weight, max(0.0, target_weight))
                for symbol, target_weight in account.anchor_weights.items()
                if symbol not in proposed or symbol in pending_buys
            }
            if risk.freeze_new_risk and not bounded_recovery_repair:
                unfinished = {}
            gross_budget = min(
                self.cfg.max_gross,
                self._confirmed_recovery_gross(risk=risk, account=account),
            )
            held_gross = sum(
                min(self.cfg.max_symbol_weight, max(0.0, weight)) for weight in proposed.values()
            )
            requested = sum(
                max(0.0, target_weight - proposed.get(symbol, 0.0))
                for symbol, target_weight in unfinished.items()
            )
            remaining = max(0.0, gross_budget - held_gross)
            scale = min(1.0, remaining / requested) if requested > 0 else 0.0
            proposed.update(
                {
                    symbol: proposed.get(symbol, 0.0)
                    + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale
                    for symbol, target_weight in unfinished.items()
                    if proposed.get(symbol, 0.0)
                    + max(0.0, target_weight - proposed.get(symbol, 0.0)) * scale
                    > 1e-12
                }
            )
            return _locked_recovery_cohort_targets(
                self=self,
                proposed=proposed,
                leaders=leaders,
                account=account,
            )
        candidates: list[LeaderScore] = []
        crash_depth: dict[str, float] = {}
        recovery_elapsed = 0
        for symbol, score in leaders.items():
            if symbol not in user_panel or date not in user_panel[symbol].index:
                continue
            frame = user_panel[symbol].loc[:date]
            row = frame.loc[date]
            close = scalar(row, "close")
            ma20 = scalar(row, f"ma{self.cfg.trend_fast}")
            ret120 = scalar(row, f"ret{self.cfg.trend_slow}", 0.0)
            previous_high = float(frame["close"].iloc[-11:-1].max()) if len(frame) >= 11 else float("nan")
            if (
                math.isfinite(close)
                and math.isfinite(ma20)
                and math.isfinite(previous_high)
                and close >= ma20
                and close >= previous_high
                and ret120 < 0
                and self._liquidity_confirmed(user_panel[symbol], date)
            ):
                candidates.append(score)
                crash_depth[symbol] = ret120
            elif symbol in account.anchor_weights and math.isfinite(ret120):
                crash_depth[symbol] = ret120
        deep_count = sum(value <= -0.30 for value in crash_depth.values())
        admission_depth = (
            -0.15 if deep_count >= 2 or (deep_count >= 1 and bool(account.anchor_weights)) else -0.30
        )
        candidates = [item for item in candidates if crash_depth.get(item.symbol, 0.0) <= admission_depth]
        if (
            weak_secular_market
            and not account.anchor_weights
            and (
                len(candidates) < 2
                or max(
                    float(risk.evidence.get("broad_ret60", -math.inf)),
                    float(risk.evidence.get("tech_ret60", -math.inf)),
                )
                < self.cfg.recovery_weak_market_min_index_ret60
            )
        ):
            candidates = []
        candidates.sort(key=lambda item: (crash_depth.get(item.symbol, 0.0), -item.score, item.symbol))
        continuous_freeze = bool(risk.evidence.get("freeze_new_risk", False))
        if (
            continuous_freeze
            and not level1_recovery_repair
            and not risk_neutral_recovery_transfer
        ):
            candidates = []
        if account.anchor_weights and account.recovery_anchor_date:
            recovery_elapsed = self._session_distance(
                self._session_clock(user_panel, date),
                account.recovery_anchor_date,
                date,
            )
            if recovery_elapsed > self.cfg.recovery_add_window_days:
                candidates = []
        if candidates:
            previous_members = set(account.anchor_weights)
            cohort = set(account.anchor_weights) | {item.symbol for item in candidates}
            selected = sorted(
                cohort,
                key=lambda symbol: (
                    crash_depth.get(symbol, 0.0),
                    -leaders[symbol].score,
                    symbol,
                ),
            )[: min(3, self.cfg.max_positions)]
            candidate_members = set(selected)
            independently_deep_empty_entry = bool(
                freeze_active
                and risk.state is Risk.CAUTION
                and not previous_members
                and len(candidate_members) == 1
                and deep_count == 1
                and all(
                    crash_depth.get(symbol, 0.0) <= admission_depth
                    for symbol in candidate_members
                )
            )
            if (
                candidate_members != previous_members
                and len(candidate_members) < min(3, self.cfg.max_positions)
                and not independently_deep_empty_entry
            ):
                admission_key = "recovery_admission:" + ",".join(sorted(candidate_members))
                for tenure_key in tuple(account.replacement_tenure):
                    if tenure_key.startswith("recovery_admission:") and tenure_key != admission_key:
                        account.replacement_tenure[tenure_key] = 0
                account.replacement_tenure[admission_key] = (
                    account.replacement_tenure.get(admission_key, 0) + 1
                )
                if account.replacement_tenure[admission_key] < self.cfg.recovery_member_confirm_days:
                    return _awaiting_recovery_cohort_targets(
                        self=self,
                        anchored_held=anchored_held,
                        leaders=leaders,
                        account=account,
                    )
            incumbent_order = [symbol for symbol in account.anchor_weights if symbol in selected]
            lead = incumbent_order[0] if incumbent_order else selected[0]
            secondaries = [symbol for symbol in selected if symbol != lead]
            proposed = {
                lead: min(
                    self.cfg.max_symbol_weight,
                    self.cfg.tactical_rebound_weight,
                    self.cfg.recovery_target_gross,
                )
            }
            if len(secondaries) == 1:
                # Reserve room for a third independently confirmed core.
                # This prevents a two-name interim cohort from being bought
                # to full gross and then immediately rebalanced when the
                # third member confirms a day or two later.
                proposed[secondaries[0]] = min(
                    0.20,
                    max(0.0, self.cfg.recovery_target_gross - proposed[lead]),
                )
            if len(secondaries) >= 2:
                independent_recovery_breadth = bool(
                    int(risk.evidence.get("risk_anchor_group_count", 0))
                    >= self.cfg.risk_anchor_min_groups
                )
                if previous_members or not independent_recovery_breadth:
                    # A live tactical anchor already survived its own
                    # causal probe. Expanding its cohort must not sell that
                    # owner merely to manufacture equal starting weights.
                    # The same ownership rule applies when three crash
                    # candidates appear together but independent market
                    # anchors do not yet cover multiple industries: name
                    # count alone is not confirmation of a broad repair.
                    secondary_weight = max(
                        0.0,
                        self.cfg.recovery_target_gross - proposed[lead],
                    ) / len(secondaries)
                    proposed.update(
                        {symbol: secondary_weight for symbol in secondaries}
                    )
                else:
                    # No member owns an earlier entry. Preserve the causal
                    # crash-depth winner as the conviction anchor while
                    # independent breadth diversifies the residual budget.
                    # Equal weighting erased this evidence and materially
                    # reduced continuous wealth without improving the hard
                    # drawdown line.
                    cohort_gross = self._confirmed_recovery_gross(
                        risk=risk,
                        account=account,
                    )
                    ambiguous_empty_cohort = bool(
                        not previous_members
                        and len(candidates) > len(selected)
                    )
                    if ambiguous_empty_cohort:
                        # More independently qualified crash breakouts than
                        # available seats creates real selection ambiguity.
                        # Unqualified padding cannot change this budget.
                        cohort_gross = min(
                            cohort_gross,
                            self.cfg.recovery_expansive_universe_gross,
                        )
                    if self.cfg.recovery_conviction_weighting_enabled:
                        lead_weight = min(
                            self.cfg.max_symbol_weight,
                            self.cfg.tactical_rebound_weight,
                            cohort_gross
                            * self.cfg.tactical_rebound_weight
                            / self.cfg.recovery_target_gross,
                        )
                        secondary_weight = max(
                            0.0,
                            cohort_gross - lead_weight,
                        ) / len(secondaries)
                        proposed = {
                            lead: lead_weight,
                            **{
                                symbol: secondary_weight
                                for symbol in secondaries
                            },
                        }
                    else:
                        member_weight = cohort_gross / len(selected)
                        proposed = {symbol: member_weight for symbol in selected}
            elif (
                len(secondaries) == 1
                and account.recovery_anchor_date
                and recovery_elapsed > self.cfg.recovery_add_window_days
            ):
                proposed[secondaries[0]] = max(
                    proposed[secondaries[0]],
                    self.cfg.recovery_target_gross - proposed[lead],
                )
                account.candidate_tenure["confirmed_anchor_pair"] = 1
            owner_targets = dict(proposed)
            if risk_neutral_recovery_transfer:
                # Transfer only already-deployed gross: a sell-funded
                # ownership handoff, never a risk-budget exception.
                handoff_gross = min(
                    sum(max(0.0, weight) for weight in weights_now.values()),
                    self.cfg.max_gross,
                    max(0.0, risk.target_gross_cap),
                )
                requested_gross = sum(
                    max(0.0, weight) for weight in proposed.values()
                )
                if requested_gross > 0:
                    scale = min(1.0, handoff_gross / requested_gross)
                    proposed = {
                        symbol: max(0.0, weight) * scale
                        for symbol, weight in proposed.items()
                        if max(0.0, weight) * scale > 1e-12
                    }
                    if risk_neutral_recovery_handoff:
                        account.protected_weights.clear()
                        account.candidate_tenure[
                            "post_shock_restore_complete"
                        ] = 0
                        account.candidate_tenure[
                            "post_shock_restore_submitted"
                        ] = 0
                    account.candidate_tenure["recovery_owner_handoff"] = 1
            account.anchor_weights = owner_targets
            if self.cfg.recovery_conviction_weighting_enabled:
                # Preserve which name causally led the recovery after the
                # temporary cohort weights graduate. Crisis reducers can
                # then retain the evidence owner without treating every
                # old recovery lot as equally informative.
                account.recovery_conviction_symbol = lead
            account.candidate_tenure["recovery_cohort_graduated"] = 0
            if len(selected) == 2 and all(crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected):
                account.candidate_tenure["confirmed_anchor_pair"] = 1
            if len(selected) == min(3, self.cfg.max_positions) and all(
                crash_depth.get(symbol, 0.0) <= -0.15 for symbol in selected
            ):
                account.candidate_tenure["recovery_cohort_locked"] = 1
            if not account.recovery_anchor_date:
                account.recovery_anchor_date = str(date.date())
                account.candidate_tenure["recovery_reserve_qualified"] = 0
                account.candidate_tenure["recovery_substitution_pending"] = 0
                account.candidate_tenure["recovery_substitution_completed"] = 0
            cohort_changed = set(selected) != previous_members
        else:
            for tenure_key in tuple(account.replacement_tenure):
                if tenure_key.startswith("recovery_admission:"):
                    account.replacement_tenure[tenure_key] = 0
            cohort_changed = False
        if proposed:
            if not cohort_changed:
                for symbol in account.anchor_weights:
                    if weights_now.get(symbol, 0.0) > 0:
                        proposed[symbol] = weights_now[symbol]
            capped = False
            if recovery_elapsed > self.cfg.recovery_add_window_days:
                proposed, capped = self._cap_underdiversified(proposed, account)
            return _recovery_cohort_targets(
                self=self,
                proposed=proposed,
                leaders=leaders,
                account=account,
                capped=capped,
                cohort_changed=cohort_changed,
            )
    return None
