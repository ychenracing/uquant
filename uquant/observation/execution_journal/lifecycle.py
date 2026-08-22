"""Execution-journal record and lifecycle validation."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import cast

from .models import (
    _BROKER_ORDER_ID,
    _PLAN_ID,
    _SYMBOL,
    JournalRecord,
    JournalStatus,
)


def _timestamp(value: str, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"journal {field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"journal {field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"journal {field} must include a UTC offset")
    return parsed


def _date(value: str, *, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"journal {field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"journal {field} must be an ISO date") from exc


def _positive_number(value: float, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"journal {field} must be finite")
    converted = float(value)
    if converted <= 0:
        raise ValueError(f"journal {field} must be positive")
    return converted


def _positive_shares(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"journal {field} must be a positive integer")
    return value


def _validate_record(record: JournalRecord) -> None:
    if not _PLAN_ID.fullmatch(record.plan_id):
        raise ValueError("execution journal plan_id is malformed")
    recorded_at = _timestamp(record.recorded_at, field="recorded_at")
    decision_date = _date(record.decision_date, field="decision_date")
    if decision_date > recorded_at.date():
        raise ValueError("execution journal decision_date follows recorded_at")
    if record.planned_weight is not None and (
        isinstance(record.planned_weight, bool)
        or not isinstance(record.planned_weight, (int, float))
        or not math.isfinite(float(record.planned_weight))
        or not 0.0 <= float(record.planned_weight) <= 1.0
    ):
        raise ValueError("journal planned_weight must be finite and between zero and one")
    if record.broker_order_id is not None and not _BROKER_ORDER_ID.fullmatch(record.broker_order_id):
        raise ValueError("journal broker_order_id is malformed")
    if record.status is JournalStatus.PLANNED:
        if (
            record.symbol is None
            or not _SYMBOL.fullmatch(record.symbol)
            or record.side not in {"BUY", "SELL"}
        ):
            raise ValueError("planned journal symbol or side is malformed")
        if record.planned_price is None or record.planned_shares is None:
            raise ValueError("planned journal event lacks price or shares")
        _positive_number(record.planned_price, field="planned_price")
        _positive_shares(record.planned_shares, field="planned_shares")
        expected_null = (
            record.next_open,
            record.actual_time,
            record.actual_price,
            record.actual_shares,
            record.manual_skip,
            record.slippage_per_share,
            record.slippage_bps,
            record.slippage_value,
            record.broker_order_id,
        )
        if any(value is not None for value in expected_null):
            raise ValueError("planned journal event contains execution data")
    elif record.status is JournalStatus.FILLED:
        if (
            record.next_open is None
            or record.actual_time is None
            or record.actual_price is None
            or record.actual_shares is None
        ):
            raise ValueError("filled journal event lacks execution data")
        _positive_number(record.next_open, field="next_open")
        _timestamp(record.actual_time, field="actual_time")
        _positive_number(record.actual_price, field="actual_price")
        _positive_shares(record.actual_shares, field="actual_shares")
        if any(
            value is None for value in (record.slippage_per_share, record.slippage_bps, record.slippage_value)
        ):
            raise ValueError("filled journal event lacks derived slippage")
        for field in ("slippage_per_share", "slippage_bps", "slippage_value"):
            value = getattr(record, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"journal {field} must be finite")
        if record.schema_version == 1:
            if any(
                value is not None
                for value in (
                    record.symbol,
                    record.side,
                    record.planned_price,
                    record.planned_shares,
                    record.manual_skip,
                )
            ):
                raise ValueError("filled journal event duplicates planned or skip data")
        elif (
            record.symbol is None
            or record.side is None
            or record.planned_price is None
            or record.planned_shares is None
            or record.manual_skip is not None
        ):
            raise ValueError("filled journal event lacks its immutable plan identity")
    else:
        if not isinstance(record.manual_skip, str) or not record.manual_skip.strip():
            raise ValueError("skipped journal event requires a manual skip reason")
        if record.next_open is None:
            raise ValueError("skipped journal event requires the observed next open")
        _positive_number(record.next_open, field="next_open")
        unrelated = [
            record.actual_time,
            record.actual_price,
            record.actual_shares,
            record.slippage_per_share,
            record.slippage_bps,
            record.slippage_value,
            record.broker_order_id,
        ]
        if record.schema_version == 1:
            unrelated.extend((record.symbol, record.side, record.planned_price, record.planned_shares))
        elif any(
            value is None
            for value in (record.symbol, record.side, record.planned_price, record.planned_shares)
        ):
            raise ValueError("skipped journal event lacks its immutable plan identity")
        if any(value is not None for value in unrelated):
            raise ValueError("skipped journal event contains unrelated data")


def _validate_lifecycle(records: list[JournalRecord] | tuple[JournalRecord, ...]) -> None:
    plans: dict[str, JournalRecord] = {}
    filled_shares: dict[str, int] = {}
    plan_opens: dict[str, float] = {}
    terminal: set[str] = set()
    prior_recorded: datetime | None = None
    for record in records:
        recorded = _timestamp(record.recorded_at, field="recorded_at")
        if prior_recorded is not None and recorded < prior_recorded:
            raise ValueError("execution journal chronology is not monotonic")
        prior_recorded = recorded
        if record.status is JournalStatus.PLANNED:
            if record.plan_id in plans:
                raise ValueError("execution journal plan_id is duplicated")
            plans[record.plan_id] = record
            filled_shares[record.plan_id] = 0
        else:
            plan = plans.get(record.plan_id)
            if plan is None:
                raise ValueError("execution journal event references an unknown plan")
            if record.plan_id in terminal:
                raise ValueError("execution journal plan is already terminal")
            planned_at = _timestamp(plan.recorded_at, field="recorded_at")
            if recorded < planned_at:
                raise ValueError("execution journal event chronology predates its plan")
            if record.schema_version == 2 and (
                record.decision_date != plan.decision_date
                or record.symbol != plan.symbol
                or record.side != plan.side
                or record.planned_weight != plan.planned_weight
                or record.planned_price != plan.planned_price
                or record.planned_shares != plan.planned_shares
            ):
                raise ValueError("execution journal event plan identity differs from its plan")
            next_open = cast(float, record.next_open)
            prior_open = plan_opens.setdefault(record.plan_id, next_open)
            if next_open != prior_open:
                raise ValueError("execution journal next open differs within one plan")
            if record.status is JournalStatus.FILLED:
                actual_time_raw = cast(str, record.actual_time)
                actual_time = _timestamp(actual_time_raw, field="actual_time")
                if actual_time < planned_at or actual_time > recorded:
                    raise ValueError("execution journal fill chronology is invalid")
                actual_shares = cast(int, record.actual_shares)
                planned_shares = cast(int, plan.planned_shares)
                total = filled_shares[record.plan_id] + actual_shares
                if total > planned_shares:
                    raise ValueError("execution journal fills exceed planned shares")
                filled_shares[record.plan_id] = total
                if total == planned_shares:
                    terminal.add(record.plan_id)
                side = cast(str, plan.side)
                actual_price = cast(float, record.actual_price)
                direction = 1.0 if side == "BUY" else -1.0
                per_share = direction * (actual_price - next_open)
                expected = (
                    per_share,
                    per_share / next_open * 10_000.0,
                    per_share * actual_shares,
                )
                observed = (
                    record.slippage_per_share,
                    record.slippage_bps,
                    record.slippage_value,
                )
                if any(
                    value is None or not math.isclose(float(value), wanted, rel_tol=1e-12, abs_tol=1e-12)
                    for value, wanted in zip(observed, expected, strict=True)
                ):
                    raise ValueError("execution journal derived slippage is invalid")
            else:
                terminal.add(record.plan_id)


validate_lifecycle = _validate_lifecycle
