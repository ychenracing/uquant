"""Current long-pullback stock proof; it supplies no capital authority."""
from __future__ import annotations

import math
import re
from numbers import Real
from typing import Any, TypeGuard

import pandas as pd

from .config import SystemConfig
from .types import LeaderScore


def _is_finite_number(value: object) -> TypeGuard[float]:
    if isinstance(value, bool):
        return False
    return isinstance(value, Real) and math.isfinite(value)


def pullback_quality(values: dict[str, Any], cfg: SystemConfig) -> bool:
    """One fixed conjunction, including weak-short-term losing historical cases."""
    floors = {
        "leader_confidence": cfg.leader_min_confidence,
        "secular_score": cfg.strategic_secular_min_score,
        "secular_confidence": cfg.strategic_secular_min_confidence,
        "momentum60": cfg.strategic_current_factor_floor,
        "momentum120": cfg.strategic_current_factor_floor,
        "relative_strength": cfg.strategic_current_factor_floor,
    }
    return all(_is_finite_number(values.get(k)) and values[k] >= floor for k, floor in floors.items())


def _read_quality(leader: LeaderScore, cfg: SystemConfig, values: dict[str, Any]) -> str | None:
    """Add verified industry and stock quality to the current proof."""
    components = leader.components
    industry_confidence = components.get("industry_inference_confidence")
    unknown = components.get("unknown_industry")
    if (leader.industry in {"", "unknown"} or not _is_finite_number(industry_confidence)
            or industry_confidence < cfg.strategic_secular_min_confidence
            or not _is_finite_number(unknown) or unknown >= .5):
        return "INDUSTRY_NOT_VERIFIED"
    quality_keys = ("secular_score", "secular_confidence", "momentum60", "momentum120", "relative_strength")
    if not _is_finite_number(leader.confidence) or any(not _is_finite_number(components.get(k)) for k in quality_keys):
        return "CURRENT_QUALITY_UNAVAILABLE"
    values.update({k: float(components[k]) for k in quality_keys})
    values["leader_confidence"] = float(leader.confidence)
    if not pullback_quality(values, cfg):
        return "LONG_QUALITY_INCOMPLETE"
    return None


def current_pullback_proof(
    *, symbol: str, date: pd.Timestamp, frame: pd.DataFrame, leader: LeaderScore,
    cfg: SystemConfig,
) -> dict[str, Any]:
    """Read only causal stock inputs; callers own tradable-role and capital checks."""
    result: dict[str, Any] = {"as_of": str(date.date()), "symbol": symbol, "values": {}}
    values = result["values"]
    if not re.fullmatch(r"(?:sh|sz)[0-9]{6}", symbol) or leader.symbol != symbol:
        return {**result, "block": "CURRENT_IDENTITY_UNAVAILABLE"}
    if date not in frame.index:
        return {**result, "block": "CURRENT_MARKET_DATA_UNAVAILABLE"}
    history = frame.loc[:date]
    if len(history) < 121:
        return {**result, "block": "INSUFFICIENT_HISTORY"}
    row = history.iloc[-1]
    keys = ("close", "ma120", "ret20", "ret60", "ret120")
    if any(not _is_finite_number(row.get(key)) for key in keys):
        return {**result, "block": "CURRENT_MARKET_DATA_UNAVAILABLE"}
    values.update({key: float(row[key]) for key in keys})
    quality_block = _read_quality(leader, cfg, values)
    if quality_block is not None:
        return {**result, "block": quality_block}
    if not (values["close"] >= values["ma120"] > 0
            and values["ret20"] <= cfg.tactical_rebound_breadth_max_ret20
            and values["ret60"] >= cfg.tactical_rebound_min_ret60
            and values["ret120"] <= cfg.tactical_rebound_max_ret120):
        return {**result, "block": "LONG_PULLBACK_NOT_PRESENT"}
    if "amount" not in history:
        return {**result, "block": "LIQUIDITY_NOT_CONFIRMED"}
    amounts = [float(x) for x in history["amount"].tail(20) if _is_finite_number(x) and x > 0]
    median = float(pd.Series(amounts).median()) if amounts else 0.0
    values["positive_amount_sessions"] = len(amounts)
    values["median_amount"] = median
    if len(amounts) < 10 or median < cfg.minimum_median_amount:
        return {**result, "block": "LIQUIDITY_NOT_CONFIRMED"}
    return {**result, "block": "READY"}
