"""Fixed causal trend policy for ordinary positions; no recovery authority."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..types import AccountState, LeaderScore, Opportunity, RiskAssessment
from .strategic.qualification_candidates import candidate_entry, candidate_market_block

if TYPE_CHECKING:
    from .allocator import PortfolioAllocator


def ordinary_trend(frame: pd.DataFrame, date: pd.Timestamp) -> dict[str, Any]:
    """Evaluate only closes known at this decision; never create a certificate."""
    if date not in frame.index:
        return {"block": "CURRENT_MARKET_DATA_UNAVAILABLE"}
    close = frame.loc[:date, "close"].tail(125)
    if len(close) < 125 or not np.isfinite(close.to_numpy(dtype=float)).all() or (close <= 0).any():
        return {"block": "INSUFFICIENT_VALID_HISTORY"}
    ma60, ma120 = close.rolling(60).mean(), close.rolling(120).mean()
    ret120 = close.pct_change(120, fill_method=None)
    confirmed = ((close > ma60) & (ma60 > ma120) & (ret120 > 0)).tail(5).all()
    return {"block": "READY" if confirmed else "TREND_NOT_CONFIRMED",
            "signal": "MA60_MA120_RET120_FIVE_BARS", "ret120": float(ret120.iloc[-1])}


def ordinary_trend_exit(frame: pd.DataFrame, date: pd.Timestamp) -> bool:
    if date not in frame.index:
        return False
    close = frame.loc[:date, "close"].tail(121)
    if len(close) < 121 or not np.isfinite(close.to_numpy(dtype=float)).all() or (close <= 0).any():
        return False
    return bool((close < close.rolling(120).mean()).tail(2).all())


def ordinary_core_entry(
    self: PortfolioAllocator, *, symbol: str, score: LeaderScore, date: pd.Timestamp,
    user_panel: dict[str, pd.DataFrame], account: AccountState, confirmation_days: int,
    certificate: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reference = account.strategic_cash_rearm.consumed_order
    if reference is not None and any(
        order.symbol == symbol and order.order_id == reference.order_id
        and order.event_id == reference.event_id for order in account.pending_orders
    ):
        return candidate_entry(self, symbol=symbol, score=score, date=date,
                               user_panel=user_panel, account=account,
                               confirmation_days=confirmation_days)
    strategic_claims = (
        bool(account.strategic_cohort_targets)
        or any(p.shares > 0 and (p.grant_id or p.epoch_id) for p in account.positions.values())
        or any(o.grant_id or o.epoch_id for o in account.pending_orders)
    )
    if strategic_claims:
        return candidate_entry(self, symbol=symbol, score=score, date=date,
                               user_panel=user_panel, account=account,
                               confirmation_days=confirmation_days, certificate=certificate)
    block = candidate_market_block(self, symbol=symbol, score=score, date=date, user_panel=user_panel)
    if block != "READY":
        return {"block": block}
    return ordinary_trend(user_panel[symbol], date)


def observe_ordinary_market(
    self: PortfolioAllocator, *, date: pd.Timestamp, opportunity: Opportunity,
    risk: RiskAssessment, leaders: dict[str, LeaderScore],
    user_panel: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    return {"as_of": str(date.date()), "policy": "simple_ordinary_trend"}
