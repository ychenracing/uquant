"""Paired, research-only attribution for the locked Sentinel gross-cap candidate."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from uquant.atomic_io import atomic_write_text
from uquant.config import DEFAULT_CONFIG
from uquant.data import DataStore
from uquant.engine import ProductionEngine
from uquant.features import scalar


def _curve(result: Mapping[str, Any]) -> tuple[tuple[str, float], ...]:
    raw = result.get("equity_curve")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Sentinel cap result requires a non-empty equity curve")
    rows: list[tuple[str, float]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError("Sentinel cap equity point is malformed")
        date = str(item.get("date", ""))
        value = float(item.get("equity", math.nan))
        if not date or not math.isfinite(value) or value <= 0.0:
            raise ValueError("Sentinel cap equity point is malformed")
        rows.append((date, value))
    if tuple(date for date, _ in rows) != tuple(sorted({date for date, _ in rows})):
        raise ValueError("Sentinel cap equity curve must be unique and ordered")
    return tuple(rows)


def _ledger(result: Mapping[str, Any], dates: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    attribution = result.get("attribution")
    if not isinstance(attribution, Mapping):
        raise ValueError("Sentinel cap result requires economic attribution")
    rows = attribution.get("daily_ledger")
    if not isinstance(rows, list):
        raise ValueError("Sentinel cap result requires a daily ledger")
    indexed = {
        str(row.get("date")): row
        for row in rows
        if isinstance(row, Mapping)
    }
    if tuple(indexed) != dates:
        raise ValueError("Sentinel cap daily ledger must align with equity")
    return indexed


def _drawdown(values: Sequence[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def _interval_return(result: Mapping[str, Any], *, start: str, end: str) -> float:
    curve = dict(_curve(result))
    if start not in curve or end not in curve:
        raise ValueError("Sentinel cap acute interval must be present in the curve")
    return curve[end] / curve[start] - 1.0


def _records(result: Mapping[str, Any], field: str) -> tuple[Mapping[str, Any], ...]:
    value: object = result.get(field)
    if field == "fills":
        account = result.get("final_account")
        value = account.get("fills") if isinstance(account, Mapping) else None
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ValueError(f"Sentinel cap result {field} is malformed")
    return tuple(item for item in value if isinstance(item, Mapping))


def _binding_segments(
    result: Mapping[str, Any],
    dates: tuple[str, ...],
) -> tuple[tuple[int, int], ...]:
    events = result.get("sentinel_events")
    if not isinstance(events, list):
        raise ValueError("Sentinel cap result sentinel events are malformed")
    binding = {
        str(item.get("date"))
        for item in events
        if isinstance(item, Mapping) and item.get("sentinel_cap_binding") is True
    }
    positions = [index for index, date in enumerate(dates) if date in binding]
    segments: list[tuple[int, int]] = []
    for position in positions:
        if not segments or position != segments[-1][1] + 1:
            segments.append((position, position))
        else:
            segments[-1] = (segments[-1][0], position)
    return tuple(segments)


def _behavior_projection(trace: Mapping[str, Any]) -> dict[str, Any]:
    risk = trace.get("risk")
    if not isinstance(risk, Mapping):
        raise ValueError("Sentinel cap decision trace risk is malformed")
    return {
        "target_gross_cap": risk.get("target_gross_cap"),
        "target_gross": trace.get("target_gross"),
        "targets": trace.get("targets"),
        "orders": trace.get("orders"),
    }


def _first_behavior_divergence(
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    base_trace = _records(base, "decision_trace")
    candidate_trace = _records(candidate, "decision_trace")
    if len(base_trace) != len(candidate_trace):
        raise ValueError("Sentinel cap paired decision traces must align exactly")
    for left, right in zip(base_trace, candidate_trace, strict=True):
        left_date = str(left.get("date", ""))
        if not left_date or left_date != str(right.get("date", "")):
            raise ValueError("Sentinel cap paired decision traces must align exactly")
        left_behavior = _behavior_projection(left)
        right_behavior = _behavior_projection(right)
        changed = tuple(
            field for field in left_behavior if left_behavior[field] != right_behavior[field]
        )
        if changed:
            return {
                "date": left_date,
                "changed_fields": list(changed),
                "base": {field: left_behavior[field] for field in changed},
                "candidate": {field: right_behavior[field] for field in changed},
            }
    return None


def compare_sentinel_cap_results(
    *,
    base: Mapping[str, Any],
    candidate: Mapping[str, Any],
    benchmark_close: Mapping[str, float],
    recovery_sessions: int = 20,
) -> dict[str, Any]:
    """Attribute one candidate only against its exact Freeze-only counterfactual."""

    if recovery_sessions < 1:
        raise ValueError("Sentinel cap recovery_sessions must be positive")
    base_curve = _curve(base)
    candidate_curve = _curve(candidate)
    if tuple(date for date, _ in base_curve) != tuple(date for date, _ in candidate_curve):
        raise ValueError("Sentinel cap paired curves must align exactly")
    dates = tuple(date for date, _ in base_curve)
    if set(benchmark_close) != set(dates):
        raise ValueError("Sentinel cap benchmark must align exactly")
    benchmark = {date: float(benchmark_close[date]) for date in dates}
    if any(not math.isfinite(value) or value <= 0.0 for value in benchmark.values()):
        raise ValueError("Sentinel cap benchmark closes must be positive and finite")
    base_values = [value for _, value in base_curve]
    candidate_values = [value for _, value in candidate_curve]
    base_ledger = _ledger(base, dates)
    candidate_ledger = _ledger(candidate, dates)
    date_position = {date: index for index, date in enumerate(dates)}

    incremental_cash_drag = 0.0
    for prior, current in pairwise(dates):
        extra_cash = float(candidate_ledger[prior].get("cash", 0.0)) - float(
            base_ledger[prior].get("cash", 0.0)
        )
        incremental_cash_drag -= extra_cash * (
            benchmark[current] / benchmark[prior] - 1.0
        )

    base_orders = _records(base, "order_ledger")
    candidate_orders = _records(candidate, "order_ledger")
    base_fills = _records(base, "fills")
    candidate_fills = _records(candidate, "fills")
    sentinel_orders = tuple(
        item for item in candidate_orders if item.get("reason_code") == "sentinel_gross_cap"
    )
    sentinel_turnover = sum(
        float(item.get("gross_value", 0.0))
        for item in candidate_fills
        if item.get("reason_code") == "sentinel_gross_cap"
    )
    initial_equity = base_values[0]
    additional_turnover = (
        sum(float(item.get("gross_value", 0.0)) for item in candidate_fills)
        - sum(float(item.get("gross_value", 0.0)) for item in base_fills)
    ) / initial_equity

    events: list[dict[str, Any]] = []
    binding_segments = _binding_segments(candidate, dates)
    for segment_index, (start, end) in enumerate(binding_segments):
        release = end + 1 if end + 1 < len(dates) else None
        horizon = (
            release + recovery_sessions
            if release is not None and release + recovery_sessions < len(dates)
            else None
        )
        recovery_status = "OBSERVED" if horizon is not None else "RIGHT_CENSORED"
        curve_end = horizon if horizon is not None else len(dates) - 1
        next_start = (
            binding_segments[segment_index + 1][0]
            if segment_index + 1 < len(binding_segments)
            else len(dates)
        )
        attribution_end = next_start - 1
        recovery_cost = None
        if release is not None and horizon is not None:
            base_recovery = base_values[horizon] / base_values[release] - 1.0
            candidate_recovery = candidate_values[horizon] / candidate_values[release] - 1.0
            recovery_cost = max(
                0.0,
                (base_recovery - candidate_recovery) * candidate_values[release],
            )
        candidate_event_orders = sum(
            start <= date_position[str(item.get("signal_date"))] <= attribution_end
            for item in candidate_orders
            if str(item.get("signal_date")) in dates
        )
        base_event_orders = sum(
            start <= date_position[str(item.get("signal_date"))] <= attribution_end
            for item in base_orders
            if str(item.get("signal_date")) in dates
        )
        direct_event_orders = sum(
            start <= date_position[str(item.get("signal_date"))] <= attribution_end
            for item in sentinel_orders
            if str(item.get("signal_date")) in dates
        )
        event_cash_drag = 0.0
        cash_end = min(end + 1, len(dates) - 1)
        for prior_index in range(start, cash_end):
            prior = dates[prior_index]
            current = dates[prior_index + 1]
            extra_cash = float(candidate_ledger[prior].get("cash", 0.0)) - float(
                base_ledger[prior].get("cash", 0.0)
            )
            event_cash_drag -= extra_cash * (
                benchmark[current] / benchmark[prior] - 1.0
            )

        candidate_event_turnover = sum(
            float(item.get("gross_value", 0.0))
            for item in candidate_fills
            if str(item.get("fill_date")) in date_position
            and start <= date_position[str(item.get("fill_date"))] <= attribution_end
        )
        base_event_turnover = sum(
            float(item.get("gross_value", 0.0))
            for item in base_fills
            if str(item.get("fill_date")) in date_position
            and start <= date_position[str(item.get("fill_date"))] <= attribution_end
        )

        events.append(
            {
                "start": dates[start],
                "end": dates[end],
                "release": dates[release] if release is not None else None,
                "recovery_horizon": dates[horizon] if horizon is not None else None,
                "recovery_status": recovery_status,
                "attribution_end": dates[attribution_end],
                "avoided_drawdown": _drawdown(base_values[start : end + 1])
                - _drawdown(candidate_values[start : end + 1]),
                "cash_drag": event_cash_drag,
                "post_release_recovery_cost": recovery_cost,
                "additional_orders": candidate_event_orders - base_event_orders,
                "sentinel_orders": direct_event_orders,
                "additional_turnover": (
                    candidate_event_turnover - base_event_turnover
                )
                / initial_equity,
                "with_sentinel_equity": [
                    {"date": dates[index], "equity": candidate_values[index]}
                    for index in range(start, curve_end + 1)
                ],
                "base_counterfactual_equity": [
                    {"date": dates[index], "equity": base_values[index]}
                    for index in range(start, curve_end + 1)
                ],
            }
        )

    first_divergence = _first_behavior_divergence(base, candidate)
    return {
        "base_counterfactual": {
            "final_equity": base_values[-1],
            "final_wealth": float(base["final_wealth"]),
            "max_drawdown": float(base["max_drawdown"]),
            "equity_curve": [dict(date=date, equity=value) for date, value in base_curve],
        },
        "candidate": {
            "final_equity": candidate_values[-1],
            "final_wealth": float(candidate["final_wealth"]),
            "max_drawdown": float(candidate["max_drawdown"]),
            "equity_curve": [
                {"date": date, "equity": value} for date, value in candidate_curve
            ],
        },
        "first_behavior_divergence": first_divergence,
        "avoided_drawdown": float(base["max_drawdown"]) - float(candidate["max_drawdown"]),
        "cash_drag": {
            "value": incremental_cash_drag,
            "definition": "incremental candidate cash times next-session benchmark return",
            "is_accounting_pnl": False,
        },
        "post_release_recovery_cost": {
            "observed_total": sum(
                float(item["post_release_recovery_cost"])
                for item in events
                if item["post_release_recovery_cost"] is not None
            ),
            "observed_events": sum(
                item["recovery_status"] == "OBSERVED" for item in events
            ),
            "right_censored_events": sum(
                item["recovery_status"] == "RIGHT_CENSORED" for item in events
            ),
        },
        "order_attribution": {
            "additional_orders": len(candidate_orders) - len(base_orders),
            "sentinel_orders": len(sentinel_orders),
        },
        "turnover_attribution": {
            "additional_turnover": additional_turnover,
            "sentinel_gross_value": sentinel_turnover,
        },
        "events": events,
    }


def run_locked_candidate(
    *,
    data_dir: str | Path,
    symbols: Sequence[str],
    start: str,
    end: str,
    acute_start: str,
    acute_end: str,
) -> dict[str, Any]:
    """Replay the pre-registered 70/50 candidate and exact Freeze-only base."""

    base = ProductionEngine(
        data_dir,
        DEFAULT_CONFIG.override(risk_sentinel_mode="FREEZE_ONLY"),
    ).backtest(symbols=symbols, start=start, end=end)
    candidate = ProductionEngine(
        data_dir,
        DEFAULT_CONFIG.override(risk_sentinel_mode="LIMITED_GROSS_CAP"),
    ).backtest(symbols=symbols, start=start, end=end)
    sessions = tuple(str(item["date"]) for item in candidate["equity_curve"])
    tech = DataStore(data_dir).load("sh000682")
    benchmark = {session: scalar(tech.loc[session], "close") for session in sessions}
    comparison = compare_sentinel_cap_results(
        base=base,
        candidate=candidate,
        benchmark_close=benchmark,
    )
    comparison["locked_candidate"] = {
        "NORMAL": None,
        "CAUTION": None,
        "DEFENSIVE": 0.70,
        "CRITICAL": 0.50,
        "NOT_READY": None,
    }
    comparison["metrics"] = {
        "base": {
            name: base[name]
            for name in (
                "final_wealth",
                "max_drawdown",
                "account_orders",
                "gross_turnover",
                "annual_turnover",
            )
        },
        "candidate": {
            name: candidate[name]
            for name in (
                "final_wealth",
                "max_drawdown",
                "account_orders",
                "gross_turnover",
                "annual_turnover",
            )
        },
    }
    comparison["metrics"]["base"]["acute_return"] = _interval_return(
        base,
        start=acute_start,
        end=acute_end,
    )
    comparison["metrics"]["candidate"]["acute_return"] = _interval_return(
        candidate,
        start=acute_start,
        end=acute_end,
    )
    base_metrics = comparison["metrics"]["base"]
    candidate_metrics = comparison["metrics"]["candidate"]
    comparison["gate_diagnostics"] = {
        "wealth_retention": (
            float(candidate_metrics["final_wealth"])
            / float(base_metrics["final_wealth"])
        ),
        "acute_return_delta": (
            float(candidate_metrics["acute_return"])
            - float(base_metrics["acute_return"])
        ),
        "account_order_delta": (
            int(candidate_metrics["account_orders"])
            - int(base_metrics["account_orders"])
        ),
        "gross_turnover_delta": (
            float(candidate_metrics["gross_turnover"])
            - float(base_metrics["gross_turnover"])
        ),
        "annual_turnover_delta": (
            float(candidate_metrics["annual_turnover"])
            - float(base_metrics["annual_turnover"])
        ),
        "max_drawdown_improvement_percentage_points": 100.0
        * (
            float(base_metrics["max_drawdown"])
            - float(candidate_metrics["max_drawdown"])
        ),
    }
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/frozen")
    parser.add_argument("--pool", default="a", choices=tuple("abcde"))
    parser.add_argument("--window", default="h1_2024")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    contract = json.loads(Path("benchmarks/promotion_baseline.json").read_text())
    window = contract["contract"]["windows"][args.window]
    acute = contract["contract"]["acute_windows"][args.window]
    result = run_locked_candidate(
        data_dir=args.data_dir,
        symbols=contract["pools"][args.pool],
        start=window["start"],
        end=window["end"],
        acute_start=acute["start"],
        acute_end=acute["end"],
    )
    atomic_write_text(
        args.output,
        json.dumps(result, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the research CLI
    raise SystemExit(main())
