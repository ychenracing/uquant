"""Account-free causal market history for base risk and Risk Sentinel."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from uquant.config import SystemConfig
from uquant.contracts.universe import AIUniverse
from uquant.features import scalar
from uquant.market_risk import build_base_market_family_snapshot

from .coverage import assess_coverage, build_coverage_health
from .evidence import NameMarketEvidence, build_market_evidence_from_observations
from .integration import _severe_direct
from .models import (
    BaseMarketRiskRow,
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
            pd.Timestamp(key).normalize(): float(value)  # type: ignore[arg-type]
            for key, value in (close / close.shift(5) - 1.0).dropna().items()
        },
        {
            pd.Timestamp(key).normalize(): float(value)  # type: ignore[arg-type]
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
        and all(
            symbol in complete.columns and bool(complete.at[session, symbol])
            for symbol in symbols
        )
    ):
        matrix = rolling[session]
        if symbols not in positions:
            positions[symbols] = matrix.columns.get_indexer(pd.Index(symbols))
        selected = matrix.to_numpy(dtype=float, copy=False)[
            np.ix_(positions[symbols], positions[symbols])
        ]
        values = selected[~np.eye(len(selected), dtype=bool)]
        finite = values[np.isfinite(values)]
        median = float(np.median(finite)) if finite.size else 0.0
        return median if math.isfinite(median) else 0.0
    series = {
        symbol: returns[symbol].loc[:session].tail(20)
        for symbol in symbols
        if symbol in returns
    }
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
        pd.Timestamp(session).normalize(): values.droplevel(0)  # type: ignore[arg-type]
        for session, values in rolling.groupby(level=0, sort=False)
    }
    return panels, complete


def _prefix(frame: pd.DataFrame, point: pd.Timestamp) -> pd.DataFrame:
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


def fold_sentinel_market_state(
    rows: tuple[SentinelMarketRow, ...],
    *,
    confirm_days: int,
    repair_days: int,
) -> SentinelCausalState:
    """Fold complete observations without assuming pre-history was NORMAL."""

    if confirm_days < 1 or repair_days < 1:
        raise ValueError("Sentinel confirmation and repair days must be positive")
    trusted = False
    effective = SentinelLevel.NORMAL
    confirmed_since: str | None = None
    pending_since: str | None = None
    confirmation = 0
    repair = 0
    normal_seed = 0
    trust_reasons: list[str] = []
    previous_date: str | None = None

    for row in rows:
        if previous_date is not None and row.date <= previous_date:
            raise ValueError("Sentinel history rows must be strictly ordered")
        previous_date = row.date
        ready = row.coverage_status is WarmupStatus.READY
        normal = row.level is SentinelLevel.NORMAL and not row.freeze_candidate
        if not ready:
            trusted = False
            effective = SentinelLevel.NORMAL
            confirmed_since = None
            pending_since = None
            confirmation = 0
            repair = 0
            normal_seed = 0
            reason = row.coverage_status.value
            if reason not in trust_reasons:
                trust_reasons.append(reason)
            continue

        if not trusted:
            normal_seed = normal_seed + 1 if normal else 0
            confirmation = 0
            repair = normal_seed
            if normal_seed >= repair_days:
                trusted = True
                trust_reasons.clear()
            continue

        if row.freeze_candidate and row.level is not SentinelLevel.NOT_READY:
            repair = 0
            if row.severe_direct:
                confirmation = max(1, confirm_days)
                pending_since = row.date
            elif confirmation > 0:
                confirmation += 1
            else:
                pending_since = row.date
                confirmation = 1
            if confirmation >= confirm_days and _level_rank(row.level) >= _level_rank(effective):
                effective = row.level
                confirmed_since = pending_since
            continue

        pending_since = None
        confirmation = 0
        if normal:
            repair += 1
            if repair >= repair_days:
                effective = SentinelLevel.NORMAL
                confirmed_since = None
        else:
            repair = 0

    if not rows:
        trust_reasons.append("NO_WARMUP_SEQUENCE")
    elif not trusted and not trust_reasons:
        trust_reasons.append("INSUFFICIENT_NORMAL_REPAIR_SEED")
    return SentinelCausalState(
        effective_level=effective,
        confirmed_since=confirmed_since,
        confirmation_days=confirmation,
        repair_days=repair,
        confirmation_history_trusted=trusted,
        trust_reasons=tuple(sorted(trust_reasons)),
    )


def _timeline_sessions(
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    point: pd.Timestamp,
    reference_panel: Mapping[str, pd.DataFrame],
    universe: AIUniverse,
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
        (member.effective_from for member in universe.members if member.tradable),
        default=point.date(),
    )
    for index, session in enumerate(candidates):
        if session.date() < first_membership:
            continue
        session_text = str(session.date())
        symbols = universe.symbols_as_of(session_text)
        industries = {
            symbol: universe.industry_of(symbol, session_text) for symbol in symbols
        }
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
        declining_name = (
            float(np.mean(np.asarray(visible_returns) < 0.0))
            if visible_returns
            else 0.0
        )
        below_name = float(np.mean(below_ma20)) if below_ma20 else 0.0
        if cfg.group_balanced_reference_enabled:
            declining_group = (
                float(
                    np.mean(
                        [
                            float(np.mean(np.asarray(values) < 0.0))
                            for values in sector_returns.values()
                        ]
                    )
                )
                if sector_returns
                else declining_name
            )
        else:
            declining_group = (
                float(
                    np.mean(
                        [float(np.mean(values)) < 0.0 for values in sector_returns.values()]
                    )
                )
                if sector_returns
                else declining_name
            )
        below_group = (
            float(
                np.mean(
                    [float(np.mean(values)) for values in sector_below20.values()]
                )
            )
            if sector_below20
            else below_name
        )
        name_weight = cfg.risk_breadth_name_weight
        declining_ratio = (
            name_weight * declining_name
            + (1.0 - name_weight) * declining_group
        )
        below_ratio = name_weight * below_name + (1.0 - name_weight) * below_group
        sector_stress = (
            float(
                np.mean(
                    [float(np.mean(values)) < -0.04 for values in sector_returns.values()]
                )
            )
            if sector_returns
            else 0.0
        )
        returns = (
            reference_returns.loc[:session].tail(_MARKET_LOOKBACK)
            if reference_returns is not None
            else pd.DataFrame(
                {
                    symbol: frame["close"].pct_change(fill_method=None)
                    for symbol, frame in reference_panel.items()
                }
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
        snapshot = build_base_market_family_snapshot(
            average_fast_return=(
                float(np.mean(visible_returns)) if visible_returns else 0.0
            ),
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
        flags = dict(snapshot.family_active)
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
    sentinel_rows = tuple(
        row for row in timeline.sentinel_rows if pd.Timestamp(row.date) <= point
    )
    base_rows = tuple(row for row in timeline.base_rows if pd.Timestamp(row.date) <= point)
    if len(sentinel_rows) != len(base_rows):
        raise RuntimeError("base and Sentinel timeline prefixes differ")
    return _assemble_timeline(
        as_of=str(point.date()),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        cfg=cfg,
    )


def risk_evidence_timeline_to_dict(
    timeline: RiskEvidenceTimeline,
) -> dict[str, Any]:
    """Serialize an immutable timeline for a sealed data/config cache."""

    return {
        "as_of": timeline.as_of,
        "sessions": list(timeline.sessions),
        "sentinel_rows": [
            {
                "date": row.date,
                "coverage_status": row.coverage_status.value,
                "confidence": row.confidence,
                "level": row.level.value,
                "freeze_candidate": row.freeze_candidate,
                "family_active": [list(item) for item in row.family_active],
                "reasons": list(row.reasons),
                "weakest_subindustries": list(row.weakest_subindustries),
                "severe_direct": row.severe_direct,
            }
            for row in timeline.sentinel_rows
        ],
        "base_rows": [
            {
                "date": row.date,
                "family_active": [list(item) for item in row.family_active],
                "data_ready": row.data_ready,
            }
            for row in timeline.base_rows
        ],
        "sentinel_first_family_dates": [
            list(item) for item in timeline.sentinel_first_family_dates
        ],
        "base_first_family_dates": [
            list(item) for item in timeline.base_first_family_dates
        ],
        "incremental_families": list(timeline.incremental_families),
        "earlier_families": list(timeline.earlier_families),
        "confirmation_days": timeline.confirmation_days,
        "repair_days": timeline.repair_days,
        "effective_level": timeline.effective_level.value,
        "confirmed_since": timeline.confirmed_since,
        "confirmation_history_trusted": timeline.confirmation_history_trusted,
        "trust_reasons": list(timeline.trust_reasons),
    }


def risk_evidence_timeline_from_dict(payload: Mapping[str, Any]) -> RiskEvidenceTimeline:
    """Validate and restore a timeline cache without executable serialization."""

    required = {
        "as_of",
        "sessions",
        "sentinel_rows",
        "base_rows",
        "sentinel_first_family_dates",
        "base_first_family_dates",
        "incremental_families",
        "earlier_families",
        "confirmation_days",
        "repair_days",
        "effective_level",
        "confirmed_since",
        "confirmation_history_trusted",
        "trust_reasons",
    }
    if set(payload) != required:
        raise ValueError("risk evidence timeline cache fields are invalid")
    sentinel_raw = payload["sentinel_rows"]
    base_raw = payload["base_rows"]
    if not isinstance(sentinel_raw, list) or not isinstance(base_raw, list):
        raise ValueError("risk evidence timeline cache rows are invalid")
    sentinel_rows = tuple(
        SentinelMarketRow(
            date=str(row["date"]),
            coverage_status=WarmupStatus(str(row["coverage_status"])),
            confidence=float(row["confidence"]),
            level=SentinelLevel(str(row["level"])),
            freeze_candidate=bool(row["freeze_candidate"]),
            family_active=tuple(
                (str(item[0]), bool(item[1])) for item in row["family_active"]
            ),
            reasons=tuple(str(item) for item in row["reasons"]),
            weakest_subindustries=tuple(
                str(item) for item in row["weakest_subindustries"]
            ),
            severe_direct=bool(row["severe_direct"]),
        )
        for row in sentinel_raw
        if isinstance(row, Mapping)
    )
    base_rows = tuple(
        BaseMarketRiskRow(
            date=str(row["date"]),
            family_active=tuple(
                (str(item[0]), bool(item[1])) for item in row["family_active"]
            ),
            data_ready=bool(row["data_ready"]),
        )
        for row in base_raw
        if isinstance(row, Mapping)
    )
    if len(sentinel_rows) != len(sentinel_raw) or len(base_rows) != len(base_raw):
        raise ValueError("risk evidence timeline cache contains invalid rows")
    timeline = RiskEvidenceTimeline(
        as_of=str(payload["as_of"]),
        sessions=tuple(str(item) for item in payload["sessions"]),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        sentinel_first_family_dates=tuple(
            (str(item[0]), str(item[1]))
            for item in payload["sentinel_first_family_dates"]
        ),
        base_first_family_dates=tuple(
            (str(item[0]), str(item[1]))
            for item in payload["base_first_family_dates"]
        ),
        incremental_families=tuple(
            str(item) for item in payload["incremental_families"]
        ),
        earlier_families=tuple(str(item) for item in payload["earlier_families"]),
        confirmation_days=int(payload["confirmation_days"]),
        repair_days=int(payload["repair_days"]),
        effective_level=SentinelLevel(str(payload["effective_level"])),
        confirmed_since=(
            None
            if payload["confirmed_since"] is None
            else str(payload["confirmed_since"])
        ),
        confirmation_history_trusted=bool(
            payload["confirmation_history_trusted"]
        ),
        trust_reasons=tuple(str(item) for item in payload["trust_reasons"]),
    )
    if timeline.sessions != tuple(row.date for row in timeline.sentinel_rows):
        raise ValueError("risk evidence timeline cache sessions differ from rows")
    if timeline.sessions != tuple(row.date for row in timeline.base_rows):
        raise ValueError("risk evidence timeline cache base rows differ from sessions")
    return timeline


def build_risk_evidence_timeline(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    reference_panel: Mapping[str, pd.DataFrame],
    reference_returns: pd.DataFrame | None,
    universe: AIUniverse,
    cfg: SystemConfig,
) -> RiskEvidenceTimeline:
    """Rebuild complete PIT market history without current account inputs."""

    point = pd.Timestamp(as_of).normalize()
    sessions = _timeline_sessions(
        broad_frame,
        tech_frame,
        point,
        reference_panel,
        universe,
    )
    name_observations, name_returns = _prepared_name_observations(reference_panel)
    rolling_correlation, complete_correlation = _rolling_correlation_cache(
        name_returns
    )
    correlation_positions: dict[tuple[str, ...], np.ndarray] = {}
    broad_fast, broad_medium = _prepared_index_returns(broad_frame)
    tech_fast, tech_medium = _prepared_index_returns(tech_frame)
    sentinel_rows: list[SentinelMarketRow] = []
    base_rows: list[BaseMarketRiskRow] = []
    for session in sessions:
        session_text = str(session.date())
        symbols = universe.symbols_as_of(session_text)
        panel = {
            symbol: _prefix(reference_panel[symbol], session)
            for symbol in symbols
            if symbol in reference_panel
        }
        industries = {
            symbol: universe.industry_of(symbol, session_text)
            for symbol in symbols
        }
        broad_prefix = _prefix(broad_frame, session)
        tech_prefix = _prefix(tech_frame, session)
        observed = frozenset(
            symbol for symbol in symbols if session in reference_panel[symbol].index
        )
        counts = {
            symbol: int(reference_panel[symbol].index.searchsorted(session, side="right"))
            for symbol in symbols
            if symbol in reference_panel
        }
        warmed = frozenset(
            symbol for symbol in observed if counts.get(symbol, 0) >= 21
        )
        new = observed - warmed
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
            new_symbols=new,
            point_in_time_industries=industries,
            held_symbols=(),
            missing_indices=missing_indices,
        )
        names = {
            symbol: name_observations[symbol][session]
            for symbol in symbols
            if session in name_observations.get(symbol, {})
        }
        evidence = build_market_evidence_from_observations(
            as_of=session_text,
            names=names,
            point_in_time_industries=industries,
            held_symbols=(),
            leader_symbols=(),
            capital_drawdown=None,
            broad_fast=broad_fast.get(session, 0.0),
            broad_medium=broad_medium.get(session, 0.0),
            tech_fast=tech_fast.get(session, 0.0),
            tech_medium=tech_medium.get(session, 0.0),
            median_correlation=_prepared_correlation(
                session=session,
                symbols=tuple(sorted(names)),
                returns=name_returns,
                rolling=rolling_correlation,
                complete=complete_correlation,
                positions=correlation_positions,
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
        active = {
            family: family in assessment.evidence_families
            for family in _MARKET_FAMILIES
        }
        sentinel_rows.append(
            SentinelMarketRow(
                date=session_text,
                coverage_status=assessment.coverage.status,
                confidence=assessment.confidence,
                level=assessment.level,
                freeze_candidate=(
                    assessment.freeze_new_risk and any(active.values())
                ),
                family_active=tuple(sorted(active.items())),
                reasons=assessment.reasons,
                weakest_subindustries=assessment.weakest_subindustries,
                severe_direct=_severe_direct(assessment, cfg),
            )
        )
        base_rows.append(
            _base_row(
                session=session,
                broad_frame=broad_prefix,
                tech_frame=tech_prefix,
                reference_panel=panel,
                reference_returns=reference_returns,
                industries=industries,
                cfg=cfg,
            )
        )

    return _assemble_timeline(
        as_of=str(point.date()),
        sentinel_rows=tuple(sentinel_rows),
        base_rows=tuple(base_rows),
        cfg=cfg,
    )
