"""Strict, executable-free encoding for causal risk-evidence timelines."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import (
    BaseMarketRiskRow,
    RiskEvidenceTimeline,
    SentinelLevel,
    SentinelMarketRow,
    WarmupStatus,
)


def encode_risk_evidence_timeline(timeline: RiskEvidenceTimeline) -> dict[str, Any]:
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
        "sentinel_first_family_dates": [list(item) for item in timeline.sentinel_first_family_dates],
        "base_first_family_dates": [list(item) for item in timeline.base_first_family_dates],
        "incremental_families": list(timeline.incremental_families),
        "earlier_families": list(timeline.earlier_families),
        "confirmation_days": timeline.confirmation_days,
        "repair_days": timeline.repair_days,
        "effective_level": timeline.effective_level.value,
        "confirmed_since": timeline.confirmed_since,
        "confirmation_history_trusted": timeline.confirmation_history_trusted,
        "trust_reasons": list(timeline.trust_reasons),
    }


def _timeline_cache_rows(
    *,
    sentinel_raw: list[Any],
    base_raw: list[Any],
) -> tuple[tuple[SentinelMarketRow, ...], tuple[BaseMarketRiskRow, ...]]:
    sentinel_rows = tuple(
        SentinelMarketRow(
            date=str(row["date"]),
            coverage_status=WarmupStatus(str(row["coverage_status"])),
            confidence=float(row["confidence"]),
            level=SentinelLevel(str(row["level"])),
            freeze_candidate=bool(row["freeze_candidate"]),
            family_active=tuple((str(item[0]), bool(item[1])) for item in row["family_active"]),
            reasons=tuple(str(item) for item in row["reasons"]),
            weakest_subindustries=tuple(str(item) for item in row["weakest_subindustries"]),
            severe_direct=bool(row["severe_direct"]),
        )
        for row in sentinel_raw
        if isinstance(row, Mapping)
    )
    base_rows = tuple(
        BaseMarketRiskRow(
            date=str(row["date"]),
            family_active=tuple((str(item[0]), bool(item[1])) for item in row["family_active"]),
            data_ready=bool(row["data_ready"]),
        )
        for row in base_raw
        if isinstance(row, Mapping)
    )
    if len(sentinel_rows) != len(sentinel_raw) or len(base_rows) != len(base_raw):
        raise ValueError("risk evidence timeline cache contains invalid rows")
    return sentinel_rows, base_rows


def decode_risk_evidence_timeline(payload: Mapping[str, Any]) -> RiskEvidenceTimeline:
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
    sentinel_rows, base_rows = _timeline_cache_rows(
        sentinel_raw=sentinel_raw,
        base_raw=base_raw,
    )
    timeline = RiskEvidenceTimeline(
        as_of=str(payload["as_of"]),
        sessions=tuple(str(item) for item in payload["sessions"]),
        sentinel_rows=sentinel_rows,
        base_rows=base_rows,
        sentinel_first_family_dates=tuple(
            (str(item[0]), str(item[1])) for item in payload["sentinel_first_family_dates"]
        ),
        base_first_family_dates=tuple(
            (str(item[0]), str(item[1])) for item in payload["base_first_family_dates"]
        ),
        incremental_families=tuple(str(item) for item in payload["incremental_families"]),
        earlier_families=tuple(str(item) for item in payload["earlier_families"]),
        confirmation_days=int(payload["confirmation_days"]),
        repair_days=int(payload["repair_days"]),
        effective_level=SentinelLevel(str(payload["effective_level"])),
        confirmed_since=(None if payload["confirmed_since"] is None else str(payload["confirmed_since"])),
        confirmation_history_trusted=bool(payload["confirmation_history_trusted"]),
        trust_reasons=tuple(str(item) for item in payload["trust_reasons"]),
    )
    if timeline.sessions != tuple(row.date for row in timeline.sentinel_rows):
        raise ValueError("risk evidence timeline cache sessions differ from rows")
    if timeline.sessions != tuple(row.date for row in timeline.base_rows):
        raise ValueError("risk evidence timeline cache base rows differ from sessions")
    return timeline


__all__ = ("decode_risk_evidence_timeline", "encode_risk_evidence_timeline")
