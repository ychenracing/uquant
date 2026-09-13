"""Lifecycle transitions for ordinary leader positions."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pandas as pd

from ...features import scalar
from ...models.ordinary_entry import (
    holding_pullback_entry,
    pullback_graduated,
    record_pullback_graduation,
)
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
    reference_return: float = math.nan,
) -> bool:
    """Confirm holding-specific deterioration with causal session evidence."""
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
    )
    holding_return = scalar(row, f"ret{self.cfg.trend_medium}", math.nan)
    if math.isfinite(holding_return) and math.isfinite(reference_return):
        # Common market damage belongs to the account risk budget. A separate
        # structural liquidation requires the holding to lose ground both
        # absolutely and against the contemporaneous technology reference.
        broken = broken and holding_return < min(0.0, reference_return)
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


def _pullback_structure_exit(
    self: LeaderPortfolioPolicy, *, entry: dict[str, Any], date: pd.Timestamp,
    frame: pd.DataFrame, row: pd.Series | pd.DataFrame, close: float, account: AccountState,
) -> str | None:
    """Confirm the long basis before considering maturity graduation."""
    key = "pullback_exit:" + entry["canonical_sha256"]
    clock = key + ":session"
    session = date.toordinal()
    observed = account.candidate_tenure.get(clock, 0)
    if observed > session:
        raise ValueError("pullback exit observations moved backwards")
    ma120 = scalar(row, "ma120", float("nan"))
    if not math.isfinite(ma120) or ma120 <= 0:
        return ""  # Missing structure is neither confirmation nor repair.
    previous = frame.loc[:date].index[-2].toordinal() if len(frame.loc[:date]) > 1 else 0
    broken = close < ma120
    if observed != session:
        streak = account.replacement_tenure.get(key, 0) if observed == previous else 0
        account.replacement_tenure[key] = streak + 1 if broken else 0
        account.candidate_tenure[clock] = session
    elif not broken:
        account.replacement_tenure[key] = 0
    first_fill = min(fill.fill_date for fill in account.fills
                     if fill.order_id == entry["order_id"] and fill.event_id == entry["event_id"]
                     and fill.side == "BUY")
    held_sessions = len(frame.loc[pd.Timestamp(first_fill):date])
    if (account.replacement_tenure.get(key, 0) >= self.cfg.replacement_confirm_days
            and held_sessions >= self.cfg.min_hold_days):
        return "ordinary long-pullback confirmed MA120 deterioration"
    return "" if broken else None


def ordinary_pullback_exit(
    self: LeaderPortfolioPolicy, *, symbol: str, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], leaders: dict[str, LeaderScore], account: AccountState,
) -> str | None:
    """None delegates graduated/ordinary holdings; empty text retains the long basis."""
    entry = holding_pullback_entry(account, symbol)
    if entry is None:
        return None
    position = account.positions[symbol]
    frame = user_panel.get(symbol)
    if frame is None or date not in frame.index:
        return ""
    row = frame.loc[date]
    close = scalar(row, "close", float("nan"))
    if not math.isfinite(close) or close <= 0 or not math.isfinite(position.avg_cost) or position.avg_cost <= 0:
        return ""
    if close / position.avg_cost - 1 <= self.cfg.strategic_cohort_disaster_stop:
        return "ordinary long-pullback disaster loss against actual average cost"
    if pullback_graduated(account, entry):
        return None
    structure_exit = _pullback_structure_exit(
        self, entry=entry, date=date, frame=frame, row=row, close=close, account=account)
    if structure_exit is not None:
        return structure_exit
    leader = leaders.get(symbol)
    ma60, ret60 = scalar(row, "ma60", float("nan")), scalar(row, "ret60", float("nan"))
    if (leader is not None and leader.mature
            and math.isfinite(ma60) and math.isfinite(ret60) and close >= ma60 > 0 and ret60 > 0
            and self._liquidity_confirmed(frame, date)):
        record_pullback_graduation(account, entry, date=str(date.date()), proof={
            "close": close, "ma60": ma60, "ret60": ret60,
            "mature": True, "liquidity_confirmed": True,
        })
        return None
    return ""


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
