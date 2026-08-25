"""Point-in-time industry breadth and rotation evidence.

The module owns no positions and emits no target weights.  It converts the
same frozen, causal cross-section used by leader scoring into auditable
industry signals that portfolio policy may use as one hand-off condition.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from .contracts.universe import default_ai_universe

_FROZEN_COMPATIBILITY_LABELS = MappingProxyType(
    {
        "advanced_packaging": "packaging",
        "semicap": "equipment",
        "storage": "memory",
    }
)


def production_industries() -> Mapping[str, str]:
    """Derive Phase 1's legacy decision buckets from the canonical manifest.

    The manifest is the sole source of production membership and canonical
    taxonomy.  These labels preserve the accepted Phase 1 economic behavior
    while later validation consumes the normalized names directly.
    """
    universe = default_ai_universe()
    return MappingProxyType(
        {
            member.symbol: _FROZEN_COMPATIBILITY_LABELS.get(member.industry, member.industry)
            for member in universe.members
        }
    )


@dataclass(frozen=True, slots=True)
class IndustrySignal:
    """Causal strength, breadth, and coverage for one industry bucket."""

    industry: str
    score: float
    raw_score: float
    confidence: float
    member_count: int
    return20: float
    return60: float
    return120: float
    breadth20: float
    breadth60: float
    acceleration: float


def _finite_median(values: Iterable[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(value)]
    return float(np.median(finite)) if finite else float("nan")


def _percentile(value: float, reference: Iterable[float]) -> float:
    values = np.asarray(
        [float(item) for item in reference if math.isfinite(item)],
        dtype=float,
    )
    if not math.isfinite(value) or len(values) < 2:
        return 0.5
    return float((values < value).sum() + 0.5 * (values == value).sum()) / len(values)


def compute_industry_signals(
    *,
    raw: Mapping[str, Mapping[str, float]],
    reference_symbols: Iterable[str],
    industries: Mapping[str, str],
    minimum_members: int,
    hierarchical: bool = True,
) -> dict[str, IndustrySignal]:
    """Aggregate robust multi-horizon signals from visible reference members.

    Every input value already belongs to the current decision date.  Medians
    limit the influence of one extreme security, while breadth prevents a
    single winner from making an otherwise weak group look healthy.  Sparse
    groups remain visible but their score is shrunk toward neutral.
    """
    grouped: dict[str, list[Mapping[str, float]]] = {}
    for symbol in reference_symbols:
        item = raw.get(symbol)
        industry = industries.get(symbol, "unknown")
        if item is None or industry == "unknown":
            continue
        grouped.setdefault(industry, []).append(item)

    aggregates: dict[str, dict[str, float]] = {}
    for industry, members in grouped.items():
        aggregates[industry] = {
            "return20": _finite_median(item["ret20"] for item in members),
            "return60": _finite_median(item["ret60"] for item in members),
            "return120": _finite_median(item["ret120"] for item in members),
            "breadth20": float(np.mean([item.get("above20", 0.0) > 0.5 for item in members])),
            "breadth60": float(np.mean([item.get("above60", 0.0) > 0.5 for item in members])),
            "acceleration": _finite_median(item["accel"] for item in members),
            "member_count": float(len(members)),
        }

    raw_scores: dict[str, float] = {}
    for industry, item in aggregates.items():
        raw_scores[industry] = (
            0.20
            * _percentile(
                item["return20"],
                (other["return20"] for other in aggregates.values()),
            )
            + 0.27
            * _percentile(
                item["return60"],
                (other["return60"] for other in aggregates.values()),
            )
            + 0.18
            * _percentile(
                item["return120"],
                (other["return120"] for other in aggregates.values()),
            )
            + 0.15 * item["breadth20"]
            + 0.12 * item["breadth60"]
            + 0.08
            * _percentile(
                item["acceleration"],
                (other["acceleration"] for other in aggregates.values()),
            )
        )
    parent_score = (
        float(np.mean(list(raw_scores.values()))) if raw_scores else 0.5
    )
    signals: dict[str, IndustrySignal] = {}
    for industry, item in aggregates.items():
        raw_score = raw_scores[industry]
        member_count = int(item["member_count"])
        confidence = (
            member_count / (member_count + max(1, minimum_members))
            if hierarchical
            else min(1.0, member_count / max(1, minimum_members))
        )
        prior = parent_score if hierarchical else 0.5
        score = prior + confidence * (raw_score - prior)
        signals[industry] = IndustrySignal(
            industry=industry,
            score=float(min(1.0, max(0.0, score))),
            raw_score=float(min(1.0, max(0.0, raw_score))),
            confidence=confidence,
            member_count=member_count,
            return20=item["return20"],
            return60=item["return60"],
            return120=item["return120"],
            breadth20=item["breadth20"],
            breadth60=item["breadth60"],
            acceleration=item["acceleration"],
        )
    return signals
