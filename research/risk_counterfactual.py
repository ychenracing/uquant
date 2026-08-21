"""Research-only shadow policies and counterfactual promotion rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace

import pandas as pd

from uquant.config import SystemConfig
from uquant.execution import merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.portfolio_core import current_weights
from uquant.types import AccountState, PendingOrder, Target

NEGATIVE_CONTROL_IDS = frozenset(
    {"phase5_rejected_gross_cap_control", "phase7_rejected_exclusive_freeze_control"}
)


def effective_shadow_cap(base_cap: float, trade_cap: float) -> float:
    if not 0 <= base_cap <= 1 or not 0 <= trade_cap <= 1:
        raise ValueError("gross caps must be in [0, 1]")
    return min(base_cap, trade_cap)


def execution_day(signal_date: str, calendar: Sequence[str]) -> str | None:
    try:
        position = tuple(calendar).index(signal_date)
    except ValueError as exc:
        raise ValueError("signal date is outside the execution calendar") from exc
    return calendar[position + 1] if position + 1 < len(calendar) else None


def wilder_atr(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], *, period: int
) -> float:
    if len(highs) != len(lows) or len(highs) != len(closes) or len(highs) < period:
        raise ValueError("ATR inputs are not aligned or warm")
    true_ranges: list[float] = []
    for index, (high, low) in enumerate(zip(highs, lows, strict=True)):
        previous = closes[index - 1] if index else closes[index]
        true_ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    atr = sum(true_ranges[:period]) / period
    for value in true_ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


@dataclass(frozen=True, slots=True)
class ShadowPolicy:
    policy_id: str
    transfer_kind: str
    trigger_axis: str | None
    description: str


POLICY_SET = (
    ShadowPolicy("baseline_uquant", "CONTROL", None, "current production FREEZE_ONLY"),
    ShadowPolicy("base_only_control", "CONTROL", None, "Sentinel SHADOW isolation"),
    ShadowPolicy("sentinel_freeze_only_control", "CONTROL", None, "explicit production mapping"),
    ShadowPolicy("trade_entry_freeze_shadow", "EXACT_TRANSFER", "block_new_entries", "block new symbols"),
    ShadowPolicy("trade_pyramid_freeze_shadow", "EXACT_TRANSFER", "block_pyramiding", "clamp additions"),
    ShadowPolicy(
        "trade_gross_cap_shadow",
        "TRANSLATED_SHADOW",
        "recommended_gross_cap",
        "translated monotone gross-cap diagnostic",
    ),
    ShadowPolicy(
        "trade_layered_protection_shadow",
        "TRANSLATED_SHADOW",
        "layered_protection",
        "translated next-open layered-stop diagnostic",
    ),
    ShadowPolicy(
        "trade_cluster_trim_hybrid_shadow", "HYBRID_DIAGNOSTIC", "cluster_trim", "uquant retention ordering"
    ),
    ShadowPolicy(
        "phase5_rejected_gross_cap_control", "NEGATIVE_CONTROL", "recommended_gross_cap", "archived Phase 5"
    ),
    ShadowPolicy(
        "phase7_rejected_exclusive_freeze_control",
        "NEGATIVE_CONTROL",
        "block_new_entries",
        "archived Phase 7",
    ),
)


def classify_promotion(candidate_id: str, transfer_kind: str, metrics: Mapping[str, bool]) -> str:
    if candidate_id in NEGATIVE_CONTROL_IDS or transfer_kind == "NEGATIVE_CONTROL":
        return "REJECTED_NO_INCREMENTAL_VALUE"
    if transfer_kind == "HYBRID_DIAGNOSTIC":
        return "HYBRID_DIAGNOSTIC_ONLY"
    if not metrics.get("sample_pass", False):
        return "INSUFFICIENT_SAMPLE"
    if transfer_kind != "EXACT_TRANSFER" or not metrics.get("causal_validity_pass", False):
        return "REJECTED_NO_INCREMENTAL_VALUE"
    if not metrics.get("detection_pass", False):
        return "REJECTED_NO_INCREMENTAL_VALUE"
    if not metrics.get("economic_pass", False) or not metrics.get("generalization_pass", False):
        return "REJECTED_ECONOMIC_REGRESSION"
    return "PROMOTION_CANDIDATE"


def baseline_equivalent_metrics(baseline: Mapping[str, float | int]) -> dict[str, float | int]:
    """Return an exact copy for a policy with no causal/actionable trigger."""
    return dict(baseline)


def clamp_pyramid_targets(
    targets: tuple[Target, ...], weights_now: Mapping[str, float]
) -> tuple[Target, ...]:
    """Clamp increases in held symbols while preserving independent reductions."""

    return tuple(
        replace(target, weight=min(target.weight, weights_now[target.symbol]))
        if target.symbol in weights_now
        else target
        for target in targets
    )


def layered_protection_line(
    *,
    entry: float,
    peak_close: float,
    atr: float | None,
    risk_level: int,
    account_drawdown: float,
    trend_adjustment: float = 0.0,
) -> tuple[float, str]:
    """Pinned trade layered-stop formula, without importing challenger code."""

    if entry <= 0:
        return 0.0, "none"
    peak = max(peak_close, entry)
    candidates = [("catastrophe_stop", peak * 0.72)]
    if risk_level >= 1 and account_drawdown >= 0.05:
        candidates.append(("cost_stop", entry * 0.82))
        if atr is not None and atr > 0 and peak - 6.0 * atr > 0:
            candidates.append(("atr_stop", peak - 6.0 * atr))
        if risk_level >= 2:
            peak_gain = peak / entry - 1.0
            giveback = 0.18
            for threshold, ratio in ((0.30, 0.18), (0.80, 0.22), (1.50, 0.26), (3.00, 0.28)):
                if peak_gain >= threshold:
                    giveback = ratio
            giveback = min(0.28, max(0.14, giveback + trend_adjustment))
            candidates.append(("profit_tier_stop", peak * (1.0 - giveback)))
    return max(candidates, key=lambda item: item[1])[1], max(candidates, key=lambda item: item[1])[0]


def rebuild_shadow_orders(
    *,
    account: AccountState,
    previous_account: AccountState,
    signal_date: str,
    targets: tuple[Target, ...],
    prices: dict[str, float],
    cfg: SystemConfig,
    removed_buy_reason: str | None = None,
) -> tuple[PendingOrder, ...]:
    """Regenerate intents through the production planner on a research account."""

    account.order_ledger = deepcopy(previous_account.order_ledger)
    account.next_order_sequence = previous_account.next_order_sequence
    account.pending_orders = deepcopy(previous_account.pending_orders)
    planned = plan_orders(
        signal_date=signal_date,
        targets=targets,
        account=account,
        prices=prices,
        cfg=cfg,
    )
    previous = list(account.pending_orders)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=targets, cfg=cfg)
    return reconcile_account_orders(
        account=account,
        previous=previous,
        current=merged,
        submitted_date=signal_date,
        removed_buy_reason=removed_buy_reason,
    )


def marked_weights(account: AccountState, prices: dict[str, float]) -> tuple[dict[str, float], float]:
    return current_weights(account, prices)


def trend_health_adjustment(frame: pd.DataFrame, date: pd.Timestamp) -> float:
    """Pinned +/-3 percentage-point giveback adjustment."""

    visible = frame.loc[:date]
    if len(visible) < 60:
        return 0.0
    closes = pd.to_numeric(visible["close"], errors="coerce")
    current = float(closes.iloc[-1])
    ma20 = float(closes.iloc[-20:].mean())
    ma60 = float(closes.iloc[-60:].mean())
    previous_ma20 = float(closes.iloc[-21:-1].mean())
    if current > ma20 > ma60 and ma20 >= previous_ma20:
        return 0.03
    if current < ma20 <= ma60:
        return -0.03
    return 0.0
