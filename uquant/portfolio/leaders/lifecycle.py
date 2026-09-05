"""Lifecycle transitions for ordinary leader positions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from ...features import scalar
from ...types import (
    AccountState,
    LeaderScore,
)

if TYPE_CHECKING:
    from .admission import LeaderPortfolioPolicy


def _session_clock(
    user_panel: dict[str, pd.DataFrame],
    date: pd.Timestamp,
) -> pd.DatetimeIndex:
    """Return the deterministic union of all visible user sessions."""
    clock = pd.DatetimeIndex([])
    for frame in user_panel.values():
        sessions = pd.DatetimeIndex(frame.index)
        clock = clock.union(sessions[sessions <= date])
    return clock.sort_values()


def _leader_session_distance(
    clock: pd.DatetimeIndex,
    start: str | pd.Timestamp,
    end: pd.Timestamp,
) -> int:
    bounded = clock[(clock >= pd.Timestamp(start)) & (clock <= end)]
    return max(0, len(bounded) - 1)


def _rotation_allowed(
    self: LeaderPortfolioPolicy,
    account: AccountState,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
) -> bool:
    clock = self._session_clock(user_panel, date)
    recent = [
        value
        for value in account.rotation_dates
        if pd.Timestamp(value) <= date and self._session_distance(clock, value, date) <= 20
    ]
    account.rotation_dates = recent
    return len(recent) < self.cfg.max_rotations_20d


def _retention_score(
    symbol: str,
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> float:
    """Protect proven winners when K contracts or a challenger appears."""
    position = account.positions.get(symbol)
    if position is None:
        return leaders[symbol].score
    peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
    winner_bonus = min(0.20, 0.50 * max(0.0, peak_mfe))
    return leaders[symbol].score + winner_bonus


def _leader_lifecycle_exit_confirmed(
    self: LeaderPortfolioPolicy,
    *,
    symbol: str,
    date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame],
    leaders: dict[str, LeaderScore],
    account: AccountState,
) -> bool:
    """Reuse the existing per-symbol damage confirmation across owner gaps."""
    position = account.positions.get(symbol)
    frame = user_panel.get(symbol)
    leader = leaders.get(symbol)
    key = f"lifecycle_exit:{symbol}"
    if position is None or position.shares <= 0 or frame is None or date not in frame.index or leader is None:
        account.replacement_tenure[key] = 0
        return False
    row = frame.loc[date]
    peak_mfe = position.highest_close / max(position.avg_cost, 1e-12) - 1.0
    protected_winner = peak_mfe >= 0.20
    broken = bool(
        not leader.mature
        and scalar(row, "close")
        < scalar(
            row,
            f"ma{self.cfg.trend_medium if protected_winner else self.cfg.trend_fast}",
        )
        and scalar(row, f"ret{self.cfg.trend_fast}", 0.0) <= (-0.15 if protected_winner else -0.08)
    )
    clock = f"lifecycle_exit_session:{symbol}"
    session = date.toordinal()
    previous = frame.loc[:date].index[-2].toordinal() if len(frame.loc[:date]) > 1 else 0
    observed = account.candidate_tenure.get(clock, 0)
    if observed > session:
        raise ValueError("lifecycle exit observations must be causal")
    if observed != session:
        streak = account.replacement_tenure.get(key, 0) if observed == previous else 0
        account.replacement_tenure[key] = streak + 1 if broken else 0
        account.candidate_tenure[clock] = session
    elif not broken:
        account.replacement_tenure[key] = 0
    held_sessions = len(frame.loc[pd.Timestamp(position.entry_date) : date]) if position.entry_date else 0
    return bool(
        account.replacement_tenure[key] >= self.cfg.replacement_confirm_days
        and held_sessions >= self.cfg.min_hold_days
    )


def _industry_handoff(
    self: LeaderPortfolioPolicy,
    *,
    challenger: LeaderScore,
    incumbent: LeaderScore,
) -> bool:
    """Confirm a cross-industry hand-off from independent breadth evidence."""
    if (
        not self.cfg.industry_rotation_enabled
        or challenger.industry == incumbent.industry
        or challenger.industry == "unknown"
        or challenger.components.get("unknown_industry", 0.0) >= 0.5
    ):
        return False
    challenger_strength = challenger.components.get("industry_rotation_strength", 0.5)
    incumbent_strength = incumbent.components.get("industry_rotation_strength", 0.5)
    challenger_confidence = challenger.components.get("industry_confidence", 0.0)
    incumbent_breadth = incumbent.components.get("industry_breadth20", 0.0)
    return bool(
        challenger_strength >= self.cfg.industry_rotation_min_score
        and challenger_confidence >= self.cfg.industry_rotation_min_confidence
        and challenger_strength - incumbent_strength >= self.cfg.industry_rotation_edge
        and (
            incumbent_strength <= self.cfg.industry_rotation_deterioration
            or incumbent_breadth <= self.cfg.industry_rotation_breadth
        )
    )


industry_handoff = _industry_handoff
leader_lifecycle_exit_confirmed = _leader_lifecycle_exit_confirmed
leader_retention_score = _retention_score
leader_rotation_allowed = _rotation_allowed
leader_session_clock = _session_clock
leader_session_distance = _leader_session_distance
