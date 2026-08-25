"""Task 6 mechanical owner for fees."""

from __future__ import annotations

from ..config import SystemConfig
from ..types import (
    Side,
)


def fee_components(side: str, gross: float, cfg: SystemConfig) -> tuple[float, float, float]:
    """Return commission, stamp duty, and transfer fee for one fill."""
    commission = max(cfg.min_commission, gross * cfg.commission_rate) if gross > 0 else 0.0
    stamp = gross * cfg.stamp_duty if side == Side.SELL.value else 0.0
    transfer = gross * cfg.transfer_fee
    return commission, stamp, transfer
