"""Fixed-reference mature and emerging leader intelligence."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .types import AccountState, LeaderScore

REFERENCE_UNIVERSE = (
    "sh600487",
    "sh601869",
    "sh603688",
    "sh603986",
    "sh688008",
    "sh688012",
    "sh688019",
    "sh688037",
    "sh688041",
    "sh688072",
    "sh688082",
    "sh688110",
    "sh688120",
    "sh688146",
    "sh688200",
    "sh688233",
    "sh688256",
    "sh688268",
    "sh688300",
    "sh688347",
    "sh688361",
    "sh688498",
    "sh688766",
    "sz000636",
    "sz002281",
    "sz002371",
    "sz002409",
    "sz300054",
    "sz300223",
    "sz300308",
    "sz300394",
    "sz300502",
    "sz300604",
    "sz300666",
)

INDUSTRY = {
    "sz300308": "optical",
    "sz300502": "optical",
    "sz300394": "optical",
    "sh688205": "optical",
    "sh603986": "memory",
    "sh688008": "compute",
    "sh688041": "compute",
    "sh688256": "compute",
    "sh688120": "equipment",
    "sh688012": "equipment",
    "sh688072": "equipment",
    "sh688082": "equipment",
    "sh688200": "equipment",
    "sh688361": "equipment",
    "sh688347": "equipment",
    "sh688300": "materials",
    "sh688019": "materials",
    "sh688233": "materials",
    "sz300666": "materials",
    "sz300604": "materials",
    "sz002409": "pcb",
    "sz002371": "pcb",
    "sz002281": "datacenter",
    "sh601869": "optical",
    "sh600487": "optical",
    "sh603688": "semiconductor",
    "sz000636": "passives",
    "sz300054": "equipment",
    "sz300223": "equipment",
    "sh688498": "packaging",
    "sh688268": "equipment",
    "sh688146": "materials",
    "sh688766": "compute",
    "sh688110": "foundry",
    "sh688037": "design",
}


def industry_of(symbol: str) -> str:
    return INDUSTRY.get(symbol, "unknown")


def _percentile(value: float, reference: Iterable[float]) -> float:
    values = np.array([item for item in reference if math.isfinite(item)], dtype=float)
    if not math.isfinite(value) or len(values) < 3:
        return 0.0
    return float((values < value).sum() + 0.5 * (values == value).sum()) / len(values)


def compute_leaders(
    panel: dict[str, pd.DataFrame],
    *,
    as_of: pd.Timestamp,
    tech: pd.DataFrame,
    account: AccountState,
    cfg: SystemConfig,
    score_cache: dict[tuple[pd.Timestamp, tuple[str, ...]], dict[str, LeaderScore]] | None = None,
) -> dict[str, LeaderScore]:
    if as_of not in tech.index:
        raise RuntimeError("fixed tech index missing at decision date")
    extra_symbols = tuple(sorted(set(panel) - set(REFERENCE_UNIVERSE)))
    cache_key = (as_of, extra_symbols)
    cached = score_cache.get(cache_key) if score_cache is not None else None
    if cached is not None:
        return _apply_tenure(cached, account, cfg)
    tech_row = tech.loc[as_of]
    raw: dict[str, dict[str, float]] = {}
    for symbol, frame in panel.items():
        if as_of not in frame.index:
            continue
        row = frame.loc[as_of]
        # The feature store is date-sorted. Index position is the causal
        # history length and avoids materialising a growing prefix every day.
        history = int(frame.index.searchsorted(as_of, side="right"))
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        ma60 = scalar(row, f"ma{cfg.trend_medium}")
        ma120 = scalar(row, f"ma{cfg.trend_slow}")
        trend_checks = [close > value for value in (ma20, ma60, ma120) if math.isfinite(value)]
        raw[symbol] = {
            "ret20": scalar(row, f"ret{cfg.trend_fast}"),
            "ret60": scalar(row, f"ret{cfg.trend_medium}"),
            "ret120": scalar(row, f"ret{cfg.trend_slow}"),
            "rs60": scalar(row, f"ret{cfg.trend_medium}") - scalar(tech_row, f"ret{cfg.trend_medium}", 0.0),
            "trend": float(np.mean(trend_checks)) if trend_checks else 0.0,
            "breakout": scalar(row, "breakout", -1.0),
            "resilience": 1.0 + max(-1.0, scalar(row, "drawdown120", -1.0)),
            "volume": min(2.0, max(0.0, scalar(row, "volume_expansion", 0.0))) / 2.0,
            "accel": scalar(row, "mom_accel_5_20", -1.0),
            "history": float(history),
        }
    references = [symbol for symbol in REFERENCE_UNIVERSE if symbol in raw]
    if len(references) < 20:
        raise RuntimeError(f"fixed reference coverage too low: {len(references)}/34")
    industry_returns: dict[str, list[float]] = {}
    for symbol in references:
        industry_returns.setdefault(industry_of(symbol), []).append(raw[symbol]["ret60"])
    base_results: dict[str, LeaderScore] = {}
    industry_reference_means = [
        float(np.mean(finite))
        for values in industry_returns.values()
        if (finite := [value for value in values if math.isfinite(value)])
    ]
    for symbol, item in raw.items():
        industry_values = [
            value for value in industry_returns.get(industry_of(symbol), []) if math.isfinite(value)
        ]
        industry_mean = float(np.mean(industry_values)) if industry_values else float("nan")
        factors = {
            "momentum60": _percentile(item["ret60"], (raw[s]["ret60"] for s in references)),
            "momentum120": _percentile(item["ret120"], (raw[s]["ret120"] for s in references)),
            "relative_strength": _percentile(item["rs60"], (raw[s]["rs60"] for s in references)),
            "trend_persistence": item["trend"],
            "breakout_quality": _percentile(item["breakout"], (raw[s]["breakout"] for s in references)),
            "resilience": _percentile(item["resilience"], (raw[s]["resilience"] for s in references)),
            "volume_expansion": item["volume"],
            "industry_strength": _percentile(
                industry_mean,
                industry_reference_means,
            ),
        }
        observed = sum(math.isfinite(value) for value in item.values() if not isinstance(value, str))
        confidence = min(1.0, item["history"] / cfg.min_history) * min(1.0, observed / 9.0)
        raw_score = (
            0.23 * factors["momentum60"]
            + 0.12 * factors["momentum120"]
            + 0.17 * factors["relative_strength"]
            + 0.14 * factors["trend_persistence"]
            + 0.10 * factors["breakout_quality"]
            + 0.10 * factors["resilience"]
            + 0.08 * factors["industry_strength"]
            + 0.06 * factors["volume_expansion"]
        )
        score = raw_score * (0.55 + 0.45 * confidence)
        mature = bool(
            score >= cfg.leader_mature_score
            and item["ret60"] > 0
            and item["ret120"] > 0
            and factors["trend_persistence"] >= 2 / 3
            and confidence >= cfg.leader_min_confidence
        )
        emerging = bool(
            not mature
            and item["history"] >= cfg.emerging_min_history
            and item["accel"] > 0
            and factors["breakout_quality"] >= 0.70
            and factors["relative_strength"] >= 0.70
            and score >= cfg.leader_emerging_score
        )
        base_results[symbol] = LeaderScore(
            symbol=symbol,
            score=score,
            confidence=confidence,
            mature=mature,
            emerging=emerging,
            industry=industry_of(symbol),
            components=factors,
        )
    if score_cache is not None:
        score_cache[cache_key] = base_results
    return _apply_tenure(base_results, account, cfg)


def _apply_tenure(
    base_results: dict[str, LeaderScore], account: AccountState, cfg: SystemConfig
) -> dict[str, LeaderScore]:
    results: dict[str, LeaderScore] = {}
    for symbol, base in base_results.items():
        tenure = account.leader_tenure.get(symbol, 0)
        if base.mature or base.emerging:
            tenure += 1
        else:
            tenure = max(0, tenure - 1)
        account.leader_tenure[symbol] = tenure
        results[symbol] = LeaderScore(
            symbol=base.symbol,
            score=base.score,
            confidence=base.confidence,
            mature=base.mature and tenure >= cfg.leader_tenure_days,
            emerging=base.emerging and tenure >= cfg.emerging_tenure_days,
            industry=base.industry,
            components=base.components,
        )
    return results
