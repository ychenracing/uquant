"""Portfolio invariants and shared causal allocation primitives."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .types import (
    AccountState,
    AttributionMechanism,
    LeaderScore,
    Lifecycle,
    OriginSubsystem,
    RiskAssessment,
    Target,
)


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


def strategic_dominant_symbol(account: AccountState) -> str | None:
    """Return the sole owner of a currently evidenced dominant epoch."""
    if (
        account.candidate_tenure.get("strategic_cohort_active", 0) != 1
        or len(account.strategic_cohort_targets) != 1
    ):
        return None
    symbol = next(iter(account.strategic_cohort_targets))
    if symbol not in account.strategic_cohort_symbols:
        return None
    realized_dominant = bool(
        account.strategic_epoch > 0
        and account.candidate_tenure.get("strategic_dominant_epoch", -1)
        == account.strategic_epoch
    )
    if realized_dominant:
        return symbol
    grant = account.strategic_grant
    pending_epochs = [
        epoch
        for epoch in account.strategic_epochs
        if not epoch.terminal
        and epoch.realized_status == "PROBE"
        and epoch.qualification_quorum == "FULL_COHORT"
        and epoch.owner_symbol == symbol
        and grant is not None
        and epoch.grant_id == grant.grant_id
    ]
    pending_full_cohort = bool(
        len(pending_epochs) == 1
        and not account.active_strategic_epoch_id
        and grant is not None
        and grant.candidate_symbol == symbol
        and grant.qualification_quorum == "FULL_COHORT"
        and grant.status not in {"COMPLETED", "EXPIRED", "CANCELLED"}
        and account.candidate_tenure.get("strategic_dominant_epoch", -1)
        == account.strategic_epoch + 1
    )
    return symbol if pending_full_cohort else None


def symbol_weight_cap(cfg: SystemConfig, account: AccountState, symbol: str) -> float:
    """Keep the ordinary 60% cap except for one validated dominant owner."""
    return (
        cfg.strategic_dominant_max_weight
        if strategic_dominant_symbol(account) == symbol
        else cfg.max_symbol_weight
    )


def _unknown_industry_scale(
    *,
    cfg: SystemConfig,
    proposed: dict[str, float],
    leaders: dict[str, LeaderScore],
) -> float:
    low_confidence_unknowns = {
        symbol for symbol, score in leaders.items() if score.components.get("unknown_industry", 0.0) >= 0.5
    }
    unknown_gross = sum(max(0.0, proposed.get(symbol, 0.0)) for symbol in low_confidence_unknowns)
    return min(1.0, cfg.unknown_industry_weight_cap / unknown_gross) if unknown_gross > 0 else 1.0


class PortfolioCore:
    """Hard constraints and state helpers shared by every portfolio policy."""

    def __init__(self, cfg: SystemConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _reason_code(reason: str) -> str:
        """Convert human explanations to a stable, finite attribution code."""
        normalized = reason.lower()
        if "rotation" in normalized or "replaces" in normalized:
            return "rotation"
        if "satellite" in normalized or "scout" in normalized:
            return "satellite_expiry" if "expiry" in normalized else "challenger_scout"
        if "strategic" in normalized:
            return "strategic_tail" if "exit" in normalized else "strategic_cohort"
        if "recovery" in normalized or "rebound" in normalized:
            return "recovery_exit" if "exit" in normalized else "recovery_cohort"
        if "deterioration" in normalized or "no independently" in normalized:
            return "lifecycle_exit"
        return "strategy_target"

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
        origin_subsystem: OriginSubsystem,
        mechanism: AttributionMechanism,
        lifecycles: dict[str, Lifecycle] | None = None,
        reasons: dict[str, str] | None = None,
        mechanisms: dict[str, AttributionMechanism] | None = None,
        replaces_symbols: dict[str, str] | None = None,
    ) -> tuple[Target, ...]:
        """Convert proposed weights into capped, attributed, deterministic targets."""

        targets: list[Target] = []
        unknown_scale = _unknown_industry_scale(
            cfg=self.cfg,
            proposed=proposed,
            leaders=leaders,
        )
        for symbol in sorted(set(account.positions) | set(proposed)):
            score = leaders.get(symbol)
            weight = min(
                symbol_weight_cap(self.cfg, account, symbol),
                max(0.0, proposed.get(symbol, 0.0)),
            )
            if score is not None and score.components.get("unknown_industry", 0.0) >= 0.5:
                weight = min(weight * unknown_scale, self.cfg.unknown_industry_weight_cap)
            selected_lifecycle = (lifecycles or {}).get(symbol, lifecycle)
            selected_mechanism = (mechanisms or {}).get(symbol, mechanism)
            selected_reason = (reasons or {}).get(symbol)
            if selected_reason is None:
                selected_reason = reason
            held_grant_id = (
                account.positions[symbol].grant_id if symbol in account.positions else ""
            )
            held_epoch_id = (
                account.positions[symbol].epoch_id if symbol in account.positions else ""
            )
            grant = account.strategic_grant
            strategic_epoch = next(
                (
                    epoch
                    for epoch in account.strategic_epochs
                    if grant is not None and epoch.epoch_id == grant.epoch_id
                ),
                None,
            )
            strategic_epoch_owned = bool(
                origin_subsystem is OriginSubsystem.STRATEGIC
                and grant is not None
                and strategic_epoch is not None
                and not strategic_epoch.terminal
                and symbol in set(account.strategic_cohort_symbols)
            )
            strategic_grant_owned = bool(
                origin_subsystem is OriginSubsystem.STRATEGIC
                and grant is not None
                and symbol == grant.candidate_symbol
                and (
                    not grant.epoch_id
                    or strategic_epoch is not None and not strategic_epoch.terminal
                )
            )
            targets.append(
                Target(
                    symbol=symbol,
                    weight=weight,
                    lifecycle=selected_lifecycle.value,
                    alpha_score=score.score if score else 0.0,
                    confidence=score.confidence if score else 0.0,
                    reason=selected_reason,
                    reason_code=self._reason_code(selected_reason),
                    entry_industry_strength=(
                        score.components.get("industry_rotation_strength", 0.0) if score else 0.0
                    ),
                    origin_subsystem=origin_subsystem.value,
                    mechanism=selected_mechanism.value,
                    origin_lifecycle=selected_lifecycle.value,
                    replaces_symbol=(replaces_symbols or {}).get(symbol),
                    grant_id=(
                        held_grant_id
                        or (
                            grant.grant_id
                            if strategic_grant_owned and grant is not None
                            else ""
                        )
                    ),
                    epoch_id=(
                        held_epoch_id
                        or (
                            grant.epoch_id
                            if strategic_epoch_owned and grant is not None
                            else ""
                        )
                    ),
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
        account.candidate_tenure["cross_industry_hard_risk_trail"] = 0
        for key in tuple(account.replacement_tenure):
            if key.startswith("hard_risk_winner_trail:"):
                account.replacement_tenure.pop(key, None)

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
        pending_buy = any(order.symbol in anchors and order.side == "BUY" for order in account.pending_orders)
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
