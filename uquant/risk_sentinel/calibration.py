"""Offline-only risk event outcome calibration."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Final, cast

import pandas as pd

DEFAULT_CONTRACT_PATH: Final = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "risk_sentinel_calibration_contract.json"
)
_FIELDS: Final = frozenset(
    {
        "schema_version",
        "contract_id",
        "horizons",
        "prediction_levels",
        "shock_definition",
        "lead_window_sessions",
        "bull_definition",
        "canonical_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class CalibrationContract:
    """Pre-registered definitions used by offline calibration only."""

    horizons: tuple[int, ...]
    prediction_levels: tuple[str, ...]
    shock_horizon: int
    shock_drawdown_lte: float
    lead_window_sessions: int
    bull_horizon: int
    bull_return_gte: float
    sha256: str


def _reject_duplicate_calibration_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"calibration contract contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"calibration contract contains non-standard number: {value}")


def _calibration_sha256(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "canonical_sha256"}
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_calibration_contract(
    path: str | Path = DEFAULT_CONTRACT_PATH,
) -> CalibrationContract:
    """Load the strict pre-outcome calibration definition."""

    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("calibration contract must be a regular file")
    try:
        raw = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("calibration contract is corrupt") from exc
    if not isinstance(raw, dict) or set(raw) != _FIELDS:
        raise ValueError("calibration contract schema is malformed")
    shock = raw.get("shock_definition")
    bull = raw.get("bull_definition")
    if (
        raw.get("schema_version") != 1
        or raw.get("contract_id") != "risk-sentinel-event-calibration-v1"
        or raw.get("horizons") != [1, 3, 5, 10, 20]
        or raw.get("prediction_levels") != ["CAUTION", "DEFENSIVE", "CRITICAL"]
        or shock != {"horizon": 20, "max_drawdown_lte": -0.08}
        or raw.get("lead_window_sessions") != 5
        or bull != {"horizon": 20, "forward_return_gte": 0.05}
        or raw.get("canonical_sha256") != _canonical_sha256(raw)
    ):
        raise ValueError("calibration contract identity differs from preregistration")
    return CalibrationContract(
        horizons=tuple(raw["horizons"]),
        prediction_levels=tuple(raw["prediction_levels"]),
        shock_horizon=cast(int, shock["horizon"]),
        shock_drawdown_lte=cast(float, shock["max_drawdown_lte"]),
        lead_window_sessions=raw["lead_window_sessions"],
        bull_horizon=cast(int, bull["horizon"]),
        bull_return_gte=cast(float, bull["forward_return_gte"]),
        sha256=raw["canonical_sha256"],
    )


def _validated_market(
    market_frame: pd.DataFrame,
    evaluation_end: str,
) -> pd.Series:
    if not isinstance(market_frame.index, pd.DatetimeIndex) or "close" not in market_frame:
        raise ValueError("calibration market requires DatetimeIndex and close")
    end = pd.Timestamp(evaluation_end).normalize()
    visible = pd.to_numeric(
        market_frame.loc[:end, "close"],
        errors="coerce",
    ).dropna()
    if (
        visible.empty
        or not visible.index.is_monotonic_increasing
        or visible.index.has_duplicates
        or (visible <= 0.0).any()
    ):
        raise ValueError("calibration market prefix is malformed")
    return visible.astype(float)


def _optional_unit(value: object, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{label} must be finite in [0, 1]")
    return float(value)


def calibrate_events(
    *,
    assessments: Sequence[Mapping[str, object]],
    market_frame: pd.DataFrame,
    evaluation_end: str,
    contract: CalibrationContract | None = None,
) -> tuple[dict[str, object], ...]:
    """Compute post-event outcomes using only the declared offline prefix."""

    reviewed = load_calibration_contract() if contract is None else contract
    close = _validated_market(market_frame, evaluation_end)
    positions = {str(value.date()): index for index, value in enumerate(close.index)}
    results: list[dict[str, object]] = []
    for assessment in assessments:
        event_date = assessment.get("date")
        level = assessment.get("level")
        first_evidence = assessment.get("first_evidence_date")
        if (
            not isinstance(event_date, str)
            or event_date not in positions
            or level not in reviewed.prediction_levels
        ):
            raise ValueError("calibration assessment identity is malformed")
        confidence = _optional_unit(
            assessment.get("confidence"),
            label="calibration confidence",
        )
        event_position = positions[event_date]
        if first_evidence is None:
            lead_time: int | None = None
        elif not isinstance(first_evidence, str) or first_evidence not in positions:
            raise ValueError("calibration first evidence date is outside the prefix")
        else:
            lead_time = event_position - positions[first_evidence]
            if lead_time < 0:
                raise ValueError("calibration first evidence occurs after the event")
        base = float(close.iloc[event_position])
        result: dict[str, object] = {
            "event_date": event_date,
            "level": level,
            "confidence": confidence,
            "first_evidence_date": first_evidence,
            "lead_time": lead_time,
        }
        forward_twenty: pd.Series | None = None
        for horizon in reviewed.horizons:
            endpoint = event_position + horizon
            if endpoint >= len(close):
                result[f"return_{horizon}d"] = None
                result[f"drawdown_{horizon}d"] = None
                continue
            forward = close.iloc[event_position + 1 : endpoint + 1]
            result[f"return_{horizon}d"] = float(close.iloc[endpoint] / base - 1.0)
            result[f"drawdown_{horizon}d"] = float((forward / base - 1.0).min())
            if horizon == reviewed.shock_horizon:
                forward_twenty = forward
        drawdown = result[f"drawdown_{reviewed.shock_horizon}d"]
        realized = None if drawdown is None else bool(cast(float, drawdown) <= reviewed.shock_drawdown_lte)
        result["realized_shock"] = realized
        result["false_positive"] = None if realized is None else not realized
        result["opportunity_cost"] = (
            None
            if realized is None or realized or forward_twenty is None
            else max(0.0, float((forward_twenty / base - 1.0).max()))
        )
        results.append(result)
    return tuple(results)


def _derive_calibration_outcome_metrics(
    *,
    bull_dates: Any,
    complete: Any,
    detected: Any,
    events: Any,
    shock_dates: Any,
    shock_depths: Any,
) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    recall = detected / len(shock_dates) if shock_dates else None
    lead_times = [
        float(cast(int | float, item["lead_time"]))
        for item in complete
        if item.get("realized_shock") is True and isinstance(item.get("lead_time"), (int, float))
    ]
    false_costs = [
        float(cast(int | float, item["opportunity_cost"]))
        for item in complete
        if item.get("realized_shock") is False and isinstance(item.get("opportunity_cost"), (int, float))
    ]
    predicted_dates = {str(item["event_date"]) for item in events if isinstance(item.get("event_date"), str)}
    silent_bulls = sum(date not in predicted_dates for date in bull_dates)
    missed = len(shock_dates) - detected
    depths = shock_depths or {}
    missed_depth_values = [
        abs(float(depths[date])) for date in shock_dates if date not in predicted_dates and date in depths
    ]
    caution_costs = [
        float(cast(int | float, item["opportunity_cost"]))
        for item in complete
        if item.get("level") == "CAUTION"
        and item.get("realized_shock") is False
        and isinstance(item.get("opportunity_cost"), (int, float))
    ]
    return caution_costs, false_costs, lead_times, missed, missed_depth_values, recall, silent_bulls


def _match_predictions_to_outcomes(
    *,
    bull_dates: Any,
    contract: Any,
    events: Any,
    sessions: Any,
    shock_dates: Any,
) -> tuple[Any, Any, Any]:
    reviewed = load_calibration_contract() if contract is None else contract
    if len(sessions) != len(set(sessions)) or tuple(sorted(sessions)) != sessions:
        raise ValueError("calibration sessions must be unique and ordered")
    positions = {session: index for index, session in enumerate(sessions)}
    if any(value not in positions for value in (*shock_dates, *bull_dates)):
        raise ValueError("calibration outcome date is outside sessions")
    complete = [item for item in events if isinstance(item.get("realized_shock"), bool)]
    true_positive = sum(item["realized_shock"] is True for item in complete)
    precision = true_positive / len(complete) if complete else None

    available = list(range(len(events)))
    detected = 0
    for shock in shock_dates:
        candidates = [
            index
            for index in available
            if isinstance(events[index].get("event_date"), str)
            and events[index]["event_date"] in positions
            and 0
            <= positions[shock] - positions[str(events[index]["event_date"])]
            <= reviewed.lead_window_sessions
        ]
        if candidates:
            chosen = max(
                candidates,
                key=lambda index: positions[str(events[index]["event_date"])],
            )
            available.remove(chosen)
            detected += 1
    return complete, detected, precision


def summarize_calibration(
    *,
    events: Sequence[Mapping[str, object]],
    shock_dates: tuple[str, ...],
    bull_dates: tuple[str, ...],
    sessions: tuple[str, ...],
    shock_depths: Mapping[str, float] | None = None,
    contract: CalibrationContract | None = None,
) -> dict[str, object]:
    """Summarize pre-registered event detection and opportunity costs."""

    complete, detected, precision = _match_predictions_to_outcomes(
        bull_dates=bull_dates,
        contract=contract,
        events=events,
        sessions=sessions,
        shock_dates=shock_dates,
    )
    caution_costs, false_costs, lead_times, missed, missed_depth_values, recall, silent_bulls = (
        _derive_calibration_outcome_metrics(
            bull_dates=bull_dates,
            complete=complete,
            detected=detected,
            events=events,
            shock_dates=shock_dates,
            shock_depths=shock_depths,
        )
    )
    return {
        "precision": precision,
        "recall": recall,
        "median_lead_time": median(lead_times) if lead_times else None,
        "false_positive_opportunity_cost": (
            float(sum(false_costs) / len(false_costs)) if false_costs else 0.0
        ),
        "caution_freeze_opportunity_cost": (
            float(sum(caution_costs) / len(caution_costs)) if caution_costs else 0.0
        ),
        "bull_silence_rate": silent_bulls / len(bull_dates) if bull_dates else None,
        "missed_shock_count": missed,
        "missed_shock_depth": (max(missed_depth_values) if missed_depth_values else None),
    }


_canonical_sha256 = _calibration_sha256
_reject_duplicate_keys = _reject_duplicate_calibration_keys
