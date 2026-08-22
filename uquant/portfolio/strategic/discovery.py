"""Mechanical Task 8 strategic owner extracted from the immutable policy."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pandas as pd

from ...portfolio_core import PortfolioCore
from ...types import (
    AccountState,
    LeaderScore,
    RiskAssessment,
    Target,
)


class StrategicPortfolioPolicy(PortfolioCore):
    """Discover, protect, trail, and retire a causal strategic cohort."""

    if TYPE_CHECKING:
        def _bounded_strategic_restore_risk_open(self, *, risk: RiskAssessment, account: AccountState) -> bool:
            ...

        @staticmethod
        def _retire_strategic_member(account: AccountState, symbol: str) -> None:
            ...

        def _initialize_strategic_cohort(self, *, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore], account: AccountState, risk: RiskAssessment, admission_open: bool=True) -> None:
            ...

        def _strategic_cohort_targets(self, *, date: pd.Timestamp, risk: RiskAssessment, user_panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore], account: AccountState, prices: dict[str, float], weights_now: dict[str, float], admission_open: bool=True) -> tuple[Target, ...] | None:
            ...


StrategicPortfolioPolicy.__module__ = "uquant.portfolio_strategic"


def _initialize_strategic_cohort(
    self: StrategicPortfolioPolicy,
    *,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    risk: RiskAssessment,
    admission_open: bool = True,
) -> None:
    """Discover and activate a persistent long-cycle cohort causally."""
    evaluated_key = "strategic_cohort_evaluated"
    if (
        not self.cfg.strategic_dynamic_enabled
        or account.candidate_tenure.get("strategic_cohort_active", 0) == 1
    ):
        return
    live_general_leaders = {
        symbol
        for symbol in account.active_leaders
        if (position := account.positions.get(symbol)) is not None
        and position.shares > 0
    }
    if live_general_leaders:
        # An operating leader book already has one lifecycle owner and a
        # confirmed replacement process. Strategic discovery must not
        # relabel it and silently bypass rotation hysteresis.
        return
    initial_check_key = "strategic_long_cycle_initial_check"
    long_cycle_open_key = "strategic_long_cycle_open"
    qualification_key = "strategic_cohort_qualification"

    def reset_qualification_streaks() -> None:
        """Clear all candidate streaks when the strategic gate is not admissible."""

        for key in tuple(account.replacement_tenure):
            if key.startswith("strategic_qualification:"):
                account.replacement_tenure[key] = 0

    account.candidate_tenure[initial_check_key] = 1

    # Pool membership must never bypass the independent account-risk gate.
    unsafe_new_cohort = (
        risk.freeze_new_risk
        or bool(risk.evidence.get("freeze_new_risk", False))
        or risk.state.value in {"RISK_OFF", "CRISIS"}
        or (risk.state.value == "CAUTION" and risk.votes >= 2)
    )
    if unsafe_new_cohort:
        reset_qualification_streaks()
        account.candidate_tenure[qualification_key] = 0
        account.candidate_tenure[long_cycle_open_key] = 0
        return

    if account.strategic_last_exit_date:
        last_exit = pd.Timestamp(account.strategic_last_exit_date)
        visible_sessions = sorted(
            {
                session
                for frame in user_panel.values()
                for session in frame.index
                if last_exit < session <= date
            }
        )
        if len(visible_sessions) < self.cfg.strategic_epoch_cooldown_sessions:
            reset_qualification_streaks()
            account.candidate_tenure[qualification_key] = 0
            account.candidate_tenure[long_cycle_open_key] = 0
            return

    snapshots: dict[str, dict[str, float]] = {}
    for symbol, frame in user_panel.items():
        if date not in frame.index:
            continue
        history = frame.loc[:date, "close"].dropna()
        if len(history) < 121 or not self._liquidity_confirmed(frame, date):
            continue
        rolling240 = history / history.shift(240) - 1.0
        persistent = rolling240.dropna().tail(self.cfg.strategic_cohort_confirm_days)
        leader = leaders.get(symbol)
        components = leader.components if leader is not None else {}
        transition_score = (
            0.20 * components.get("short_relative_strength", 0.0)
            + 0.20 * components.get("breakout_quality", 0.0)
            + 0.15 * components.get("acceleration", 0.0)
            + 0.15 * components.get("momentum60", 0.0)
            + 0.10 * components.get("relative_strength", 0.0)
            + 0.10 * components.get("industry_rotation_strength", 0.0)
            + 0.10 * components.get("trend_persistence", 0.0)
        )
        snapshots[symbol] = {
            "history": float(len(history)),
            "ret240": float(rolling240.iloc[-1]) if not persistent.empty else -math.inf,
            "persistent_ret240": float(persistent.median()) if not persistent.empty else -math.inf,
            "ret20": float(history.iloc[-1] / history.iloc[-21] - 1.0),
            "ret5": float(history.iloc[-1] / history.iloc[-6] - 1.0),
            "ret60": float(history.iloc[-1] / history.iloc[-61] - 1.0),
            "ret120": float(history.iloc[-1] / history.iloc[-121] - 1.0),
            "leader_score": leader.score if leader is not None else 0.0,
            "leader_confidence": leader.confidence if leader is not None else 0.0,
            "secular_score": components.get("secular_score", 0.0),
            "secular_confidence": components.get("secular_confidence", 0.0),
            "industry_confidence": components.get("industry_inference_confidence", 0.0),
            "momentum60": components.get("momentum60", 0.0),
            "momentum120": components.get("momentum120", 0.0),
            "relative_strength": components.get("relative_strength", 0.0),
            "short_relative_strength": components.get("short_relative_strength", 0.0),
            "trend_persistence": components.get("trend_persistence", 0.0),
            "breakout_quality": components.get("breakout_quality", 0.0),
            "transition_score": transition_score,
        }
    if not snapshots:
        reset_qualification_streaks()
        account.candidate_tenure[qualification_key] = 0
        account.candidate_tenure[long_cycle_open_key] = 0
        return
    def has_known_industry(symbol: str) -> bool:
        """Return whether a candidate has sufficiently confident industry evidence."""

        return bool(
            symbol in leaders
            and leaders[symbol].components.get("unknown_industry", 1.0) < 0.5
            and snapshots[symbol]["industry_confidence"] >= self.cfg.unknown_industry_confidence
        )

    established_candidates = sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["secular_score"] >= self.cfg.strategic_secular_min_score
            and values["secular_confidence"] >= self.cfg.strategic_secular_min_confidence
            and values["ret20"] >= self.cfg.strategic_long_cycle_min_ret20
            and values["ret60"] >= self.cfg.strategic_long_cycle_min_ret60
            and values["ret120"] >= self.cfg.strategic_long_cycle_min_ret120
            and values["leader_score"] >= self.cfg.leader_mature_score
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["momentum60"] >= self.cfg.strategic_current_factor_floor
            and values["momentum120"] >= self.cfg.strategic_current_factor_floor
            and values["relative_strength"] >= self.cfg.strategic_current_factor_floor
            and values["trend_persistence"] >= 2 / 3
            and has_known_industry(symbol)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["secular_score"],
            -snapshots[symbol]["secular_confidence"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["persistent_ret240"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )
    transition_candidates = sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["transition_score"] >= self.cfg.strategic_transition_min_score
            and values["leader_score"] >= self.cfg.leader_emerging_score
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["ret20"] > 0.0
            and values["ret60"] > 0.0
            and values["ret120"] > 0.0
            and values["relative_strength"] >= self.cfg.strategic_transition_min_component
            and values["breakout_quality"] >= self.cfg.strategic_transition_min_component
            and values["trend_persistence"] >= 2 / 3
            and has_known_industry(symbol)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["transition_score"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["ret60"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )
    # A lower-latency route observes a synchronized industry impulse before
    # 240-session evidence can possibly mature.  It remains a strategic
    # trend admission: the outer allocator requires NORMAL risk plus a
    # TREND/STRONG_TREND regime, every member must persist above its medium
    # trend, and the ordinary cohort confirmation/sizing/exit lifecycle is
    # reused unchanged.  There is no weak-index, ret5, ret240, or special
    # reversal bypass.
    impulse_candidates = sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["history"] >= self.cfg.strategic_transition_impulse_min_history
            and values["transition_score"] >= self.cfg.strategic_transition_impulse_min_score
            and values["leader_score"] >= self.cfg.strategic_transition_impulse_min_leader_score
            and values["secular_score"] >= self.cfg.strategic_transition_impulse_min_secular_score
            and values["secular_confidence"]
            >= self.cfg.strategic_transition_impulse_min_secular_confidence
            and values["leader_confidence"] >= self.cfg.leader_min_confidence
            and values["ret20"] >= self.cfg.strategic_transition_impulse_min_ret20
            and values["ret60"] >= self.cfg.strategic_transition_impulse_min_ret60
            and values["ret120"] >= self.cfg.strategic_transition_impulse_min_ret120
            and values["ret120"] <= self.cfg.strategic_transition_impulse_max_ret120
            and float(risk.evidence.get("broad_ret20", 0.0))
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and float(risk.evidence.get("tech_ret20", 0.0))
            >= self.cfg.strategic_transition_impulse_min_market_ret20
            and values["trend_persistence"] >= 2 / 3
            and has_known_industry(symbol)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["transition_score"],
            -snapshots[symbol]["ret20"],
            -snapshots[symbol]["leader_score"],
            symbol,
        ),
    )
    persistent_candidates = sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["persistent_ret240"] >= self.cfg.strategic_cohort_min_ret240
            and values["ret120"] <= self.cfg.strategic_persistent_max_ret120
            and has_known_industry(symbol)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["persistent_ret240"],
            -snapshots[symbol]["leader_score"],
            -snapshots[symbol]["ret20"],
            symbol,
        ),
    )
    reversal_candidates = sorted(
        (
            symbol
            for symbol, values in snapshots.items()
            if values["ret240"] <= self.cfg.strategic_reversal_max_ret240
            and values["ret5"] >= self.cfg.strategic_reversal_min_ret5
            and has_known_industry(symbol)
        ),
        key=lambda symbol: (
            -snapshots[symbol]["ret20"],
            -snapshots[symbol]["ret5"],
            -snapshots[symbol]["leader_score"],
            symbol,
        ),
    )

    # Industry agreement is evidence, not a quota.  When three independently
    # qualified names agree inside one industry, prefer that coherent group
    # over a mixed basket assembled from marginally higher individual ranks.
    # If no industry has enough qualified names, the established route still
    # falls back to global relative evidence so small/diverse universes remain
    # investable.
    def synchronized_groups(
        candidates: list[str],
        *,
        primary_component: str,
    ) -> list[list[str]]:
        """Rank coherent industry groups without imposing an industry quota."""

        by_industry: dict[str, list[str]] = {}
        for symbol in candidates:
            by_industry.setdefault(leaders[symbol].industry, []).append(symbol)
        groups = [
            symbols[: self.cfg.strategic_cohort_size]
            for symbols in by_industry.values()
            if len(symbols) >= self.cfg.strategic_cohort_min_size
        ]
        groups.sort(
            key=lambda symbols: (
                -float(pd.Series([snapshots[s][primary_component] for s in symbols]).median()),
                -float(pd.Series([snapshots[s]["leader_score"] for s in symbols]).median()),
                leaders[symbols[0]].industry,
            )
        )
        return groups

    high_quality_groups = synchronized_groups(
        transition_candidates,
        primary_component="transition_score",
    )
    established_groups = synchronized_groups(
        established_candidates,
        primary_component="secular_score",
    )
    impulse_groups = synchronized_groups(
        impulse_candidates,
        primary_component="transition_score",
    )
    persistent_groups = synchronized_groups(
        persistent_candidates,
        primary_component="persistent_ret240",
    )
    reversal_groups = synchronized_groups(
        reversal_candidates,
        primary_component="ret20",
    )
    impulse_groups.sort(
        key=lambda symbols: (
            -float(pd.Series([snapshots[s]["ret20"] for s in symbols]).median()),
            -float(pd.Series([snapshots[s]["leader_score"] for s in symbols]).median()),
            leaders[symbols[0]].industry,
        )
    )
    # A synchronized, currently accelerating group is the explicit
    # leadership hand-off route.  Prefer it to a mixed set of lagging
    # long-horizon winners once all independent transition gates agree.
    synchronized_reversal = bool(
        reversal_groups
        and float(
            pd.Series(
                [snapshots[symbol]["ret20"] for symbol in reversal_groups[0][:2]]
            ).median()
        )
        >= self.cfg.strategic_reversal_min_median_ret20
        and float(risk.evidence.get("tech_ret120", math.inf))
        <= self.cfg.strategic_reversal_max_tech_ret120
    )
    anchor_state_observed = "risk_anchor_symbols" in risk.evidence
    anchors_not_yet_armed = bool(
        anchor_state_observed and not risk.evidence.get("risk_anchor_symbols", [])
    )
    decisive_reversal_symbol: str | None = None
    decisive_reversal_pair: list[str] = []
    if anchor_state_observed and synchronized_reversal:
        decisive_reversal_pair = sorted(
            reversal_groups[0][:2],
            key=lambda symbol: (-leaders[symbol].score, symbol),
        )
        if len(decisive_reversal_pair) == 2:
            lead, runner = decisive_reversal_pair
            lead_evidence = snapshots[lead]
            runner_evidence = snapshots[runner]
            if (
                lead_evidence["leader_score"]
                - runner_evidence["leader_score"]
                >= self.cfg.strategic_dominant_min_leader_gap
                and lead_evidence["ret60"] - runner_evidence["ret60"]
                >= self.cfg.strategic_dominant_min_leader_gap
                and lead_evidence["leader_score"]
                >= self.cfg.strategic_secular_min_score
                and lead_evidence["trend_persistence"] >= 2 / 3
                and runner_evidence["trend_persistence"] < 2 / 3
                and lead_evidence["short_relative_strength"]
                >= self.cfg.strategic_transition_min_component
                and lead_evidence["breakout_quality"]
                >= self.cfg.strategic_transition_min_component
            ):
                decisive_reversal_symbol = lead
    # Durable 240-session industry evidence dominates shorter factor
    # admission even after dynamic risk anchors have armed. A proven
    # cluster remains stronger evidence than a merely recent winner.
    if decisive_reversal_symbol is not None:
        # Authorization for a dominant owner depends only on the pair's
        # synchronized economic evidence.  Unrelated pool members and the
        # configured universe-size route cannot suppress or create it.
        long_cycle_symbols = decisive_reversal_pair
        route = "reversal_industry"
    elif persistent_groups:
        long_cycle_symbols = persistent_groups[0]
        route = "persistent_industry"
    elif high_quality_groups:
        long_cycle_symbols = high_quality_groups[0]
        route = "transition"
    elif established_groups:
        long_cycle_symbols = established_groups[0]
        route = "established"
    elif len(established_candidates) >= self.cfg.strategic_cohort_min_size:
        long_cycle_symbols = established_candidates[: self.cfg.strategic_cohort_size]
        route = "established"
    elif impulse_groups:
        long_cycle_symbols = impulse_groups[0]
        route = "transition_impulse"
    elif anchor_state_observed and synchronized_reversal:
        long_cycle_symbols = reversal_groups[0][:2]
        route = "reversal_industry"
    elif len(established_candidates) >= 2:
        long_cycle_symbols = established_candidates[:2]
        route = "established"
    elif established_candidates:
        long_cycle_symbols = established_candidates[:1]
        route = "established"
    elif len(transition_candidates) >= 2:
        long_cycle_symbols = transition_candidates[:2]
        route = "transition"
    else:
        long_cycle_symbols = []
        route = "none"
    if (
        route == "established"
        and all(
            leaders[symbol].mature
            for symbol in long_cycle_symbols
            if symbol in leaders
        )
        and float(
            pd.Series(
                [snapshots[symbol]["persistent_ret240"] for symbol in long_cycle_symbols]
            ).median()
        )
        < self.cfg.strategic_established_min_median_ret240
    ):
        # Already-mature leaders need durable median persistence; emerging
        # candidates keep their separate current-factor confirmation.
        long_cycle_symbols = []
        route = "none"
    evidence_route = route
    admission_state = (
        "SECULAR"
        if route in {"established", "persistent_industry"}
        else "EMERGING_SECULAR"
        if route in {"transition", "transition_impulse", "reversal_industry"}
        else "NONE"
    )
    persistent_route_hard_evidence = bool(
        route == "persistent_industry"
        and long_cycle_symbols
        and all(
            snapshots[symbol]["persistent_ret240"]
            >= self.cfg.strategic_cohort_min_ret240
            for symbol in long_cycle_symbols
        )
    )
    long_cycle_industries = {
        leaders[symbol].industry
        for symbol in long_cycle_symbols
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    independent_risk_coverage = bool(
        int(
            risk.evidence.get(
                "risk_anchor_group_count",
                self.cfg.strategic_cohort_min_size,
            )
        )
        >= self.cfg.strategic_cohort_min_size
    )
    synchronized_before_anchor_arm = bool(
        anchors_not_yet_armed
        and (persistent_route_hard_evidence or route == "reversal_industry")
        and len(long_cycle_industries) == 1
        and (
            len(long_cycle_symbols) >= self.cfg.strategic_cohort_min_size
            or (admission_state == "EMERGING_SECULAR" and bool(reversal_groups))
        )
    )
    independent_market_confirmation = bool(
        float(
            risk.evidence.get(
                "breadth20",
                self.cfg.high_confidence_entry_breadth,
            )
        )
        >= self.cfg.high_confidence_entry_breadth
        and float(risk.evidence.get("broad_ret20", 0.0))
        >= self.cfg.strategic_transition_impulse_min_market_ret20
        and float(risk.evidence.get("tech_ret20", 0.0))
        >= self.cfg.strategic_transition_impulse_min_market_ret20
        and max(
            float(risk.evidence.get("broad_ret120", 0.0)),
            float(risk.evidence.get("tech_ret120", 0.0)),
        )
        > self.cfg.recovery_transition_weak_leg_ret120
        # The cohort evidence is name-specific, while this independent
        # market guard prevents spending an epoch at a broad blow-off top.
        # A later consolidation can still qualify with the same causal
        # long-cycle evidence.
        and max(
            float(
                risk.evidence.get(
                    "broad_ret120",
                    risk.evidence.get("tech_ret120", math.inf),
                )
            ),
            float(risk.evidence.get("tech_ret120", math.inf)),
        )
        <= self.cfg.strategic_long_cycle_max_tech_ret120
    )
    cohort_count = len(long_cycle_symbols)
    negative_long_cycle_backed = bool(
        all(
            snapshots[symbol]["ret120"] > 0.0
            for symbol in long_cycle_symbols
        )
        or anchor_state_observed
        or synchronized_reversal
        or route == "transition_impulse"
    )
    partial_cohort_supported = bool(synchronized_reversal)
    cohort_quality = bool(
        cohort_count >= 3
        or (
            cohort_count == 2
            and partial_cohort_supported
            and (
                synchronized_reversal
                or all(
                    snapshots[symbol]["leader_score"]
                    >= self.cfg.strategic_two_name_min_score
                    for symbol in long_cycle_symbols
                )
            )
        )
        or (
            cohort_count == 1
            and partial_cohort_supported
            and snapshots[long_cycle_symbols[0]]["leader_score"]
            >= self.cfg.strategic_one_name_min_score
            and snapshots[long_cycle_symbols[0]]["secular_score"]
            >= self.cfg.strategic_one_name_min_secular_score
            and snapshots[long_cycle_symbols[0]]["leader_confidence"]
            >= self.cfg.leader_min_confidence
        )
    )
    raw_long_cycle = bool(
        cohort_quality
        and negative_long_cycle_backed
        # A fully armed, cross-industry sentinel basket is the ordinary
        # independent gate. During its initial hysteresis only, three
        # independently qualified names agreeing inside one known
        # industry may confirm from their synchronized evidence. A mixed
        # basket never receives this start-up exception.
        and (
            synchronized_before_anchor_arm
            or (
                independent_risk_coverage
                and independent_market_confirmation
            )
        )
    )
    if not raw_long_cycle:
        long_cycle_symbols = []
    # This is a causal eligibility gate, not a verdict frozen at the first
    # day of an arbitrary backtest window.  A secular move that develops
    # later must be investable once the same persistent evidence exists.
    account.candidate_tenure[long_cycle_open_key] = int(raw_long_cycle)

    route_symbols = long_cycle_symbols
    # Rank order may move by a few basis points during confirmation; cohort
    # identity is its route plus economic membership, not that transient
    # ordering.  Canonicalizing prevents a stable group from resetting its
    # streak every day while still resetting on any member or route change.
    signature_body = ",".join(
        f"{symbol}:{leaders[symbol].industry if symbol in leaders else 'unknown'}"
        for symbol in sorted(route_symbols)
    )
    signature = (
        f"strategic_qualification:{admission_state}:{signature_body}"
        f":evidence={evidence_route}"
    )
    previous = set(account.strategic_previous_symbols)
    same_members = bool(previous) and set(route_symbols) == previous
    new_members = len(set(route_symbols) - previous)
    # A completed cohort is not permanently banned.  After the ordinary
    # cooldown has reset every qualification streak, the same economic
    # members may earn a new epoch from fresh causal evidence.  A different
    # cohort must still clear the configured membership-change threshold.
    if previous and not same_members and new_members < self.cfg.strategic_epoch_min_symbol_change:
        route_symbols = []
        account.candidate_tenure[qualification_key] = 0
    if route_symbols:
        for key in tuple(account.replacement_tenure):
            if key.startswith("strategic_qualification:") and key != signature:
                account.replacement_tenure[key] = 0
        account.replacement_tenure[signature] = account.replacement_tenure.get(signature, 0) + 1
        account.candidate_tenure[qualification_key] = account.replacement_tenure[signature]
    else:
        reset_qualification_streaks()
        account.candidate_tenure[qualification_key] = 0
    required_confirm_days = (
        self.cfg.strategic_cohort_confirm_days
        if synchronized_reversal or len(route_symbols) >= self.cfg.strategic_cohort_size
        else self.cfg.strategic_two_name_confirm_days
        if len(route_symbols) == 2
        else self.cfg.strategic_one_name_confirm_days
        if len(route_symbols) == 1
        else self.cfg.strategic_cohort_confirm_days
    )
    route_admission_open = bool(
        admission_open
        or (
            (persistent_route_hard_evidence or synchronized_reversal)
            and anchors_not_yet_armed
            and synchronized_before_anchor_arm
        )
    )
    if (
        not route_symbols
        or not route_admission_open
        or account.candidate_tenure[qualification_key] < required_confirm_days
        or account.pending_orders
        or account.protected_weights
    ):
        return
    # A locked recovery cohort owns the whole deployed risk budget, not
    # only symbols that happen to overlap a later secular route.  Do not
    # replace that lower-turnover, winner-preserving lifecycle with a
    # second label merely because independent secular evidence confirms.
    # An unlocked old anchor may still hand off when the opportunity is
    # disjoint; once a locked cohort graduates or exits this route becomes
    # available again.
    live_anchor_symbols = {
        symbol
        for symbol in account.anchor_weights
        if account.positions.get(symbol) is not None and account.positions[symbol].shares > 0
    }
    locked_recovery_owner = bool(
        live_anchor_symbols
        and account.candidate_tenure.get("recovery_cohort_locked", 0) == 1
    )
    if locked_recovery_owner or live_anchor_symbols & set(route_symbols):
        account.candidate_tenure["strategic_deferred_to_recovery"] = 1
        return
    # A persistently qualified secular cluster is also a causal graduation
    # signal for an old recovery cohort.  Clear every recovery-only lock so
    # the hand-off cannot leave stale anchors controlling later decisions.
    self._release_recovery_anchor(account)
    account.tactical_anchor_symbol = ""
    account.candidate_tenure["tactical_active"] = 0
    account.candidate_tenure["tactical_promotable"] = 0
    account.candidate_tenure["strategic_deferred_to_recovery"] = 0
    account.candidate_tenure[evaluated_key] = 1
    weighted_symbols = sorted(
        route_symbols,
        key=lambda symbol: (-leaders[symbol].score, symbol),
    )
    dominant_symbol = (
        decisive_reversal_symbol
        if route == "reversal_industry" and len(weighted_symbols) == 2
        else None
    )
    account.strategic_cohort_symbols = (
        [dominant_symbol] if dominant_symbol is not None else list(weighted_symbols)
    )
    if dominant_symbol is not None:
        account.strategic_cohort_targets = {
            dominant_symbol: self.cfg.strategic_dominant_max_weight
        }
    elif len(route_symbols) == 1:
        account.strategic_cohort_targets = {
            weighted_symbols[0]: min(
                self.cfg.max_symbol_weight,
                self.cfg.strategic_one_name_gross,
            )
        }
    elif len(route_symbols) == 2:
        cohort_gross = min(self.cfg.max_gross, self.cfg.strategic_two_name_gross)
        lead_weight = min(self.cfg.max_symbol_weight, 0.60 * cohort_gross)
        account.strategic_cohort_targets = {
            weighted_symbols[0]: lead_weight,
            weighted_symbols[1]: max(0.0, cohort_gross - lead_weight),
        }
    else:
        weight = min(
            self.cfg.max_symbol_weight,
            self.cfg.max_gross / len(route_symbols),
        )
        account.strategic_cohort_targets = {symbol: weight for symbol in weighted_symbols}
    account.strategic_exit_bands.clear()
    account.strategic_active_bands.clear()
    account.strategic_restore_weights.clear()
    account.candidate_tenure["strategic_damage_guard_active_epoch"] = 0
    account.candidate_tenure["strategic_external_risk_epoch"] = 0
    account.candidate_tenure["strategic_cohort_active"] = 1
    account.candidate_tenure["strategic_cohort_completed"] = 0
    account.candidate_tenure["strategic_cohort_started"] = 0
    account.candidate_tenure["strategic_cohort_days"] = 0
    account.candidate_tenure["strategic_profit_armed"] = 0
    account.candidate_tenure["strategic_tail_armed"] = 1
    account.strategic_epoch += 1
    account.candidate_tenure["strategic_early_cycle_epoch"] = (
        account.strategic_epoch
        if route_symbols
        and all(
            snapshots[symbol]["persistent_ret240"]
            >= self.cfg.strategic_cohort_min_ret240
            and snapshots[symbol]["ret120"] < 0.0
            for symbol in route_symbols
        )
        else 0
    )
    account.candidate_tenure["strategic_dominant_epoch"] = (
        account.strategic_epoch if dominant_symbol is not None else 0
    )
    account.candidate_tenure["strategic_dominant_profit_lock_epoch"] = 0
    account.strategic_candidate_signature = signature
