"""Long-cycle strategic cohort policy, isolated from order construction."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .features import scalar
from .portfolio_core import PortfolioCore
from .types import AccountState, LeaderScore, Lifecycle, RiskAssessment, Target


class StrategicPortfolioPolicy(PortfolioCore):
    """Discover, protect, trail, and retire a causal strategic cohort."""

    def _initialize_strategic_cohort(
        self,
        *,
        date: pd.Timestamp,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        risk: RiskAssessment,
    ) -> None:
        """Discover and activate a persistent long-cycle cohort causally."""
        evaluated_key = "strategic_cohort_evaluated"
        if (
            account.candidate_tenure.get("strategic_cohort_active", 0) == 1
            or account.candidate_tenure.get("strategic_cohort_completed", 0) == 1
        ):
            return
        initial_check_key = "strategic_long_cycle_initial_check"
        long_cycle_open_key = "strategic_long_cycle_open"
        qualification_key = "strategic_cohort_qualification"
        route_key = "strategic_cohort_qualification_route"
        account.candidate_tenure[initial_check_key] = 1

        # Pool membership must never bypass the independent account-risk gate.
        unsafe_new_cohort = (
            risk.state.value in {"RISK_OFF", "CRISIS"}
            or (risk.state.value == "CAUTION" and risk.votes >= 2)
        )
        if unsafe_new_cohort:
            account.candidate_tenure[qualification_key] = 0
            account.candidate_tenure[route_key] = 0
            account.candidate_tenure[long_cycle_open_key] = 0
            return

        snapshots: dict[str, dict[str, float]] = {}
        for symbol, frame in user_panel.items():
            if date not in frame.index:
                continue
            history = frame.loc[:date, "close"].dropna()
            if len(history) < 241 or not self._liquidity_confirmed(frame, date):
                continue
            rolling240 = history / history.shift(240) - 1.0
            persistent = rolling240.dropna().tail(
                self.cfg.strategic_cohort_confirm_days
            )
            if persistent.empty:
                continue
            snapshots[symbol] = {
                "ret240": float(rolling240.iloc[-1]),
                "persistent_ret240": float(persistent.median()),
                "ret20": float(history.iloc[-1] / history.iloc[-21] - 1.0),
                "ret5": float(history.iloc[-1] / history.iloc[-6] - 1.0),
                "leader_score": leaders[symbol].score if symbol in leaders else 0.0,
            }
        if not snapshots:
            account.candidate_tenure[qualification_key] = 0
            account.candidate_tenure[long_cycle_open_key] = 0
            account.candidate_tenure[route_key] = 0
            return

        configured_symbols = list(self.cfg.strategic_cohort_symbols)
        configured_requested = all(
            symbol in user_panel for symbol in configured_symbols
        )
        configured_complete = all(
            symbol in snapshots for symbol in configured_symbols
        )
        account.candidate_tenure["strategic_configured_prior"] = int(
            configured_requested
        )
        if configured_requested and not configured_complete:
            account.candidate_tenure[qualification_key] = 0
            account.candidate_tenure[long_cycle_open_key] = 0
            account.candidate_tenure[route_key] = 0
            return
        long_cycle_candidates = sorted(
            (
                symbol
                for symbol, values in snapshots.items()
                if values["persistent_ret240"]
                >= self.cfg.strategic_cohort_min_ret240
            ),
            key=lambda symbol: (
                -snapshots[symbol]["persistent_ret240"],
                -snapshots[symbol]["leader_score"],
                -snapshots[symbol]["ret20"],
                symbol,
            ),
        )
        long_cycle_symbols = long_cycle_candidates[
            : min(3, self.cfg.max_positions)
        ]
        if configured_complete:
            long_cycle_symbols = configured_symbols
            raw_long_cycle = all(
                snapshots[symbol]["persistent_ret240"]
                >= self.cfg.strategic_cohort_min_ret240
                for symbol in configured_symbols
            )
        else:
            # When the explicit thesis cohort is absent, discover the strongest
            # persistent names in the requested universe.  A singleton remains
            # capped at 60%; larger discovered cohorts use full gross.
            raw_long_cycle = bool(long_cycle_symbols)
        if not raw_long_cycle:
            long_cycle_symbols = []
        # This is a causal eligibility gate, not a verdict frozen at the first
        # day of an arbitrary backtest window.  A secular move that develops
        # later must be investable once the same persistent evidence exists.
        account.candidate_tenure[long_cycle_open_key] = int(raw_long_cycle)

        reversal_candidates = sorted(
            (
                symbol
                for symbol, values in snapshots.items()
                if values["ret240"] <= self.cfg.strategic_reversal_max_ret240
                and values["ret5"] >= self.cfg.strategic_reversal_min_ret5
            ),
            key=lambda symbol: (
                -snapshots[symbol]["ret20"],
                -snapshots[symbol]["ret5"],
                -snapshots[symbol]["leader_score"],
                symbol,
            ),
        )
        if configured_complete:
            reversal_symbols = sorted(
                configured_symbols,
                key=lambda symbol: (-snapshots[symbol]["ret20"], symbol),
            )[: min(2, self.cfg.max_positions)]
            reversal_breadth = all(
                snapshots[symbol]["ret240"]
                <= self.cfg.strategic_reversal_max_ret240
                and snapshots[symbol]["ret5"]
                >= self.cfg.strategic_reversal_min_ret5
                for symbol in configured_symbols
            )
        else:
            reversal_symbols = reversal_candidates[: min(2, self.cfg.max_positions)]
            reversal_required = min(3, len(snapshots), self.cfg.max_positions)
            reversal_breadth = len(reversal_candidates) >= reversal_required
        synchronized_reversal = bool(
            len(reversal_symbols) >= 2
            and reversal_breadth
            and float(
                np.median(
                    [snapshots[symbol]["ret20"] for symbol in reversal_symbols]
                )
            )
            >= self.cfg.strategic_reversal_min_median_ret20
            and float(risk.evidence.get("tech_ret120", math.inf))
            <= self.cfg.strategic_reversal_max_tech_ret120
        )
        route = 2 if raw_long_cycle else 1 if synchronized_reversal else 0
        route_symbols = (
            long_cycle_symbols if route == 2 else reversal_symbols if route == 1 else []
        )
        signature = f"strategic_qualification:{route}:{','.join(route_symbols)}"
        if route:
            for key in tuple(account.replacement_tenure):
                if key.startswith("strategic_qualification:") and key != signature:
                    account.replacement_tenure[key] = 0
            account.replacement_tenure[signature] = (
                account.replacement_tenure.get(signature, 0) + 1
            )
            account.candidate_tenure[qualification_key] = account.replacement_tenure[
                signature
            ]
        else:
            account.candidate_tenure[qualification_key] = 0
        account.candidate_tenure[route_key] = route
        required_confirm_days = (
            self.cfg.strategic_reversal_confirm_days
            if synchronized_reversal
            else self.cfg.strategic_cohort_confirm_days
        )
        if (
            not route
            or account.candidate_tenure[qualification_key]
            < required_confirm_days
            or account.pending_orders
            or account.protected_weights
        ):
            return
        # A live recovery cohort already owns the same causal opportunity.  Do
        # not replace that lower-turnover, winner-preserving lifecycle with a
        # second label merely because the secular evidence later confirms.
        # Once those anchors are truly exited, the stale-anchor release above
        # makes this route available again.
        if account.anchor_weights and set(route_symbols).issubset(
            account.anchor_weights
        ):
            account.candidate_tenure["strategic_deferred_to_recovery"] = 1
            return
        # A persistently qualified secular cluster is also a causal graduation
        # signal for an old recovery cohort.  Clear every recovery-only lock so
        # the hand-off cannot leave stale anchors controlling later decisions.
        self._release_recovery_anchor(account)
        account.tactical_anchor_symbol = ""
        account.candidate_tenure["tactical_active"] = 0
        account.candidate_tenure["tactical_promotable"] = 0
        account.candidate_tenure["strategic_reversal_entry"] = int(
            synchronized_reversal
        )
        account.candidate_tenure["strategic_deferred_to_recovery"] = 0
        account.candidate_tenure[evaluated_key] = 1
        if synchronized_reversal:
            selected = route_symbols
            lead_weight = min(self.cfg.max_symbol_weight, self.cfg.max_gross)
            account.strategic_cohort_symbols = list(selected)
            account.strategic_cohort_targets = {
                selected[0]: lead_weight,
                selected[1]: max(0.0, self.cfg.max_gross - lead_weight),
            }
        else:
            weight = min(
                self.cfg.max_symbol_weight,
                self.cfg.max_gross / len(route_symbols),
            )
            account.strategic_cohort_symbols = list(route_symbols)
            account.strategic_cohort_targets = {
                symbol: weight for symbol in route_symbols
            }
        account.strategic_exit_bands.clear()
        account.strategic_active_bands.clear()
        account.strategic_restore_weights.clear()
        account.candidate_tenure["strategic_cohort_active"] = 1
        account.candidate_tenure["strategic_cohort_started"] = 0
        account.candidate_tenure["strategic_cohort_days"] = 0
        account.candidate_tenure["strategic_profit_armed"] = 0
        account.candidate_tenure["strategic_tail_armed"] = 1

    def _strategic_cohort_targets(
        self,
        *,
        date: pd.Timestamp,
        risk: RiskAssessment,
        user_panel: dict[str, pd.DataFrame],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        prices: dict[str, float],
        weights_now: dict[str, float],
    ) -> tuple[Target, ...] | None:
        """Run one persistent fixed cohort, then hand control back exactly once.

        Five neighboring ATR exit bands share one position and one final target.
        The bands smooth discrete signal dates without creating sleeves or orders;
        the execution planner still receives only one target weight per symbol.
        """
        self._initialize_strategic_cohort(
            date=date,
            user_panel=user_panel,
            leaders=leaders,
            account=account,
            risk=risk,
        )
        if account.candidate_tenure.get("strategic_cohort_active", 0) != 1:
            return None

        active_symbols = set(account.strategic_cohort_targets)
        held_cohort = {
            symbol
            for symbol in account.strategic_cohort_symbols
            if (position := account.positions.get(symbol)) is not None
            and position.shares > 0
        }
        if not active_symbols:
            if held_cohort:
                return self._targets(
                    proposed={},
                    leaders=leaders,
                    account=account,
                    lifecycle=Lifecycle.CORE,
                    reason="strategic cohort completed staged exit",
                )
            account.candidate_tenure["strategic_cohort_active"] = 0
            account.candidate_tenure["strategic_cohort_completed"] = 1
            account.candidate_tenure["strategic_profit_armed"] = 0
            account.candidate_tenure["strategic_tail_armed"] = 0
            account.strategic_restore_weights.clear()
            return None

        account.candidate_tenure["strategic_cohort_days"] = (
            account.candidate_tenure.get("strategic_cohort_days", 0) + 1
        )
        # A partially held cohort is not started: the missing members still
        # need targets.  Treating "any member held" as complete previously
        # stranded broad-universe runs in a one-name pseudo-cohort forever.
        if active_symbols and all(
            weights_now.get(symbol, 0.0) > 0 for symbol in active_symbols
        ):
            account.candidate_tenure["strategic_cohort_started"] = 1

        band_count = self.cfg.strategic_cohort_trail_bands
        thresholds = tuple(
            self.cfg.strategic_cohort_trail_atr
            + (index - (band_count - 1) / 2.0)
            * self.cfg.strategic_cohort_trail_spacing
            for index in range(band_count)
        )
        for symbol in sorted(active_symbols):
            position = account.positions.get(symbol)
            if position is None or position.shares <= 0:
                if symbol in account.strategic_exit_bands:
                    account.strategic_cohort_targets.pop(symbol, None)
                    account.strategic_exit_bands.pop(symbol, None)
                    account.strategic_active_bands.pop(symbol, None)
                continue
            frame = user_panel.get(symbol)
            if frame is None or date not in frame.index:
                continue
            row = frame.loc[date]
            close = scalar(row, "close")
            pnl = close / max(position.avg_cost, 1e-12) - 1.0
            if pnl <= self.cfg.strategic_cohort_disaster_stop:
                account.strategic_cohort_targets.pop(symbol, None)
                account.strategic_exit_bands.pop(symbol, None)
                account.strategic_active_bands.pop(symbol, None)
                continue
            atr = scalar(row, "atr", math.inf)
            structural_damage = (
                close < scalar(row, f"ma{self.cfg.trend_fast}")
                and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) < 0
            )
            peak_mfe = (
                position.highest_close / max(position.avg_cost, 1e-12) - 1.0
            )
            triggered = [
                peak_mfe >= self.cfg.strategic_cohort_profit_arm
                and structural_damage
                and math.isfinite(atr)
                and close <= position.highest_close - threshold * atr
                for threshold in thresholds
            ]
            if any(triggered) and symbol not in account.strategic_exit_bands:
                current = weights_now.get(symbol, 0.0)
                account.strategic_exit_bands[symbol] = [
                    current / band_count
                ] * band_count
                account.strategic_active_bands[symbol] = [False] * band_count
                account.candidate_tenure["strategic_profit_armed"] = 1
            if symbol not in account.strategic_exit_bands:
                continue
            bands = account.strategic_exit_bands[symbol]
            armed = account.strategic_active_bands[symbol]
            for index, signal in enumerate(triggered):
                if signal:
                    armed[index] = True
                    bands[index] = max(
                        0.0,
                        bands[index]
                        - self.cfg.strategic_cohort_exit_step / band_count,
                    )
            if sum(bands) <= 1e-12:
                account.strategic_cohort_targets.pop(symbol, None)

        active_symbols = set(account.strategic_cohort_targets)
        current_selected = {
            symbol: weights_now.get(symbol, 0.0)
            for symbol in active_symbols
            if weights_now.get(symbol, 0.0) > 0
        }
        current_gross = sum(current_selected.values())
        if account.strategic_exit_bands:
            account.strategic_restore_weights.clear()
        elif risk.target_gross_cap + 0.02 < current_gross:
            if current_gross > sum(account.strategic_restore_weights.values()):
                account.strategic_restore_weights = dict(current_selected)
        elif (
            risk.target_gross_cap >= self.cfg.max_gross - 1e-12
            and account.strategic_restore_weights
            and current_gross
            >= 0.95 * sum(account.strategic_restore_weights.values())
        ):
            account.strategic_restore_weights.clear()

        proposed = dict(current_selected)
        if (
            account.strategic_restore_weights
            and risk.target_gross_cap >= self.cfg.max_gross - 1e-12
        ):
            proposed = {
                symbol: account.strategic_restore_weights.get(
                    symbol, current_selected.get(symbol, 0.0)
                )
                for symbol in active_symbols
            }
        if account.candidate_tenure.get("strategic_cohort_started", 0) == 0:
            proposed = dict(account.strategic_cohort_targets)
        for symbol in active_symbols & set(account.strategic_exit_bands):
            proposed[symbol] = min(
                proposed.get(symbol, 0.0),
                sum(account.strategic_exit_bands[symbol]),
            )
        return self._targets(
            proposed=proposed,
            leaders=leaders,
            account=account,
            lifecycle=Lifecycle.CORE,
            reason="prequalified strategic leader cohort with staged profit protection",
        )
