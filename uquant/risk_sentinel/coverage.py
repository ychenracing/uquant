"""Causal component coverage and warmup health."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from .models import CoverageHealth, WarmupStatus

_INDEX_NAMES = (("sh000300", "broad"), ("sh000682", "tech"))


def _prefix(frame: pd.DataFrame | None, point: pd.Timestamp) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("Sentinel input frames require a DatetimeIndex")
    return frame.loc[:point]


def assess_coverage(
    *,
    as_of: str,
    broad_frame: pd.DataFrame,
    tech_frame: pd.DataFrame,
    expected_symbols: tuple[str, ...],
    reference_panel: Mapping[str, pd.DataFrame],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    minimum_history: int = 21,
) -> CoverageHealth:
    """Evaluate exact-date coverage without converting missing data into safety."""

    if minimum_history < 2:
        raise ValueError("Sentinel minimum history must be at least two sessions")
    point = pd.Timestamp(as_of).normalize()
    expected = tuple(sorted(set(expected_symbols)))
    observed: set[str] = set()
    warmed: set[str] = set()
    new: set[str] = set()
    stale: set[str] = set()
    for symbol in expected:
        visible = _prefix(reference_panel.get(symbol), point)
        if visible.empty:
            continue
        last = pd.Timestamp(visible.index[-1]).normalize()
        if last == point:
            observed.add(symbol)
            if len(visible) >= minimum_history:
                warmed.add(symbol)
            else:
                new.add(symbol)
        else:
            stale.add(symbol)

    missing_indices: list[str] = []
    for index_name, frame_name in _INDEX_NAMES:
        frame = broad_frame if frame_name == "broad" else tech_frame
        visible = _prefix(frame, point)
        if (
            visible.empty
            or len(visible) < minimum_history
            or pd.Timestamp(visible.index[-1]).normalize() != point
        ):
            missing_indices.append(index_name)

    return build_coverage_health(
        expected_symbols=expected,
        observed_symbols=frozenset(observed),
        warmed_symbols=frozenset(warmed),
        stale_symbols=frozenset(stale),
        new_symbols=frozenset(new),
        point_in_time_industries=point_in_time_industries,
        held_symbols=held_symbols,
        missing_indices=tuple(missing_indices),
    )


def build_coverage_health(
    *,
    expected_symbols: tuple[str, ...],
    observed_symbols: frozenset[str],
    warmed_symbols: frozenset[str],
    stale_symbols: frozenset[str],
    new_symbols: frozenset[str],
    point_in_time_industries: Mapping[str, str],
    held_symbols: tuple[str, ...],
    missing_indices: tuple[str, ...],
) -> CoverageHealth:
    """Apply the canonical coverage policy to precomputed causal facts."""

    expected = tuple(sorted(set(expected_symbols)))
    observed = set(observed_symbols)
    warmed = set(warmed_symbols)
    stale = set(stale_symbols)
    new = set(new_symbols)
    component_observation = len(observed) / len(expected) if expected else 0.0
    reference_warmup = len(warmed) / len(expected) if expected else 0.0
    expected_groups = {
        point_in_time_industries.get(symbol, "unknown")
        for symbol in expected
        if point_in_time_industries.get(symbol, "unknown") != "unknown"
    }
    observed_groups = {
        point_in_time_industries.get(symbol, "unknown")
        for symbol in observed
        if point_in_time_industries.get(symbol, "unknown") != "unknown"
    }
    subindustry_coverage = (
        len(observed_groups) / len(expected_groups) if expected_groups else 0.0
    )
    held_industry_mapping = (
        sum(
            point_in_time_industries.get(symbol, "unknown") != "unknown"
            for symbol in held_symbols
        )
        / len(held_symbols)
        if held_symbols
        else 1.0
    )
    confidence = (
        0.45 * component_observation
        + 0.35 * subindustry_coverage
        + 0.20 * held_industry_mapping
    )

    if missing_indices or confidence < 0.60 or reference_warmup < 0.50:
        status = WarmupStatus.NOT_READY
    elif confidence < 0.85 or reference_warmup < 0.80 or new or stale:
        status = WarmupStatus.DEGRADED
    else:
        status = WarmupStatus.READY
    return CoverageHealth(
        status=status,
        confidence=confidence,
        component_observation=component_observation,
        subindustry_coverage=subindustry_coverage,
        held_industry_mapping=held_industry_mapping,
        reference_warmup=reference_warmup,
        missing_indices=tuple(sorted(missing_indices)),
        new_symbols=tuple(new),
        stale_symbols=tuple(stale),
    )
