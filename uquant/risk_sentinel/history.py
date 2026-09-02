"""Account-free causal market history for base risk and Risk Sentinel."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, cast

import numpy as np
import pandas as pd

from uquant.config import SystemConfig
from uquant.contracts.universe import AIUniverse
from uquant.features import scalar
from uquant.market_risk import build_base_market_family_snapshot

from .coverage import assess_coverage, build_coverage_health
from .evidence import NameMarketEvidence, build_market_evidence_from_observations
from .history_cache import decode_risk_evidence_timeline, encode_risk_evidence_timeline
from .integration import severe_direct as _severe_direct
from .models import (
    BaseMarketRiskRow,
    CoverageHealth,
    RiskEvidenceTimeline,
    SentinelCausalState,
    SentinelLevel,
    SentinelMarketRow,
    WarmupStatus,
)
from .opinion import build_risk_opinion

_MARKET_FAMILIES = (
    "breadth_structure",
    "covariance_stress",
    "market_velocity",
)
_MARKET_LOOKBACK = 61
type TimestampInput = np.integer[Any] | float | str | date | datetime | np.datetime64


def _normalized_timestamp(value: object) -> pd.Timestamp:
    """Normalize one causal market-history label without changing conversion semantics."""
    return pd.Timestamp(cast(TimestampInput, value)).normalize()


def _valid_close(frame: pd.DataFrame) -> pd.Series:
    values = pd.to_numeric(frame["close"], errors="coerce")
    return values[values > 0.0].astype(float)


def _prepared_name_observations(
    reference_panel: Mapping[str, pd.DataFrame],
) -> tuple[
    dict[str, dict[pd.Timestamp, NameMarketEvidence]],
    dict[str, pd.Series],
]:
    observations: dict[str, dict[pd.Timestamp, NameMarketEvidence]] = {}
    returns: dict[str, pd.Series] = {}
    for symbol, frame in sorted(reference_panel.items()):
        close = _valid_close(frame)
        daily = close.pct_change(fill_method=None)
        returns[symbol] = daily
        fast = close / close.shift(5) - 1.0
        below = close < close.rolling(20).mean()
        recent = daily.rolling(5).std(ddof=0)
        prior = daily.shift(5).rolling(15).std(ddof=0)
        ratio = recent / prior
        fallback = pd.Series(
            np.where(recent > 1e-12, 3.0, 1.0),
            index=recent.index,
            dtype=float,
        )
        ratio = ratio.where(prior > 1e-12, fallback)
        ratio = ratio.clip(upper=3.0)
        prepared: dict[pd.Timestamp, NameMarketEvidence] = {}
        for session in close.index[20:]:
            point = pd.Timestamp(session).normalize()
            fast_value = float(fast.loc[session])
            ratio_value = float(ratio.loc[session])
            if not math.isfinite(fast_value) or not math.isfinite(ratio_value):
                continue
            prepared[point] = NameMarketEvidence(
                fast_return=fast_value,
                downside=float(fast_value < 0.0),
                below_ma20=float(below.loc[session]),
                volatility_ratio=ratio_value,
            )
        observations[symbol] = prepared
    return observations, returns


def _prepared_index_returns(
    frame: pd.DataFrame,
) -> tuple[dict[pd.Timestamp, float], dict[pd.Timestamp, float]]:
    close = _valid_close(frame)
    return (
        {
            _normalized_timestamp(key): float(value)
            for key, value in (close / close.shift(5) - 1.0).dropna().items()
        },
        {
            _normalized_timestamp(key): float(value)
            for key, value in (close / close.shift(20) - 1.0).dropna().items()
        },
    )


def _prepared_correlation(
    *,
    session: pd.Timestamp,
    symbols: tuple[str, ...],
    returns: Mapping[str, pd.Series],
    rolling: Mapping[pd.Timestamp, pd.DataFrame],
    complete: pd.DataFrame,
    positions: dict[tuple[str, ...], np.ndarray],
) -> float:
    if (
        len(symbols) >= 4
        and session in complete.index
        and all(symbol in complete.columns and bool(complete.at[session, symbol]) for symbol in symbols)
    ):
        matrix = rolling[session]
        if symbols not in positions:
            positions[symbols] = matrix.columns.get_indexer(pd.Index(symbols))
        selected = matrix.to_numpy(dtype=float, copy=False)[np.ix_(positions[symbols], positions[symbols])]
        values = selected[~np.eye(len(selected), dtype=bool)]
        finite = values[np.isfinite(values)]
        median = float(np.median(finite)) if finite.size else 0.0
        return median if math.isfinite(median) else 0.0
    series = {symbol: returns[symbol].loc[:session].tail(20) for symbol in symbols if symbol in returns}
    if len(series) < 4:
        return 0.0
    correlation = pd.DataFrame(series).dropna(how="all").corr(min_periods=10)
    corr_values = correlation.where(~np.eye(len(correlation), dtype=bool)).stack()
    median = float(corr_values.median()) if not corr_values.empty else 0.0
    return median if math.isfinite(median) else 0.0


def _rolling_correlation_cache(
    returns: Mapping[str, pd.Series],
) -> tuple[dict[pd.Timestamp, pd.DataFrame], pd.DataFrame]:
    frame = pd.DataFrame({symbol: returns[symbol] for symbol in sorted(returns)})
    rolling = frame.rolling(20, min_periods=10).corr(pairwise=True)
    complete = frame.notna().rolling(20, min_periods=20).sum().eq(20)
    panels = {
        _normalized_timestamp(session): values.droplevel(0)
        for session, values in rolling.groupby(level=0, sort=False)
    }
    return panels, complete


def _history_prefix(frame: pd.DataFrame, point: pd.Timestamp) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("risk evidence timeline requires DatetimeIndex frames")
    return frame.loc[:point].tail(_MARKET_LOOKBACK)


def _level_rank(level: SentinelLevel) -> int:
    return {
        SentinelLevel.NORMAL: 0,
        SentinelLevel.CAUTION: 1,
        SentinelLevel.DEFENSIVE: 2,
        SentinelLevel.CRITICAL: 3,
        SentinelLevel.NOT_READY: -1,
    }[level]


@dataclass(slots=True)
class _SentinelFoldState:
    trusted: bool = False
    effective: SentinelLevel = SentinelLevel.NORMAL
    confirmed_since: str | None = None
    pending_since: str | None = None
    confirmation: int = 0
    repair: int = 0
    normal_seed: int = 0
    trust_reasons: list[str] = field(default_factory=list)


def _reset_unready_sentinel_state(state: _SentinelFoldState, row: SentinelMarketRow) -> None:
    state.trusted = False
    state.effective = SentinelLevel.NORMAL
    state.confirmed_since = None
    state.pending_since = None
    state.confirmation = 0
    state.repair = 0
    state.normal_seed = 0
    reason = row.coverage_status.value
    if reason not in state.trust_reasons:
        state.trust_reasons.append(reason)


def _fold_ready_sentinel_state(
    state: _SentinelFoldState,
    row: SentinelMarketRow,
    *,
    confirm_days: int,
    repair_days: int,
) -> None:
    normal = row.level is SentinelLevel.NORMAL and not row.freeze_candidate
    if not state.trusted:
        state.normal_seed = state.normal_seed + 1 if normal else 0
        state.confirmation = 0
        state.repair = state.normal_seed
        if state.normal_seed >= repair_days:
            state.trusted = True
            state.trust_reasons.clear()
        return

    if row.freeze_candidate and row.level is not SentinelLevel.NOT_READY:
        state.repair = 0
        if row.severe_direct:
            state.confirmation = max(1, confirm_days)
            state.pending_since = row.date
        elif state.confirmation > 0:
            state.confirmation += 1
        else:
            state.pending_since = row.date
            state.confirmation = 1
        if state.confirmation >= confirm_days and _level_rank(row.level) >= _level_rank(state.effective):
            state.effective = row.level
            state.confirmed_since = state.pending_since
        return

    state.pending_since = None
    state.confirmation = 0
    if normal:
        state.repair += 1
        if state.repair >= repair_days:
            state.effective = SentinelLevel.NORMAL
            state.confirmed_since = None
    else:
        state.repair = 0


def fold_sentinel_market_state(
    rows: tuple[SentinelMarketRow, ...],
    *,
    confirm_days: int,
    repair_days: int,
) -> SentinelCausalState:
    """Fold complete observations without assuming pre-history was NORMAL."""

    if confirm_days < 1 or repair_days < 1:
        raise ValueError("Sentinel confirmation and repair days must be positive")
    state = _SentinelFoldState()
    previous_date: str | None = None

    for row in rows:
        if previous_date is not None and row.date <= previous_date:
            raise ValueError("Sentinel history rows must be strictly ordered")
        previous_date = row.date
        ready = row.coverage_status is WarmupStatus.READY
        if not ready:
            _reset_unready_sentinel_state(state, row)
            continue
        _fold_ready_sentinel_state(state, row, confirm_days=confirm_days, repair_days=repair_days)

    trust_reasons = state.trust_reasons
    if not rows:
        trust_reasons.append("NO_WARMUP_SEQUENCE")
    elif not state.trusted and not trust_reasons:
        trust_reasons.append("INSUFFICIENT_NORMAL_REPAIR_SEED")
    return SentinelCausalState(
        effective_level=state.effective,
        confirmed_since=state.confirmed_since,
        confirmation_days=state.confirmation,
        repair_days=state.repair,
        confirmation_history_trusted=state.trusted,
        trust_reasons=tuple(sorted(trust_reasons)),
    )


def _timeline_sessions(
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    point: pd.Timestamp,
    reference_panel: Mapping[str, pd.DataFrame],
    universe: AIUniverse,
    role_absent_symbols: tuple[str, ...],
) -> tuple[pd.Timestamp, ...]:
    if not isinstance(broad_frame.index, pd.DatetimeIndex) or not isinstance(
        tech_frame.index,
        pd.DatetimeIndex,
    ):
        raise ValueError("risk evidence timeline requires DatetimeIndex indices")
    market_sessions = broad_frame.index.union(tech_frame.index)
    for frame in reference_panel.values():
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("risk evidence timeline requires DatetimeIndex frames")
        market_sessions = market_sessions.union(frame.index)
    candidates = sorted(
        {
            pd.Timestamp(item).normalize()
            for item in market_sessions
            if pd.Timestamp(item).normalize() <= point
        }
    )
    first_membership = min(
        (
            member.effective_from
            for member in universe.members
            if member.tradable and member.symbol not in role_absent_symbols
        ),
        default=point.date(),
    )
    absent = frozenset(role_absent_symbols)
    for index, session in enumerate(candidates):
        if session.date() < first_membership:
            continue
        session_text = str(session.date())
        symbols = tuple(
            symbol
            for symbol in universe.symbols_as_of(session_text)
            if symbol not in absent
        )
        industries = {symbol: universe.industry_of(symbol, session_text) for symbol in symbols}
        coverage = assess_coverage(
            as_of=session_text,
            broad_frame=broad_frame,
            tech_frame=tech_frame,
            expected_symbols=symbols,
            reference_panel=reference_panel,
            point_in_time_industries=industries,
            held_symbols=(),
        )
        if coverage.status is WarmupStatus.READY:
            return tuple(candidates[index:])
    return ()


def _base_breadth_inputs(
    *,
    session: pd.Timestamp,
    reference_panel: Mapping[str, pd.DataFrame],
    industries: Mapping[str, str],
    cfg: SystemConfig,
) -> tuple[list[float], float, float, float]:
    visible_returns: list[float] = []
    below_ma20: list[bool] = []
    sector_returns: dict[str, list[float]] = {}
    sector_below20: dict[str, list[bool]] = {}
    for symbol, frame in reference_panel.items():
        if session not in frame.index:
            continue
        row = frame.loc[session]
        ret5 = scalar(row, "ret5")
        close = scalar(row, "close")
        ma20 = scalar(row, f"ma{cfg.trend_fast}")
        industry = industries.get(symbol, "unknown")
        if math.isfinite(ret5):
            visible_returns.append(ret5)
            sector_returns.setdefault(industry, []).append(ret5)
        if math.isfinite(close) and math.isfinite(ma20):
            below = close < ma20
            below_ma20.append(below)
            sector_below20.setdefault(industry, []).append(below)
    declining_name = float(np.mean(np.asarray(visible_returns) < 0.0)) if visible_returns else 0.0
    below_name = float(np.mean(below_ma20)) if below_ma20 else 0.0
    declining_group = (
        float(np.mean([float(np.mean(values)) < 0.0 for values in sector_returns.values()]))
        if sector_returns
        else declining_name
    )
    below_group = (
        float(np.mean([float(np.mean(values)) for values in sector_below20.values()]))
        if sector_below20
        else below_name
    )
    name_weight = cfg.risk_breadth_name_weight
    declining_ratio = name_weight * declining_name + (1.0 - name_weight) * declining_group
    below_ratio = name_weight * below_name + (1.0 - name_weight) * below_group
    sector_stress = (
        float(np.mean([float(np.mean(values)) < -0.04 for values in sector_returns.values()]))
        if sector_returns
        else 0.0
    )
    return visible_returns, declining_ratio, below_ratio, sector_stress


def _base_covariance_inputs(
    *,
    session: pd.Timestamp,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    cfg: SystemConfig,
) -> tuple[float, float]:
    returns = (
        reference_returns.loc[:session].tail(_MARKET_LOOKBACK)
        if reference_returns is not None
        else pd.DataFrame(
            {symbol: frame["close"].pct_change(fill_method=None) for symbol, frame in reference_panel.items()}
        )
    )
    correlation = float("nan")
    if len(returns.columns) >= 4:
        values = (
            returns.tail(cfg.correlation_window)
            .corr()
            .where(~np.eye(len(returns.columns), dtype=bool))
            .stack()
        )
        if not values.empty:
            correlation = float(values.median())
    tech_returns = tech_frame.loc[:session, "close"].pct_change(fill_method=None)
    recent_vol = float(tech_returns.tail(10).std(ddof=0))
    normal_vol = float(tech_returns.tail(60).std(ddof=0))
    volatility_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
    return correlation, volatility_ratio


def _active_base_market_families(
    *,
    session: pd.Timestamp,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    industries: Mapping[str, str],
    cfg: SystemConfig,
) -> dict[str, bool]:
    visible_returns, declining_ratio, below_ratio, sector_stress = _base_breadth_inputs(
        session=session,
        reference_panel=reference_panel,
        industries=industries,
        cfg=cfg,
    )
    correlation, volatility_ratio = _base_covariance_inputs(
        session=session,
        tech_frame=tech_frame,
        reference_panel=reference_panel,
        reference_returns=reference_returns,
        cfg=cfg,
    )
    snapshot = build_base_market_family_snapshot(
        average_fast_return=(float(np.mean(visible_returns)) if visible_returns else 0.0),
        declining_ratio=declining_ratio,
        below_ma20_ratio=below_ratio,
        sector_stress_ratio=sector_stress,
        median_correlation=correlation,
        volatility_ratio=volatility_ratio,
        tech_speed=min(
            scalar(tech_frame.loc[session], "ret5", 0.0),
            scalar(tech_frame.loc[session], "ret10", 0.0),
        ),
        broad_speed=min(
            scalar(broad_frame.loc[session], "ret5", 0.0),
            scalar(broad_frame.loc[session], "ret10", 0.0),
        ),
        cfg=cfg,
    )
    return dict(snapshot.family_active)


def _base_row(
    *,
    session: pd.Timestamp,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    industries: Mapping[str, str],
    cfg: SystemConfig,
) -> BaseMarketRiskRow:
    ready = session in broad_frame.index and session in tech_frame.index
    flags = {family: False for family in _MARKET_FAMILIES}
    if ready:
        flags = _active_base_market_families(
            session=session,
            broad_frame=broad_frame,
            tech_frame=tech_frame,
            reference_panel=reference_panel,
            reference_returns=reference_returns,
            industries=industries,
            cfg=cfg,
        )
    return BaseMarketRiskRow(
        date=str(session.date()),
        family_active=tuple(sorted(flags.items())),
        data_ready=ready,
    )


def _first_dates(
    rows: tuple[SentinelMarketRow, ...] | tuple[BaseMarketRiskRow, ...],
) -> tuple[tuple[str, str], ...]:
    result: dict[str, str] = {}
    for row in rows:
        ready = (
            row.coverage_status is WarmupStatus.READY
            if isinstance(row, SentinelMarketRow)
            else row.data_ready
        )
        if not ready:
            continue
        for family in row.active_families:
            result.setdefault(family, row.date)
    return tuple(sorted(result.items()))


def _assemble_timeline(
    *,
    as_of: str,
    sentinel_rows: tuple[SentinelMarketRow, ...],
    base_rows: tuple[BaseMarketRiskRow, ...],
    cfg: SystemConfig,
) -> RiskEvidenceTimeline:
    state = fold_sentinel_market_state(
        sentinel_rows,
        confirm_days=cfg.risk_sentinel_confirm_days,
        repair_days=cfg.risk_sentinel_repair_days,
    )
    sentinel_first = _first_dates(sentinel_rows)
    base_first = _first_dates(base_rows)
    sentinel_first_map = dict(sentinel_first)
    base_first_map = dict(base_first)
    current_sentinel = set(sentinel_rows[-1].active_families) if sentinel_rows else set()
    current_base = set(base_rows[-1].active_families) if base_rows else set()
    return RiskEvidenceTimeline(
        as_of=as_of,
        sessions=tuple(row.date for row in sentinel_rows),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        sentinel_first_family_dates=sentinel_first,
        base_first_family_dates=base_first,
        incremental_families=tuple(sorted(current_sentinel - current_base)),
        earlier_families=tuple(
            sorted(
                family
                for family, first in sentinel_first_map.items()
                if family not in base_first_map or first < base_first_map[family]
            )
        ),
        confirmation_days=state.confirmation_days,
        repair_days=state.repair_days,
        effective_level=state.effective_level,
        confirmed_since=state.confirmed_since,
        confirmation_history_trusted=state.confirmation_history_trusted,
        trust_reasons=state.trust_reasons,
    )


def risk_evidence_timeline_prefix(
    timeline: RiskEvidenceTimeline,
    *,
    as_of: str,
    cfg: SystemConfig,
) -> RiskEvidenceTimeline:
    """Return a causally folded immutable prefix from one verified full cache."""

    point = pd.Timestamp(as_of).normalize()
    sentinel_rows = tuple(row for row in timeline.sentinel_rows if pd.Timestamp(row.date) <= point)
    base_rows = tuple(row for row in timeline.base_rows if pd.Timestamp(row.date) <= point)
    if len(sentinel_rows) != len(base_rows):
        raise RuntimeError("base and Sentinel timeline prefixes differ")
    return _assemble_timeline(
        as_of=str(point.date()),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        cfg=cfg,
    )


_prefix = _history_prefix


def risk_evidence_timeline_to_dict(
    timeline: RiskEvidenceTimeline,
) -> dict[str, Any]:
    """Serialize an immutable timeline for a sealed data/config cache."""
    return encode_risk_evidence_timeline(timeline)


def risk_evidence_timeline_from_dict(payload: Mapping[str, Any]) -> RiskEvidenceTimeline:
    """Validate and restore a timeline cache without executable serialization."""
    return decode_risk_evidence_timeline(payload)


@dataclass(slots=True)
class _TimelineSources:
    base_rows: list[BaseMarketRiskRow]
    broad_fast: dict[pd.Timestamp, float]
    broad_medium: dict[pd.Timestamp, float]
    complete_correlation: pd.DataFrame
    correlation_positions: dict[tuple[str, ...], np.ndarray]
    name_observations: dict[str, dict[pd.Timestamp, NameMarketEvidence]]
    name_returns: dict[str, pd.Series]
    point: pd.Timestamp
    rolling_correlation: dict[pd.Timestamp, pd.DataFrame]
    sentinel_rows: list[SentinelMarketRow]
    sessions: tuple[pd.Timestamp, ...]
    tech_fast: dict[pd.Timestamp, float]
    tech_medium: dict[pd.Timestamp, float]


def _prepare_timeline_sources(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    tech_frame: pd.DataFrame,
    universe: AIUniverse,
    role_absent_symbols: tuple[str, ...],
) -> _TimelineSources:
    point = pd.Timestamp(as_of).normalize()
    sessions = _timeline_sessions(
        broad_frame,
        tech_frame,
        point,
        reference_panel,
        universe,
        role_absent_symbols,
    )
    name_observations, name_returns = _prepared_name_observations(reference_panel)
    rolling_correlation, complete_correlation = _rolling_correlation_cache(name_returns)
    correlation_positions: dict[tuple[str, ...], np.ndarray] = {}
    broad_fast, broad_medium = _prepared_index_returns(broad_frame)
    tech_fast, tech_medium = _prepared_index_returns(tech_frame)
    sentinel_rows: list[SentinelMarketRow] = []
    base_rows: list[BaseMarketRiskRow] = []
    return _TimelineSources(
        base_rows=base_rows,
        broad_fast=broad_fast,
        broad_medium=broad_medium,
        complete_correlation=complete_correlation,
        correlation_positions=correlation_positions,
        name_observations=name_observations,
        name_returns=name_returns,
        point=point,
        rolling_correlation=rolling_correlation,
        sentinel_rows=sentinel_rows,
        sessions=sessions,
        tech_fast=tech_fast,
        tech_medium=tech_medium,
    )


def _timeline_session_view(
    *,
    session: pd.Timestamp,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    universe: AIUniverse,
    role_absent_symbols: tuple[str, ...],
) -> tuple[
    str,
    tuple[str, ...],
    dict[str, pd.DataFrame],
    dict[str, str],
    CoverageHealth,
]:
    session_text = str(session.date())
    absent = frozenset(role_absent_symbols)
    symbols = tuple(
        symbol
        for symbol in universe.symbols_as_of(session_text)
        if symbol not in absent
    )
    panel = {
        symbol: _prefix(reference_panel[symbol], session)
        for symbol in symbols
        if symbol in reference_panel
    }
    industries = {
        symbol: universe.industry_of(symbol, session_text) for symbol in symbols
    }
    observed = frozenset(
        symbol
        for symbol in symbols
        if symbol in reference_panel and session in reference_panel[symbol].index
    )
    counts = {
        symbol: int(reference_panel[symbol].index.searchsorted(session, side="right"))
        for symbol in symbols
        if symbol in reference_panel
    }
    warmed = frozenset(symbol for symbol in observed if counts.get(symbol, 0) >= 21)
    stale = frozenset(
        symbol
        for symbol in symbols
        if symbol not in observed and counts.get(symbol, 0) > 0
    )
    missing_indices = tuple(
        name
        for name, frame in (("sh000300", broad_frame), ("sh000682", tech_frame))
        if session not in frame.index
        or int(frame.index.searchsorted(session, side="right")) < 21
    )
    coverage = build_coverage_health(
        expected_symbols=symbols,
        observed_symbols=observed,
        warmed_symbols=warmed,
        stale_symbols=stale,
        new_symbols=observed - warmed,
        point_in_time_industries=industries,
        held_symbols=(),
        missing_indices=missing_indices,
    )
    return session_text, symbols, panel, industries, coverage


def _timeline_session_rows(
    *,
    session: pd.Timestamp,
    sources: _TimelineSources,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    universe: AIUniverse,
    role_absent_symbols: tuple[str, ...],
    cfg: SystemConfig,
) -> tuple[SentinelMarketRow, BaseMarketRiskRow]:
    session_text, symbols, panel, industries, coverage = _timeline_session_view(
        session=session,
        broad_frame=broad_frame,
        tech_frame=tech_frame,
        reference_panel=reference_panel,
        universe=universe,
        role_absent_symbols=role_absent_symbols,
    )
    names = {
        symbol: sources.name_observations[symbol][session]
        for symbol in symbols
        if session in sources.name_observations.get(symbol, {})
    }
    evidence = build_market_evidence_from_observations(
        as_of=session_text,
        names=names,
        point_in_time_industries=industries,
        held_symbols=(),
        leader_symbols=(),
        capital_drawdown=None,
        broad_fast=sources.broad_fast.get(session, 0.0),
        broad_medium=sources.broad_medium.get(session, 0.0),
        tech_fast=sources.tech_fast.get(session, 0.0),
        tech_medium=sources.tech_medium.get(session, 0.0),
        median_correlation=_prepared_correlation(
            session=session,
            symbols=tuple(sorted(names)),
            returns=sources.name_returns,
            rolling=sources.rolling_correlation,
            complete=sources.complete_correlation,
            positions=sources.correlation_positions,
        ),
    )
    weakest = tuple(
        item.industry
        for item in sorted(
            evidence.subindustries,
            key=lambda item: (item.fast_return, item.industry),
        )[:3]
    )
    assessment = replace(
        build_risk_opinion(evidence=evidence, coverage=coverage),
        weakest_subindustries=weakest,
    )
    active = {family: family in assessment.evidence_families for family in _MARKET_FAMILIES}
    return (
        SentinelMarketRow(
            date=session_text,
            coverage_status=assessment.coverage.status,
            confidence=assessment.confidence,
            level=assessment.level,
            freeze_candidate=(assessment.freeze_new_risk and any(active.values())),
            family_active=tuple(sorted(active.items())),
            reasons=assessment.reasons,
            weakest_subindustries=assessment.weakest_subindustries,
            severe_direct=_severe_direct(assessment, cfg),
        ),
        _base_row(
            session=session,
            broad_frame=_prefix(broad_frame, session),
            tech_frame=_prefix(tech_frame, session),
            reference_panel=panel,
            reference_returns=reference_returns,
            industries=industries,
            cfg=cfg,
        ),
    )


def build_risk_evidence_timeline(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    universe: AIUniverse,
    cfg: SystemConfig,
    role_absent_symbols: tuple[str, ...] = (),
) -> RiskEvidenceTimeline:
    """Rebuild complete PIT market history without current account inputs."""

    if (
        role_absent_symbols != tuple(sorted(set(role_absent_symbols)))
        or not set(role_absent_symbols).issubset(universe.symbols)
    ):
        raise ValueError("risk timeline role absence must be canonical universe members")
    absent = frozenset(role_absent_symbols)
    declared = tuple(symbol for symbol in universe.symbols if symbol not in absent)
    role_panel = (
        reference_panel
        if not role_absent_symbols
        else {
            symbol: reference_panel[symbol]
            for symbol in declared
            if symbol in reference_panel
        }
    )
    role_returns = (
        reference_returns
        if not role_absent_symbols
        else (
            None
            if reference_returns is None
            else reference_returns.loc[
                :,
                [symbol for symbol in declared if symbol in reference_returns],
            ]
        )
    )

    sources = _prepare_timeline_sources(
        as_of=as_of,
        broad_frame=broad_frame,
        reference_panel=role_panel,
        tech_frame=tech_frame,
        universe=universe,
        role_absent_symbols=role_absent_symbols,
    )
    for session in sources.sessions:
        sentinel_row, base_row = _timeline_session_rows(
            session=session,
            sources=sources,
            broad_frame=broad_frame,
            tech_frame=tech_frame,
            reference_panel=role_panel,
            reference_returns=role_returns,
            universe=universe,
            role_absent_symbols=role_absent_symbols,
            cfg=cfg,
        )
        sources.sentinel_rows.append(sentinel_row)
        sources.base_rows.append(base_row)

    return _assemble_timeline(
        as_of=str(sources.point.date()),
        sentinel_rows=tuple(sources.sentinel_rows),
        base_rows=tuple(sources.base_rows),
        cfg=cfg,
    )
