"""Account-free causal market history for base risk and Risk Sentinel."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from uquant.config import SystemConfig
from uquant.features import scalar
from uquant.reference import build_reference_context
from uquant.risk import build_base_market_family_snapshot
from uquant.validation.universe import AIUniverse

from .integration import _severe_direct
from .models import (
    BaseMarketRiskRow,
    RiskEvidenceTimeline,
    SentinelCausalState,
    SentinelLevel,
    SentinelMarketRow,
    WarmupStatus,
)
from .service import evaluate_sentinel

_MARKET_FAMILIES = (
    "breadth_structure",
    "covariance_stress",
    "market_velocity",
)


def _prefix(frame: pd.DataFrame, point: pd.Timestamp) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("risk evidence timeline requires DatetimeIndex frames")
    return frame.loc[:point]


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
    pending_level: SentinelLevel | None = None
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
            pending_level = None
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
                pending_level = row.level
                pending_since = row.date
            elif pending_level is row.level:
                confirmation += 1
            else:
                pending_level = row.level
                pending_since = row.date
                confirmation = 1
            if confirmation >= confirm_days and _level_rank(row.level) >= _level_rank(effective):
                effective = row.level
                confirmed_since = pending_since
            continue

        pending_level = None
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
) -> tuple[pd.Timestamp, ...]:
    if not isinstance(broad_frame.index, pd.DatetimeIndex) or not isinstance(
        tech_frame.index,
        pd.DatetimeIndex,
    ):
        raise ValueError("risk evidence timeline requires DatetimeIndex indices")
    candidates = sorted(
        {
            pd.Timestamp(item).normalize()
            for item in broad_frame.index.union(tech_frame.index)
            if pd.Timestamp(item).normalize() <= point
        }
    )
    for index, session in enumerate(candidates):
        if len(broad_frame.loc[:session]) >= 21 and len(tech_frame.loc[:session]) >= 21:
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
        visible_returns = [
            scalar(frame.loc[session], "ret5")
            for frame in reference_panel.values()
            if session in frame.index and math.isfinite(scalar(frame.loc[session], "ret5"))
        ]
        context = build_reference_context(
            date=session,
            panel=reference_panel,
            industries=industries,
            cfg=cfg,
            reference_returns=(
                reference_returns.loc[:session]
                if reference_returns is not None
                else None
            ),
        )
        tech_returns = tech_frame.loc[:session, "close"].pct_change(fill_method=None)
        recent_vol = float(tech_returns.tail(10).std(ddof=0))
        normal_vol = float(tech_returns.tail(60).std(ddof=0))
        volatility_ratio = recent_vol / normal_vol if normal_vol > 1e-12 else 1.0
        snapshot = build_base_market_family_snapshot(
            average_fast_return=(
                float(np.mean(visible_returns)) if visible_returns else 0.0
            ),
            declining_ratio=context.declining,
            below_ma20_ratio=1.0 - context.breadth20,
            sector_stress_ratio=context.sector_stress,
            median_correlation=context.median_correlation,
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
    sessions = _timeline_sessions(broad_frame, tech_frame, point)
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
        assessment = evaluate_sentinel(
            as_of=session_text,
            broad_frame=broad_prefix,
            tech_frame=tech_prefix,
            reference_panel=panel,
            point_in_time_industries=industries,
            held_symbols=(),
            leader_symbols=(),
            capital_drawdown=None,
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

    sentinel_tuple = tuple(sentinel_rows)
    base_tuple = tuple(base_rows)
    state = fold_sentinel_market_state(
        sentinel_tuple,
        confirm_days=cfg.risk_sentinel_confirm_days,
        repair_days=cfg.risk_sentinel_repair_days,
    )
    sentinel_first = _first_dates(sentinel_tuple)
    base_first = _first_dates(base_tuple)
    sentinel_first_map = dict(sentinel_first)
    base_first_map = dict(base_first)
    current_sentinel = set(sentinel_tuple[-1].active_families) if sentinel_tuple else set()
    current_base = set(base_tuple[-1].active_families) if base_tuple else set()
    incremental = tuple(sorted(current_sentinel - current_base))
    earlier = tuple(
        sorted(
            family
            for family, first in sentinel_first_map.items()
            if family not in base_first_map or first < base_first_map[family]
        )
    )
    return RiskEvidenceTimeline(
        as_of=str(point.date()),
        sessions=tuple(row.date for row in sentinel_tuple),
        sentinel_rows=sentinel_tuple,
        base_rows=base_tuple,
        sentinel_first_family_dates=sentinel_first,
        base_first_family_dates=base_first,
        incremental_families=incremental,
        earlier_families=earlier,
        confirmation_days=state.confirmation_days,
        repair_days=state.repair_days,
        effective_level=state.effective_level,
        confirmed_since=state.confirmed_since,
        confirmation_history_trusted=state.confirmation_history_trusted,
        trust_reasons=state.trust_reasons,
    )
