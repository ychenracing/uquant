"""A-share fees and trade-cost accounting with dated statutory schedules."""

from __future__ import annotations

from datetime import date as date_type

import pandas as pd

from ..config import SystemConfig
from ..types import (
    Side,
)

DateLike = str | date_type | pd.Timestamp

# (effective_from, seller stamp-duty rate). Each rate applies until the next row.
STAMP_DUTY_SCHEDULE: tuple[tuple[date_type, float], ...] = (
    (date_type(2008, 9, 19), 0.001),
    (date_type(2023, 8, 28), 0.0005),
)
# (effective_from, two-sided transfer-fee rate on traded value, SH and SZ).
TRANSFER_FEE_SCHEDULE: tuple[tuple[date_type, float], ...] = (
    (date_type(2015, 8, 1), 0.00002),
    (date_type(2022, 4, 29), 0.00001),
)


def _scheduled(schedule: tuple[tuple[date_type, float], ...], trade_date: DateLike, name: str) -> float:
    day = pd.Timestamp(trade_date).date()
    rate: float | None = None
    for effective, value in schedule:
        if day >= effective:
            rate = value
    if rate is None:
        raise ValueError(f"{name} schedule does not cover {day.isoformat()}")
    return rate


def stamp_duty_rate(trade_date: DateLike, cfg: SystemConfig) -> float:
    """Return the seller stamp duty; an explicit config value is a fixed-rate assumption."""

    if cfg.stamp_duty is not None:
        return float(cfg.stamp_duty)
    return _scheduled(STAMP_DUTY_SCHEDULE, trade_date, "stamp duty")


def transfer_fee_rate(trade_date: DateLike, cfg: SystemConfig) -> float:
    """Return the transfer fee; an explicit config value is a fixed-rate assumption."""

    if cfg.transfer_fee is not None:
        return float(cfg.transfer_fee)
    return _scheduled(TRANSFER_FEE_SCHEDULE, trade_date, "transfer fee")


def fee_components(
    side: str, gross: float, cfg: SystemConfig, trade_date: DateLike,
) -> tuple[float, float, float]:
    """Return commission, stamp duty, and transfer fee for one fill on ``trade_date``.

    The minimum commission applies per submission; the simulator submits at
    most one order per intent and session.
    """
    commission = max(cfg.min_commission, gross * cfg.commission_rate) if gross > 0 else 0.0
    stamp = gross * stamp_duty_rate(trade_date, cfg) if side == Side.SELL.value else 0.0
    transfer = gross * transfer_fee_rate(trade_date, cfg)
    return commission, stamp, transfer
