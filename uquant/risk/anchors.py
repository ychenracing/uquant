"""Dynamic reference-anchor selection and confirmation hysteresis."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import SystemConfig
from ..features import scalar
from ..leader import REFERENCE_UNIVERSE
from ..types import AccountState, LeaderScore, Risk


@dataclass(frozen=True, slots=True)
class AnchorAssessment:
    """Read-only outputs from the ordered dynamic-anchor ownership slice."""

    symbols: tuple[str, ...]
    groups: set[str]
    reference_armed: bool
    reference_break: bool
    break_key: str
    immediate_reference_break: bool


def _dynamic_anchor_candidate(leaders: dict[str, LeaderScore], cfg: SystemConfig) -> list[str]:
    """Select a deterministic, group-balanced anchor basket from references."""
    ranked = sorted(
        (
            item
            for symbol, item in leaders.items()
            if symbol in REFERENCE_UNIVERSE
            and item.industry != "unknown"
            and item.confidence >= cfg.leader_min_confidence
            and item.components.get("secular_score", 0.0) >= cfg.risk_anchor_min_secular_score
        ),
        key=lambda item: (
            -item.components.get("secular_score", 0.0),
            -item.score,
            item.symbol,
        ),
    )
    selected: list[str] = []
    groups: set[str] = set()
    for item in ranked:
        if item.industry not in groups:
            selected.append(item.symbol)
            groups.add(item.industry)
        if len(selected) >= cfg.risk_anchor_count:
            break
    for item in ranked:
        if item.symbol not in selected:
            selected.append(item.symbol)
        if len(selected) >= cfg.risk_anchor_count:
            break
    return selected


def _update_dynamic_anchors(
    *,
    leaders: dict[str, LeaderScore],
    account: AccountState,
    cfg: SystemConfig,
    allow_reanchor: bool,
) -> tuple[str, ...]:
    """Apply confirmation hysteresis and reset break state on a true re-anchor."""
    if not cfg.dynamic_risk_anchors_enabled:
        return ()
    candidate = _dynamic_anchor_candidate(leaders, cfg)
    candidate_groups = {
        leaders[symbol].industry
        for symbol in candidate
        if symbol in leaders and leaders[symbol].industry != "unknown"
    }
    signature = ",".join(candidate)
    current_signature = account.risk_anchor_signature
    if len(candidate) != cfg.risk_anchor_count or len(candidate_groups) < cfg.risk_anchor_min_groups:
        # Confirmation must be consecutive.  Missing coverage/evidence cannot
        # bridge two otherwise unrelated candidate periods.  More importantly,
        # a partial candidate must never replace a previously complete basket
        # and silently disarm its structural-break evidence.
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
        return tuple(account.risk_anchor_symbols)
    if not allow_reanchor:
        # Do not replace damaged sentinels with the day's survivors while a
        # transition is under way; that would erase the very break they are
        # meant to observe.
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
        return tuple(account.risk_anchor_symbols)
    if signature and signature != current_signature:
        if signature == account.risk_anchor_candidate_signature:
            account.risk_anchor_candidate_streak += 1
        else:
            account.risk_anchor_candidate_signature = signature
            account.risk_anchor_candidate_streak = 1
        if account.risk_anchor_candidate_streak >= cfg.risk_anchor_confirm_days:
            account.risk_anchor_symbols = candidate
            account.risk_anchor_signature = signature
            account.risk_anchor_candidate_signature = ""
            account.risk_anchor_candidate_streak = 0
            account.risk_streaks["reference_anchor_armed"] = 0
            account.risk_streaks["reference_anchor_break"] = 0
    elif signature == current_signature:
        account.risk_anchor_candidate_signature = ""
        account.risk_anchor_candidate_streak = 0
    return tuple(account.risk_anchor_symbols)


def _healthy_anchor_basket(
    *,
    complete: bool,
    symbols: tuple[str, ...],
    reference_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    cfg: SystemConfig,
) -> bool:
    return complete and all(
        scalar(reference_panel[symbol].loc[date], "close")
        > scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_medium}")
        and scalar(
            reference_panel[symbol].loc[date],
            f"ret{cfg.trend_medium}",
            -1.0,
        )
        > 0
        for symbol in symbols
    )


def _immediate_anchor_break(
    *,
    armed: bool,
    complete: bool,
    symbols: tuple[str, ...],
    anchor_ret5: list[float],
    reference_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
    cfg: SystemConfig,
) -> bool:
    return bool(
        armed
        and complete
        and all(
            scalar(reference_panel[symbol].loc[date], "close")
            < scalar(reference_panel[symbol].loc[date], f"ma{cfg.trend_fast}")
            for symbol in symbols
        )
        and float(np.mean(anchor_ret5)) <= cfg.severe_shock_ret5
    )


def _assess_dynamic_anchors(
    *,
    date: pd.Timestamp,
    reference_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
    cfg: SystemConfig,
    transition_damage: float,
    votes: int,
    update_dynamic_anchors: Callable[..., tuple[str, ...]],
) -> AnchorAssessment:
    """Run the existing anchor selection, health, and break slice in order."""

    anchor_symbols = update_dynamic_anchors(
        leaders=leaders,
        account=account,
        cfg=cfg,
        allow_reanchor=(
            account.risk == Risk.NORMAL.value
            and transition_damage <= cfg.transition_damage_repair
            and votes <= 1
        ),
    )
    anchor_damage: list[bool] = []
    anchor_ret5: list[float] = []
    for symbol in anchor_symbols:
        frame = reference_panel.get(symbol)
        if frame is None or date not in frame.index:
            continue
        row = frame.loc[date]
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ret5 = scalar(row, "ret5", 0.0)
        anchor_ret5.append(ret5)
        anchor_damage.append(math.isfinite(close) and math.isfinite(ma20) and close < ma20 and ret5 <= -0.06)
    anchor_groups = {leaders[symbol].industry for symbol in anchor_symbols if symbol in leaders}
    complete_anchor_basket = (
        len(anchor_damage) == cfg.risk_anchor_count and len(anchor_groups) >= cfg.risk_anchor_min_groups
    )
    reference_anchor_healthy = _healthy_anchor_basket(
        complete=complete_anchor_basket,
        symbols=anchor_symbols,
        reference_panel=reference_panel,
        date=date,
        cfg=cfg,
    )
    if reference_anchor_healthy:
        account.risk_streaks["reference_anchor_armed"] = 1
    reference_anchor_armed = account.risk_streaks.get("reference_anchor_armed", 0) == 1
    reference_anchor_break = complete_anchor_basket and all(anchor_damage)
    anchor_break_key = "reference_anchor_break"
    account.risk_streaks[anchor_break_key] = (
        account.risk_streaks.get(anchor_break_key, 0) + 1 if reference_anchor_break else 0
    )
    immediate_reference_break = _immediate_anchor_break(
        armed=reference_anchor_armed,
        complete=complete_anchor_basket,
        symbols=anchor_symbols,
        anchor_ret5=anchor_ret5,
        reference_panel=reference_panel,
        date=date,
        cfg=cfg,
    )
    return AnchorAssessment(
        symbols=anchor_symbols,
        groups=anchor_groups,
        reference_armed=reference_anchor_armed,
        reference_break=reference_anchor_break,
        break_key=anchor_break_key,
        immediate_reference_break=immediate_reference_break,
    )


assess_dynamic_anchors = _assess_dynamic_anchors
dynamic_anchor_candidate = _dynamic_anchor_candidate
update_dynamic_anchors = _update_dynamic_anchors


__all__ = (
    "AnchorAssessment",
    "_assess_dynamic_anchors",
    "_dynamic_anchor_candidate",
    "_update_dynamic_anchors",
    "assess_dynamic_anchors",
    "dynamic_anchor_candidate",
    "update_dynamic_anchors",
)
