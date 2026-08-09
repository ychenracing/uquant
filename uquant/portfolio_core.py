"""Portfolio invariants and shared causal allocation primitives."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .types import AccountState, LeaderScore, Lifecycle, RiskAssessment, Target


def current_weights(account: AccountState, prices: dict[str, float]) -> tuple[dict[str, float], float]:
    """Mark current positions and return normalized weights plus total equity."""
    market_value = sum(
        position.shares * prices.get(symbol, 0.0) for symbol, position in account.positions.items()
    )
    equity = account.cash + market_value
    if equity <= 0:
        raise RuntimeError("account equity must be positive")
    return (
        {
            symbol: position.shares * prices.get(symbol, 0.0) / equity
            for symbol, position in account.positions.items()
        },
        equity,
    )


def effective_n(weights: dict[str, float], correlations: pd.DataFrame | None = None) -> float:
    """Estimate effective diversification after concentration and correlation."""
    values = np.array([value for value in weights.values() if value > 0], dtype=float)
    if not len(values):
        return 0.0
    normalized = values / values.sum()
    naive = float(1.0 / np.square(normalized).sum())
    if correlations is None or correlations.empty:
        return naive
    off_diagonal = correlations.where(~np.eye(len(correlations), dtype=bool)).stack()
    median = float(off_diagonal.median()) if not off_diagonal.empty else 0.0
    return naive / (1.0 + max(0.0, median) * (naive - 1.0))


class PortfolioCore:
    """Hard constraints and state helpers shared by every portfolio policy."""

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _add_cooldown_complete(
        *,
        account: AccountState,
        frame: pd.DataFrame,
        date: pd.Timestamp,
        cooldown_sessions: int,
    ) -> bool:
        """Allow only causally spaced portfolio-level pyramid tranches."""
        entries = [
            pd.Timestamp(event["date"])
            for event in account.lifecycle_events
            if event.get("to") in {Lifecycle.ADD1.value, Lifecycle.ADD2.value}
            and event.get("date")
            and pd.Timestamp(event["date"]) <= date
        ]
        if not entries:
            return True
        elapsed = max(0, len(frame.loc[max(entries) : date]) - 1)
        return elapsed >= cooldown_sessions

    def _targets(
        self,
        *,
        proposed: dict[str, float],
        leaders: dict[str, LeaderScore],
        account: AccountState,
        lifecycle: Lifecycle,
        reason: str,
        lifecycles: dict[str, Lifecycle] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> tuple[Target, ...]:
        targets: list[Target] = []
        for symbol in sorted(set(account.positions) | set(proposed)):
            score = leaders.get(symbol)
            weight = min(self.cfg.max_symbol_weight, max(0.0, proposed.get(symbol, 0.0)))
            selected_lifecycle = (lifecycles or {}).get(symbol, lifecycle)
            selected_reason = (reasons or {}).get(symbol)
            if selected_reason is None:
                selected_reason = reason
            targets.append(
                Target(
                    symbol=symbol,
                    weight=weight,
                    lifecycle=selected_lifecycle.value,
                    alpha_score=score.score if score else 0.0,
                    confidence=score.confidence if score else 0.0,
                    reason=selected_reason,
                )
            )
        positive = [item for item in targets if item.weight > 1e-12]
        gross = sum(item.weight for item in positive)
        if len(positive) > self.cfg.max_positions or gross > 1.0 + 1e-8:
            weights = {item.symbol: item.weight for item in positive}
            raise RuntimeError(
                "allocator violated portfolio hard constraints: "
                f"positions={len(positive)}/{self.cfg.max_positions}, "
                f"gross={gross:.12f}/1.0, weights={weights}"
            )
        return tuple(targets)

    def _cap_underdiversified(
        self, proposed: dict[str, float], account: AccountState
    ) -> tuple[dict[str, float], bool]:
        if account.candidate_tenure.get("diversification_capped", 0):
            return proposed, False
        count = len(account.anchor_weights)
        if count == 2 and account.candidate_tenure.get("confirmed_anchor_pair", 0) == 1:
            if account.candidate_tenure.get("confirmed_pair_balanced", 0) == 0:
                ordered = list(account.anchor_weights)
                lead_weight = min(
                    self.cfg.max_symbol_weight,
                    self.cfg.tactical_rebound_weight,
                    self.cfg.recovery_target_gross,
                )
                confirmed = {
                    ordered[0]: lead_weight,
                    ordered[1]: max(
                        0.0,
                        self.cfg.max_gross - lead_weight,
                    ),
                }
                account.anchor_weights = dict(confirmed)
                account.candidate_tenure["confirmed_pair_balanced"] = 1
                return confirmed, True
            return proposed, False
        cap = self.cfg.one_anchor_gross_cap if count == 1 else self.cfg.two_anchor_gross_cap
        total = sum(proposed.values())
        if count not in {1, 2} or total <= cap or total <= 0:
            return proposed, False
        scaled = {symbol: weight * cap / total for symbol, weight in proposed.items()}
        account.anchor_weights = {
            symbol: scaled.get(symbol, weight) for symbol, weight in account.anchor_weights.items()
        }
        account.candidate_tenure["diversification_capped"] = 1
        return scaled, True

    def _liquidity_confirmed(self, frame: pd.DataFrame, date: pd.Timestamp) -> bool:
        amounts = frame.loc[:date, "amount"].tail(20)
        positive = amounts[amounts > 0]
        return len(positive) >= 10 and float(positive.median()) >= self.cfg.minimum_median_amount

    @staticmethod
    def _release_recovery_anchor(account: AccountState) -> None:
        """Release every lock owned by the current recovery cohort.

        Recovery anchors are a temporary bridge out of a shock, not a second
        portfolio.  Once that bridge is gone, retaining its target weights can
        make a later, unrelated leader portfolio sell back into symbols it no
        longer owns.  Keep the reset in one place so every graduation route has
        identical semantics.
        """
        account.anchor_weights.clear()
        account.recovery_anchor_date = ""
        account.candidate_tenure["recovery_cohort_locked"] = 0
        account.candidate_tenure["recovery_cohort_graduated"] = 1
        account.candidate_tenure["diversification_capped"] = 0
        account.candidate_tenure["confirmed_anchor_pair"] = 0
        account.candidate_tenure["confirmed_pair_balanced"] = 0
        account.candidate_tenure["recovery_substitution_pending"] = 0
        account.candidate_tenure["recovery_substitution_completed"] = 0

    def _release_stale_recovery_anchor(
        self,
        *,
        risk: RiskAssessment,
        account: AccountState,
        weights_now: dict[str, float],
    ) -> bool:
        """Forget an exited recovery cohort before allocating a new cycle.

        A live or pending anchor remains protected.  The reset is allowed only
        after the account has no anchor exposure and the shock/restoration
        state machine has finished, so it cannot cancel a causal recovery buy.
        """
        anchors = set(account.anchor_weights)
        if not anchors:
            return False
        held = any(weights_now.get(symbol, 0.0) > 1e-12 for symbol in anchors)
        pending_buy = any(
            order.symbol in anchors and order.side == "BUY"
            for order in account.pending_orders
        )
        protected = bool(anchors & set(account.protected_weights))
        shock_finished = risk.shock_state in {"NONE", "UNBACKED_COOLDOWN"}
        if (
            held
            or pending_buy
            or protected
            or risk.state.value not in {"NORMAL", "CAUTION"}
            or not shock_finished
        ):
            return False
        self._release_recovery_anchor(account)
        return True

    def _capacity_confirmed(
        self,
        frame: pd.DataFrame,
        date: pd.Timestamp,
        required_notional: float,
    ) -> bool:
        """Require a causal median-volume estimate to support the intended tranche."""
        amounts = frame.loc[:date, "amount"].tail(20)
        positive = amounts[amounts > 0]
        if len(positive) < 10:
            return False
        daily_capacity = float(positive.median()) * self.cfg.max_volume_participation
        return daily_capacity >= required_notional

    def _structure_ok(self, frame: pd.DataFrame, date: pd.Timestamp) -> bool:
        if date not in frame.index:
            return False
        row = frame.loc[date]
        close = scalar(row, "close")
        ma60 = scalar(row, f"ma{self.cfg.trend_medium}")
        return (
            math.isfinite(close)
            and math.isfinite(ma60)
            and close >= ma60
            and scalar(row, f"ret{self.cfg.trend_medium}", -1.0) > 0
            and self._liquidity_confirmed(frame, date)
        )
