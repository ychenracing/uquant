"""Causal three-way Risk Differential replay and offline outcome analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from research.risk_differential_models import RiskDifferentialEvent, RiskTraceRow, canonical_bytes

BOOLEAN_AXES = (
    "market_velocity",
    "breadth_structure",
    "covariance_stress",
    "leadership_damage",
    "live_book_damage",
    "capital_damage",
    "concentration_damage",
    "block_new_entries",
    "block_pyramiding",
)


@dataclass(frozen=True, slots=True)
class AlignedRiskDay:
    date: str
    trade: RiskTraceRow
    base: RiskTraceRow
    sentinel: RiskTraceRow


def classify_boolean_axis(*, trade: bool | None, base: bool | None, sentinel: bool | None) -> str:
    if None in (trade, base, sentinel):
        return "NOT_COMPARABLE"
    if trade == base == sentinel:
        return "AGREE_ALL"
    if trade and not base and not sentinel:
        return "TRADE_ONLY"
    if base and not trade and not sentinel:
        return "BASE_ONLY"
    if sentinel and not trade and not base:
        return "SENTINEL_ONLY"
    if trade and sentinel and not base:
        return "TRADE_AND_SENTINEL_NOT_BASE"
    if trade and base and not sentinel:
        return "TRADE_AND_BASE_NOT_SENTINEL"
    if base and sentinel and not trade:
        return "BASE_AND_SENTINEL_NOT_TRADE"
    return "NOT_COMPARABLE"


def align_three_way(
    trade: Sequence[RiskTraceRow],
    base: Sequence[RiskTraceRow],
    sentinel: Sequence[RiskTraceRow],
) -> tuple[AlignedRiskDay, ...]:
    dates = tuple(item.date for item in trade)
    if dates != tuple(item.date for item in base) or dates != tuple(item.date for item in sentinel):
        raise ValueError("three-way traces require exactly aligned calendars")
    return tuple(
        AlignedRiskDay(date=t.date, trade=t, base=b, sentinel=s)
        for t, b, s in zip(trade, base, sentinel, strict=True)
    )


def differential_events(
    days: Sequence[AlignedRiskDay],
    *,
    actionability: dict[str, dict[str, float | int]] | None = None,
) -> tuple[RiskDifferentialEvent, ...]:
    actionability = actionability or {}
    events: list[RiskDifferentialEvent] = []
    for day in days:
        counts = actionability.get(day.date, {})
        for axis in BOOLEAN_AXES:
            trade_value = getattr(day.trade, axis)
            base_value = getattr(day.base, axis)
            sentinel_value = getattr(day.sentinel, axis)
            classification = classify_boolean_axis(
                trade=trade_value, base=base_value, sentinel=sentinel_value
            )
            events.append(
                RiskDifferentialEvent(
                    date=day.date,
                    axis=axis,
                    classification=classification,
                    trade_value=trade_value,
                    base_value=base_value,
                    sentinel_value=sentinel_value,
                    actionable_buy_intents=int(counts.get("buy", 0)),
                    actionable_pyramid_intents=int(counts.get("pyramid", 0)),
                    base_already_protected=bool(day.base.block_new_entries or day.base.block_pyramiding),
                    existing_gross_exposure=float(counts.get("gross", 0.0)),
                )
            )
    return tuple(events)


def prefix_trace_sha256(rows: Sequence[RiskTraceRow], *, as_of: str) -> str:
    payload = [asdict(item) for item in rows if item.date <= as_of]
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def normalize_trade_governance(row: dict[str, Any]) -> RiskTraceRow:
    opinion = row.get("risk_opinion", row)
    if not isinstance(opinion, dict):
        return RiskTraceRow.empty(str(row.get("date", "")), "trade")
    status = str(row.get("warmup_status", opinion.get("warmup_status", "READY")))
    if status not in {"READY", "DEGRADED", "NOT_READY"}:
        status = "UNOBSERVABLE"
    if status in {"NOT_READY", "UNOBSERVABLE"}:
        return RiskTraceRow.empty(str(opinion.get("date", row.get("date", ""))), "trade", status=status)
    reasons = tuple(str(item) for item in opinion.get("reason_codes", ()))
    level = int(opinion.get("risk_level", 0))
    families = opinion.get("family_active")
    if not isinstance(families, dict):
        families = {}
    gross = opinion.get("recommended_gross_cap")
    if gross is None and row.get("gross_cap_derived_from_pinned_level_contract"):
        gross = {0: 1.0, 1: 0.85, 2: 0.60, 3: 0.35}.get(level)
    return replace(
        RiskTraceRow.empty(str(opinion["date"]), "trade", status=status, severity_rank=level),
        confidence=float(opinion.get("risk_confidence", 0.0)),
        market_velocity=families.get("market_velocity"),
        breadth_structure=families.get("breadth_structure"),
        covariance_stress=families.get("covariance_stress"),
        leadership_damage=families.get("leadership_damage"),
        live_book_damage=families.get("live_book_damage"),
        capital_damage=families.get("capital_damage"),
        concentration_damage=families.get("concentration_damage"),
        block_new_entries=bool(opinion.get("block_new_entries")),
        block_pyramiding=bool(opinion.get("block_pyramids")),
        recommended_gross_cap=float(gross) if gross is not None else None,
        weakest_clusters=tuple(str(item) for item in opinion.get("weakest_clusters", ())),
        action_candidates=tuple(str(item) for item in row.get("action_candidates", ())),
        execution_owner=str(row.get("execution_owner")) if row.get("execution_owner") else None,
        reasons=reasons,
    )


def normalize_uquant_decision(decision: Any) -> tuple[RiskTraceRow, RiskTraceRow]:
    """Extract Base and Sentinel from one sealed production decision trace."""

    evidence = {name: json.loads(value) for name, value in decision.risk_evidence}
    base_families = evidence.get("base_family_active", {})
    sentinel_families = evidence.get("sentinel_family_active", {})
    base_level = str(evidence.get("severity", decision.risk))
    rank_by_level = {"NORMAL": 0, "CAUTION": 1, "DEFENSIVE": 2, "CRITICAL": 3}
    base_rank = rank_by_level.get(base_level, 0)
    sentinel_level = str(evidence.get("sentinel_causal_effective_level", "NORMAL"))
    sentinel_rank = rank_by_level.get(sentinel_level, 0)
    status = str(evidence.get("sentinel_causal_coverage_status", "UNOBSERVABLE"))
    if status not in {"READY", "DEGRADED", "NOT_READY"}:
        status = "UNOBSERVABLE"
    common = {
        "date": str(decision.date),
        "weakest_clusters": (),
        "action_candidates": (),
        "reasons": (),
    }
    base = RiskTraceRow(
        system="uquant_base",
        status="READY",
        confidence=1.0,
        severity_rank=base_rank,
        level=base_level,
        market_velocity=bool(base_families.get("market_velocity", False)),
        breadth_structure=bool(base_families.get("breadth_structure", False)),
        covariance_stress=bool(base_families.get("covariance_stress", False)),
        leadership_damage=bool(base_families.get("leadership_damage", False)),
        live_book_damage=bool(base_families.get("live_book_damage", False)),
        capital_damage=bool(base_families.get("capital_damage", False)),
        concentration_damage=bool(evidence.get("sector_guard_active", False)),
        block_new_entries=bool(evidence.get("base_freeze_new_risk", False)),
        block_pyramiding=bool(evidence.get("base_freeze_new_risk", False)),
        recommended_gross_cap=float(evidence.get("base_target_gross_cap", 1.0)),
        execution_owner="uquant_base_risk",
        **common,
    )
    if status in {"NOT_READY", "UNOBSERVABLE"}:
        sentinel = RiskTraceRow.empty(str(decision.date), "uquant_sentinel", status=status)
    else:
        assessment = evidence.get("sentinel_assessment", {})
        sentinel = RiskTraceRow(
            system="uquant_sentinel",
            status=status,
            confidence=float(evidence.get("sentinel_causal_confidence", 0.0)),
            severity_rank=sentinel_rank,
            level=sentinel_level,
            market_velocity=bool(sentinel_families.get("market_velocity", False)),
            breadth_structure=bool(sentinel_families.get("breadth_structure", False)),
            covariance_stress=bool(sentinel_families.get("covariance_stress", False)),
            leadership_damage=bool(sentinel_families.get("leadership_damage", False)),
            live_book_damage=bool(sentinel_families.get("live_book_damage", False)),
            capital_damage=bool(sentinel_families.get("capital_damage", False)),
            concentration_damage=None,
            block_new_entries=bool(evidence.get("sentinel_freeze_new_risk", False)),
            block_pyramiding=bool(evidence.get("sentinel_freeze_new_risk", False)),
            recommended_gross_cap=(
                float(assessment["suggested_gross_cap"])
                if isinstance(assessment, dict) and assessment.get("suggested_gross_cap") is not None
                else None
            ),
            weakest_clusters=tuple(evidence.get("sentinel_causal_weakest_subindustries", ())),
            action_candidates=("FREEZE_NEW_RISK",) if evidence.get("sentinel_freeze_new_risk") else (),
            execution_owner="uquant_base_risk",
            reasons=tuple(evidence.get("sentinel_causal_reasons", ())),
            date=str(decision.date),
        )
    return base, sentinel


def merge_episodes(
    dates: Sequence[str],
    *,
    calendar: Sequence[str] | None = None,
    max_gap_sessions: int = 5,
) -> tuple[str, ...]:
    if not dates:
        return ()
    ordered = tuple(sorted(set(dates)))
    positions = {date: index for index, date in enumerate(calendar or ordered)}
    if any(date not in positions for date in ordered):
        raise ValueError("episode dates must belong to the supplied calendar")
    selected = [ordered[0]]
    last_position = positions[ordered[0]]
    for date in ordered[1:]:
        position = positions[date]
        if position - last_position > max_gap_sessions + 1:
            selected.append(date)
        last_position = position
    return tuple(selected)


def forward_outcomes(
    event_dates: Sequence[str],
    dates: Sequence[str],
    portfolio: Sequence[float],
    market: Sequence[float],
    *,
    horizons: tuple[int, ...] = (1, 3, 5, 10, 20),
) -> list[dict[str, Any]]:
    if not (len(dates) == len(portfolio) == len(market)):
        raise ValueError("outcome series require aligned calendars")
    positions = {date: index for index, date in enumerate(dates)}
    rows: list[dict[str, Any]] = []
    for event in event_dates:
        pos = positions[event]
        outcomes: dict[str, Any] = {}
        for horizon in horizons:
            end = min(pos + horizon, len(dates) - 1)
            p0, m0 = portfolio[pos], market[pos]
            p_window = portfolio[pos : end + 1]
            running_peak = p_window[0]
            max_drawdown = 0.0
            for value in p_window:
                running_peak = max(running_peak, value)
                max_drawdown = min(max_drawdown, value / running_peak - 1.0)
            outcomes[f"{horizon}d"] = {
                "forward_portfolio_return": portfolio[end] / p0 - 1.0,
                "forward_market_return": market[end] / m0 - 1.0,
                "max_drawdown": max_drawdown,
                "acute_loss": min(value / p0 - 1.0 for value in p_window),
                "realized_shock": max_drawdown <= -0.08,
            }
        rows.append({"date": event, "outcomes": outcomes})
    return rows


def append_observation(path: Path, payload: dict[str, Any], *, activation: str) -> dict[str, Any]:
    date = str(payload.get("date", ""))
    if date < activation:
        raise ValueError("observation predates lane activation")
    previous_hash = "0" * 64
    previous_date = ""
    if path.exists():
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        if lines:
            previous = json.loads(lines[-1])
            previous_hash = str(previous["record_sha256"])
            previous_date = str(previous["date"])
    if previous_date and date <= previous_date:
        raise ValueError("observation journal is append-only")
    record = {**payload, "previous_sha256": previous_hash}
    record["record_sha256"] = hashlib.sha256(canonical_bytes(record)).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return record
