"""Stable-reference, regime-aware mature and secular leader intelligence."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .industry import compute_industry_signals
from .types import AccountState, LeaderScore, Opportunity

STABLE_REFERENCE_UNIVERSE = (
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

# Research additions are deliberately staged outside the production reference
# set.  Experiments may populate this tuple without changing live percentiles,
# breadth, cache keys, or data requirements.  Promoting a symbol requires an
# explicit reviewed change to ``STABLE_REFERENCE_UNIVERSE``.
EXPANDING_RESEARCH_REFERENCE: tuple[str, ...] = ()
REFERENCE_UNIVERSE = STABLE_REFERENCE_UNIVERSE
RESEARCH_REFERENCE_UNIVERSE = tuple(dict.fromkeys(STABLE_REFERENCE_UNIVERSE + EXPANDING_RESEARCH_REFERENCE))
STABLE_REFERENCE_SNAPSHOT_COHORT_CUTOFF = "2022-01-31"


def stable_reference_requires_history(symbol: str, first_observed_date: str) -> bool:
    """Require backfill only for stable references present at the 2022 snapshot boundary."""
    return bool(
        symbol in STABLE_REFERENCE_UNIVERSE and first_observed_date <= STABLE_REFERENCE_SNAPSHOT_COHORT_CUTOFF
    )


FACTOR_PROFILES: dict[str, dict[str, float]] = {
    Opportunity.TREND.value: {
        "momentum60": 0.18,
        "momentum120": 0.12,
        "relative_strength": 0.16,
        "trend_persistence": 0.13,
        "breakout_quality": 0.10,
        "resilience": 0.06,
        "industry_strength": 0.10,
        "volume_expansion": 0.05,
        "trend_efficiency": 0.10,
    },
    Opportunity.RECOVERY.value: {
        "acceleration": 0.18,
        "recovery": 0.18,
        "resilience": 0.16,
        "industry_rotation_strength": 0.13,
        "relative_strength": 0.12,
        "trend_persistence": 0.08,
        "industry_breadth": 0.08,
        "volume_expansion": 0.07,
    },
    Opportunity.CHOPPY.value: {
        "resilience": 0.24,
        "low_drawdown": 0.15,
        "short_relative_strength": 0.16,
        "trend_efficiency": 0.14,
        "volume_expansion": 0.10,
        "industry_strength": 0.09,
        "trend_persistence": 0.07,
        "momentum60": 0.05,
    },
}

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


def credible_recovery_reserve(
    *,
    score: LeaderScore,
    frame: pd.DataFrame,
    date: pd.Timestamp,
    occupied_industries: set[str],
    cfg: SystemConfig,
) -> bool:
    """Identify a liquid, independent reserve before a recovery cohort rotates."""
    if (
        date not in frame.index
        or score.industry in occupied_industries
        or score.components.get("unknown_industry", 0.0) >= 0.5
    ):
        return False
    history = frame.loc[:date]
    if len(history) < cfg.min_history:
        return False
    row = history.iloc[-1]
    return bool(
        score.confidence >= cfg.leader_min_confidence
        and score.score >= cfg.recovery_reserve_min_score
        and scalar(row, f"ret{cfg.trend_medium}", -1.0) >= cfg.recovery_reserve_min_ret60
        and scalar(row, f"ret{cfg.trend_slow}", -1.0) >= cfg.recovery_reserve_min_ret120
        and scalar(row, "close") >= scalar(row, f"ma{cfg.trend_medium}")
    )


def industry_of(symbol: str) -> str:
    """Return the configured industry bucket for a normalized symbol."""
    return INDUSTRY.get(symbol, "unknown")


def _profile_for(opportunity: str) -> str:
    if opportunity in {Opportunity.STRONG_TREND.value, Opportunity.TREND.value}:
        return Opportunity.TREND.value
    if opportunity is Opportunity.RECOVERY.value or opportunity == Opportunity.RECOVERY.value:
        return Opportunity.RECOVERY.value
    return Opportunity.CHOPPY.value


def _residual_returns(series: pd.Series, market: pd.Series) -> pd.Series:
    """Remove the contemporaneous broad technology beta from one return series."""
    aligned = pd.concat(
        [series.rename("asset"), market.rename("market")], axis=1, sort=False
    ).dropna()
    if len(aligned) < 20:
        return pd.Series(dtype=float)
    market_var = float(aligned["market"].var(ddof=0))
    beta = (
        float(aligned[["asset", "market"]].cov(ddof=0).to_numpy(dtype=float)[0, 1])
        / market_var
        if market_var > 1e-12
        else 0.0
    )
    return aligned["asset"] - beta * aligned["market"]


def _inferred_industries(
    panel: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    tech: pd.DataFrame,
) -> dict[str, tuple[str, float]]:
    """Infer unknown groups from stable 60/120-day residual correlations."""
    tech_returns = (
        tech.loc[:as_of, "close"].astype(float).tail(121).pct_change(fill_method=None).dropna()
    )
    group_returns: dict[str, list[pd.Series]] = {}
    for symbol in STABLE_REFERENCE_UNIVERSE:
        industry = INDUSTRY.get(symbol)
        frame = panel.get(symbol)
        if industry is None or frame is None:
            continue
        series = frame.loc[:as_of, "close"].astype(float).tail(121).pct_change(fill_method=None).dropna()
        if len(series) >= 60:
            group_returns.setdefault(industry, []).append(series)
    baskets = {
        industry: _residual_returns(
            pd.concat(series, axis=1, sort=False).mean(axis=1, skipna=True),
            tech_returns,
        )
        for industry, series in group_returns.items()
    }
    inferred: dict[str, tuple[str, float]] = {}
    for symbol, frame in panel.items():
        if symbol in INDUSTRY:
            inferred[symbol] = (INDUSTRY[symbol], 1.0)
            continue
        series = frame.loc[:as_of, "close"].astype(float).tail(121).pct_change(fill_method=None).dropna()
        residual = _residual_returns(series, tech_returns)
        window_rankings: list[list[tuple[float, str]]] = []
        for window in (60, 120):
            correlations: list[tuple[float, str]] = []
            for industry, basket in baskets.items():
                aligned = pd.concat(
                    [residual.tail(window), basket.tail(window)], axis=1, sort=False
                ).dropna()
                if len(aligned) < window:
                    continue
                correlation = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
                if math.isfinite(correlation):
                    correlations.append((correlation, industry))
            correlations.sort(reverse=True)
            if correlations:
                window_rankings.append(correlations)
        if len(window_rankings) != 2:
            inferred[symbol] = ("unknown", 0.0)
            continue
        winners = [ranking[0][1] for ranking in window_rankings]
        if winners[0] != winners[1]:
            inferred[symbol] = ("unknown", 0.0)
            continue
        industry = winners[0]
        best = min(ranking[0][0] for ranking in window_rankings)
        margins = [
            ranking[0][0] - (ranking[1][0] if len(ranking) > 1 else 0.0)
            for ranking in window_rankings
        ]
        confidence = min(1.0, max(0.0, 0.70 * best + 0.30 * min(margins)))
        inferred[symbol] = (industry if confidence > 0 else "unknown", confidence)
    return inferred


def _percentile(value: float, reference: Iterable[float]) -> float:
    values = np.array([item for item in reference if math.isfinite(item)], dtype=float)
    if not math.isfinite(value) or len(values) < 3:
        return 0.0
    return float((values < value).sum() + 0.5 * (values == value).sum()) / len(values)


def compute_structural_leaders(
    panel: dict[str, pd.DataFrame],
    *,
    as_of: pd.Timestamp,
    tech: pd.DataFrame,
    cfg: SystemConfig,
    score_cache: dict[tuple[object, ...], dict[str, LeaderScore]] | None = None,
) -> dict[str, LeaderScore]:
    """Score regime-neutral leaders using only data visible at ``as_of``.

    Cross-sectional percentiles are anchored to the fixed reference universe;
    account persistence and opportunity tilts are deliberately applied later.
    """
    if as_of not in tech.index:
        raise RuntimeError("fixed tech index missing at decision date")
    extra_symbols = tuple(sorted(set(panel) - set(REFERENCE_UNIVERSE)))
    # Structural components include configuration-dependent industry evidence.
    # A ProductionEngine is intentionally reused across promotion cells, so a
    # key that omits cfg can leak small-pool hierarchical scores into the broad
    # compatibility path (or the reverse) and make replay order affect returns.
    cache_key = (as_of, extra_symbols, cfg, "STRUCTURAL")
    cached = score_cache.get(cache_key) if score_cache is not None else None
    if cached is not None:
        return cached
    tech_row = tech.loc[as_of]
    inferred_industries = _inferred_industries(panel, as_of, tech)
    effective_industries = {
        symbol: (
            industry if symbol in INDUSTRY or confidence >= cfg.unknown_industry_confidence else "unknown",
            confidence,
        )
        for symbol, (industry, confidence) in inferred_industries.items()
    }
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
            "ret240": scalar(row, "ret240"),
            "rs60": scalar(row, f"ret{cfg.trend_medium}") - scalar(tech_row, f"ret{cfg.trend_medium}", 0.0),
            "rs120": scalar(row, f"ret{cfg.trend_slow}") - scalar(tech_row, f"ret{cfg.trend_slow}", 0.0),
            "rs240": scalar(row, "ret240") - scalar(tech_row, "ret240", 0.0),
            "trend": float(np.mean(trend_checks)) if trend_checks else 0.0,
            "breakout": scalar(row, "breakout", -1.0),
            "resilience": 1.0 + max(-1.0, scalar(row, "drawdown120", -1.0)),
            "drawdown240": scalar(row, "drawdown240", -1.0),
            "recovery120": scalar(row, "recovery120", 0.0),
            "efficiency": scalar(row, "trend_efficiency120", 0.0),
            "trend_r2": scalar(row, "trend_r2_120", 0.0),
            "above60_persistence": scalar(row, "above_ma60_persistence", 0.0),
            "downside_vol": scalar(row, "downside_vol120", 1.0),
            "ma60_slope": scalar(row, "ma60_slope", -1.0),
            "ma120_slope": scalar(row, "ma120_slope", -1.0),
            "volume": min(2.0, max(0.0, scalar(row, "volume_expansion", 0.0))) / 2.0,
            "accel": scalar(row, "mom_accel_5_20", -1.0),
            "above20": float(math.isfinite(ma20) and close > ma20),
            "above60": float(math.isfinite(ma60) and close > ma60),
            "history": float(history),
            "liquidity": min(
                1.0,
                float(frame.loc[:as_of, "amount"].tail(20).median()) / max(cfg.minimum_median_amount, 1.0),
            ),
        }
    references = [symbol for symbol in REFERENCE_UNIVERSE if symbol in raw]
    expected = [
        symbol for symbol in REFERENCE_UNIVERSE if symbol in panel and panel[symbol].index.min() <= as_of
    ]
    minimum_coverage = max(3, math.ceil(0.80 * len(expected)))
    if len(references) < minimum_coverage:
        raise RuntimeError(
            f"fixed reference coverage too low: {len(references)}/{len(expected)} point-in-time-listed"
        )
    industry_signals = compute_industry_signals(
        raw=raw,
        reference_symbols=references,
        industries={symbol: effective_industries.get(symbol, ("unknown", 0.0))[0] for symbol in panel},
        minimum_members=cfg.industry_signal_min_members,
        hierarchical=cfg.hierarchical_industry_shrinkage_enabled,
    )
    industry_returns: dict[str, list[float]] = {}
    for symbol in references:
        industry_returns.setdefault(effective_industries.get(symbol, ("unknown", 0.0))[0], []).append(
            raw[symbol]["ret60"]
        )
    industry_reference_means = [
        float(np.mean(finite))
        for values in industry_returns.values()
        if (finite := [value for value in values if math.isfinite(value)])
    ]
    base_results: dict[str, LeaderScore] = {}
    for symbol, item in raw.items():
        effective_industry, industry_inference_confidence = effective_industries.get(symbol, ("unknown", 0.0))
        industry_signal = industry_signals.get(effective_industry)
        industry_values = [
            value for value in industry_returns.get(effective_industry, []) if math.isfinite(value)
        ]
        industry_mean = float(np.mean(industry_values)) if industry_values else float("nan")
        global_momentum60 = _percentile(item["ret60"], (raw[s]["ret60"] for s in references))
        global_momentum120 = _percentile(item["ret120"], (raw[s]["ret120"] for s in references))
        industry_momentum60 = _percentile(item["ret60"], industry_values)
        industry_momentum120 = _percentile(
            item["ret120"],
            (
                raw[s]["ret120"]
                for s in references
                if effective_industries.get(s, ("unknown", 0.0))[0] == effective_industry
            ),
        )
        blend = cfg.stable_reference_global_weight
        factors = {
            "momentum60": blend * global_momentum60 + (1.0 - blend) * industry_momentum60,
            "momentum120": blend * global_momentum120 + (1.0 - blend) * industry_momentum120,
            "relative_strength": _percentile(item["rs60"], (raw[s]["rs60"] for s in references)),
            "relative_strength120": _percentile(
                item["rs120"],
                (raw[s]["rs120"] for s in references),
            ),
            "industry_relative_strength120": industry_momentum120,
            "short_relative_strength": _percentile(item["ret20"], (raw[s]["ret20"] for s in references)),
            "trend_persistence": item["trend"],
            "breakout_quality": _percentile(item["breakout"], (raw[s]["breakout"] for s in references)),
            "resilience": _percentile(item["resilience"], (raw[s]["resilience"] for s in references)),
            "low_drawdown": _percentile(item["drawdown240"], (raw[s]["drawdown240"] for s in references)),
            "recovery": _percentile(item["recovery120"], (raw[s]["recovery120"] for s in references)),
            "acceleration": _percentile(item["accel"], (raw[s]["accel"] for s in references)),
            "trend_efficiency": _percentile(item["efficiency"], (raw[s]["efficiency"] for s in references)),
            "volume_expansion": item["volume"],
            "industry_strength": _percentile(
                industry_mean,
                industry_reference_means,
            ),
            "industry_rotation_strength": (industry_signal.score if industry_signal is not None else 0.5),
            "industry_rotation_raw_strength": (
                industry_signal.raw_score if industry_signal is not None else 0.5
            ),
            "industry_confidence": (industry_signal.confidence if industry_signal is not None else 0.0),
            "industry_breadth20": (industry_signal.breadth20 if industry_signal is not None else 0.0),
            "industry_breadth60": (industry_signal.breadth60 if industry_signal is not None else 0.0),
            "industry_return20": (industry_signal.return20 if industry_signal is not None else 0.0),
            "industry_return60": (industry_signal.return60 if industry_signal is not None else 0.0),
            "industry_breadth": (
                0.5 * (industry_signal.breadth20 + industry_signal.breadth60)
                if industry_signal is not None
                else 0.0
            ),
            "industry_inference_confidence": industry_inference_confidence,
            "unknown_industry": float(industry_inference_confidence < cfg.unknown_industry_confidence),
            "liquidity": item["liquidity"],
            "factor_profile": -1.0,
        }
        secular_factors = {
            "secular_ret240": _percentile(item["ret240"], (raw[s]["ret240"] for s in references)),
            "secular_rs240": _percentile(item["rs240"], (raw[s]["rs240"] for s in references)),
            "secular_efficiency": factors["trend_efficiency"],
            "secular_r2": _percentile(item["trend_r2"], (raw[s]["trend_r2"] for s in references)),
            "secular_persistence": item["above60_persistence"],
            "secular_slope60": _percentile(
                item["ma60_slope"],
                (raw[s]["ma60_slope"] for s in references),
            ),
            "secular_slope120": _percentile(
                item["ma120_slope"],
                (raw[s]["ma120_slope"] for s in references),
            ),
            "secular_resilience": (
                factors["low_drawdown"]
                + _percentile(
                    -item["downside_vol"],
                    (-raw[s]["downside_vol"] for s in references),
                )
                + factors["recovery"]
            )
            / 3.0,
        }
        secular_factors["secular_stability"] = (
            0.25 * secular_factors["secular_efficiency"]
            + 0.25 * secular_factors["secular_r2"]
            + 0.20 * secular_factors["secular_persistence"]
            + 0.15 * secular_factors["secular_slope60"]
            + 0.15 * secular_factors["secular_slope120"]
        )
        secular_score = (
            0.20 * secular_factors["secular_ret240"]
            + 0.15 * factors["momentum120"]
            + 0.15 * secular_factors["secular_rs240"]
            + 0.10 * factors["industry_relative_strength120"]
            + 0.10 * secular_factors["secular_stability"]
            + 0.10 * secular_factors["secular_resilience"]
            + 0.08 * factors["industry_strength"]
            + 0.07 * factors["industry_breadth"]
            + 0.05 * factors["liquidity"]
        )
        secular_confidence = min(1.0, item["history"] / 240.0) * item["liquidity"]
        factors.update(secular_factors)
        factors["secular_score"] = secular_score
        factors["secular_confidence"] = secular_confidence
        # Production eligibility keeps the original nine-factor availability
        # contract.  Long-horizon research features must not make an otherwise
        # identical historical observation look less complete.
        core_observed = sum(
            math.isfinite(item[name])
            for name in (
                "ret20",
                "ret60",
                "ret120",
                "rs60",
                "trend",
                "breakout",
                "resilience",
                "volume",
                "accel",
            )
        )
        confidence = min(1.0, item["history"] / cfg.min_history) * min(1.0, core_observed / 9.0)
        stable_score = (
            0.23 * global_momentum60
            + 0.12 * global_momentum120
            + 0.17 * factors["relative_strength"]
            + 0.14 * factors["trend_persistence"]
            + 0.10 * factors["breakout_quality"]
            + 0.10 * factors["resilience"]
            + 0.08 * factors["industry_strength"]
            + 0.06 * factors["volume_expansion"]
        )
        factors.update(
            {
                "stable_score": stable_score,
                "raw_ret60": item["ret60"],
                "raw_ret120": item["ret120"],
                "raw_accel": item["accel"],
                "raw_history": item["history"],
            }
        )
        raw_score = stable_score
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
            industry=effective_industry,
            components=factors,
        )
    if score_cache is not None:
        score_cache[cache_key] = base_results
    return base_results


def apply_opportunity_alpha(
    structural: dict[str, LeaderScore],
    *,
    opportunity: Opportunity | str,
    cfg: SystemConfig,
) -> dict[str, LeaderScore]:
    """Blend the current session's opportunity tilt without mutating tenure."""
    opportunity_value = opportunity.value if isinstance(opportunity, Opportunity) else str(opportunity)
    profile = _profile_for(opportunity_value)
    profile_code = {
        Opportunity.TREND.value: 2.0,
        Opportunity.RECOVERY.value: 1.0,
        Opportunity.CHOPPY.value: 0.0,
    }[profile]
    results: dict[str, LeaderScore] = {}
    for symbol, base in structural.items():
        factors = dict(base.components)
        profile_score = sum(weight * factors[name] for name, weight in FACTOR_PROFILES[profile].items())
        stable_score = factors["stable_score"]
        profile_tilt = 0.03 if cfg.regime_factor_blend_enabled else 0.0
        raw_score = (1.0 - profile_tilt) * stable_score + profile_tilt * profile_score
        score = raw_score * (0.55 + 0.45 * base.confidence)
        mature = bool(
            score >= cfg.leader_mature_score
            and factors["raw_ret60"] > 0
            and factors["raw_ret120"] > 0
            and factors["trend_persistence"] >= 2 / 3
            and base.confidence >= cfg.leader_min_confidence
        )
        emerging = bool(
            not mature
            and factors["raw_history"] >= cfg.emerging_min_history
            and factors["raw_accel"] > 0
            and factors["breakout_quality"] >= 0.70
            and factors["relative_strength"] >= 0.70
            and score >= cfg.leader_emerging_score
        )
        factors["factor_profile"] = profile_code
        factors["profile_score"] = profile_score
        results[symbol] = LeaderScore(
            symbol=symbol,
            score=score,
            confidence=base.confidence,
            mature=mature,
            emerging=emerging,
            industry=base.industry,
            components=factors,
        )
    return results


def apply_leader_tenure(
    leaders: dict[str, LeaderScore],
    *,
    account: AccountState,
    cfg: SystemConfig,
) -> dict[str, LeaderScore]:
    """Apply the only per-decision mutation of leader persistence state."""
    return _apply_tenure(leaders, account, cfg)


def compute_leaders(
    panel: dict[str, pd.DataFrame],
    *,
    as_of: pd.Timestamp,
    tech: pd.DataFrame,
    account: AccountState,
    cfg: SystemConfig,
    score_cache: dict[tuple[object, ...], dict[str, LeaderScore]] | None = None,
) -> dict[str, LeaderScore]:
    """Compatibility wrapper for structural, current-alpha, then tenure scoring."""
    structural = compute_structural_leaders(
        panel,
        as_of=as_of,
        tech=tech,
        cfg=cfg,
        score_cache=score_cache,
    )
    alpha = apply_opportunity_alpha(structural, opportunity=account.opportunity, cfg=cfg)
    return apply_leader_tenure(alpha, account=account, cfg=cfg)


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
