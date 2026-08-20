"""Production-shaped A/B evidence for the locked Sentinel freeze candidate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from uquant.config import SystemConfig, config_fingerprint
from uquant.engine import ProductionEngine

from .first_divergence import trace_backtest

_DIVERGENCE_FIELDS = ("risk", "targets", "orders", "fills", "equity")
_ECONOMIC_METRICS = (
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "annual_turnover",
    "acute_return",
)


def validate_locked_configs(
    *,
    baseline: SystemConfig,
    candidate: SystemConfig,
) -> dict[str, str]:
    """Require the locked candidate to change only causal confirmation authority."""

    baseline_fields = baseline.to_dict()
    candidate_fields = candidate.to_dict()
    baseline_enabled = baseline_fields.pop(
        "risk_sentinel_causal_confirmation_enabled"
    )
    candidate_enabled = candidate_fields.pop(
        "risk_sentinel_causal_confirmation_enabled"
    )
    if (
        baseline_enabled is not False
        or candidate_enabled is not True
        or baseline_fields != candidate_fields
    ):
        raise ValueError(
            "exclusive-freeze configs must differ only by causal confirmation authority"
        )
    if (
        baseline.risk_sentinel_mode != "FREEZE_ONLY"
        or baseline.risk_sentinel_min_confidence != 0.80
        or baseline.risk_sentinel_confirm_days != 2
        or baseline.risk_sentinel_repair_days != 3
        or baseline.risk_sentinel_severe_direct_enabled is not True
    ):
        raise ValueError("exclusive-freeze parameters differ from the candidate lock")
    return {
        "baseline_config_sha256": config_fingerprint(baseline),
        "candidate_config_sha256": config_fingerprint(candidate),
    }


def _orders(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = row.get("pending_orders", row.get("orders", ()))
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("exclusive-freeze trace orders must be a sequence")
    if any(not isinstance(order, Mapping) for order in raw):
        raise ValueError("exclusive-freeze trace order must be a mapping")
    return tuple(raw)


def _targets(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = row.get("targets", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("exclusive-freeze trace targets must be a sequence")
    if any(not isinstance(target, Mapping) for target in raw):
        raise ValueError("exclusive-freeze trace target must be a mapping")
    return tuple(raw)


def _risk_evidence(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("risk_evidence", {})
    if not isinstance(value, Mapping):
        raise ValueError("exclusive-freeze risk evidence must be a mapping")
    return value


def _order_identity(order: Mapping[str, Any]) -> tuple[object, ...]:
    event_id = order.get("event_id")
    if isinstance(event_id, str) and event_id:
        return (event_id,)
    return (
        order.get("side"),
        order.get("symbol"),
        order.get("target_weight"),
        order.get("reason_code"),
        order.get("exit_kind"),
    )


def _gross_cap_event_count(value: object) -> int:
    if isinstance(value, Mapping):
        if any(
            value.get(field) == "RISK_GROSS_CAP"
            for field in ("event", "event_type", "reason_code", "mechanism")
        ):
            return 1
        return sum(_gross_cap_event_count(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sum(_gross_cap_event_count(item) for item in value)
    return 0


def _first_divergence(
    baseline: Sequence[Mapping[str, Any]],
    candidate: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for left, right in zip(baseline, candidate, strict=True):
        changed: list[str] = []
        for field in _DIVERGENCE_FIELDS:
            left_value: object
            right_value: object
            if field == "risk":
                left_value = (left.get("risk"), _risk_evidence(left))
                right_value = (right.get("risk"), _risk_evidence(right))
            elif field == "orders":
                left_value = _orders(left)
                right_value = _orders(right)
            elif field == "targets":
                left_value = _targets(left)
                right_value = _targets(right)
            else:
                left_value = left.get(field)
                right_value = right.get(field)
            if left_value != right_value:
                changed.append(field)
        if changed:
            return {
                "date": str(left["date"]),
                "changed_fields": changed,
                "baseline": dict(left),
                "candidate": dict(right),
            }
    return None


def _healthy_reductions(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    blocked_symbols: set[str],
) -> int:
    baseline_weights = {
        str(target.get("symbol")): float(target.get("weight", 0.0))
        for target in _targets(baseline)
    }
    candidate_weights = {
        str(target.get("symbol")): float(target.get("weight", 0.0))
        for target in _targets(candidate)
    }
    return sum(
        1
        for symbol, weight in baseline_weights.items()
        if symbol not in blocked_symbols
        and weight > 0.0
        and candidate_weights.get(symbol, 0.0) + 1e-12 < weight
    )


def _opportunity_cost(
    *,
    row: Mapping[str, Any],
    blocked_orders: Sequence[Mapping[str, Any]],
    forward_returns: Mapping[str, Mapping[str, Mapping[str, float | None]]],
) -> list[dict[str, Any]]:
    date = str(row["date"])
    equity = float(row.get("equity", 0.0))
    result: list[dict[str, Any]] = []
    for order in blocked_orders:
        symbol = str(order.get("symbol", ""))
        value = equity * max(0.0, float(order.get("target_weight", 0.0)))
        returns = forward_returns.get(date, {}).get(symbol, {})
        values = [
            float(item)
            for item in (returns.get("5d"), returns.get("10d"), returns.get("20d"))
            if item is not None
        ]
        missed = value * max((item for item in values if item > 0.0), default=0.0)
        avoided = value * abs(min((item for item in values if item < 0.0), default=0.0))
        result.append(
            {
                "symbol": symbol,
                "blocked_order_value": value,
                "counterfactual_return_5d": returns.get("5d"),
                "counterfactual_return_10d": returns.get("10d"),
                "counterfactual_return_20d": returns.get("20d"),
                "missed_upside": missed,
                "avoided_loss": avoided,
                "net_opportunity_cost": missed - avoided,
            }
        )
    return result


def _compact_metrics(result: Mapping[str, object]) -> dict[str, object]:
    return {name: result[name] for name in _ECONOMIC_METRICS if name in result}


def _forward_returns(
    *,
    engine: ProductionEngine,
    trace: Sequence[Mapping[str, object]],
) -> dict[str, dict[str, dict[str, float | None]]]:
    requested: dict[str, set[str]] = {}
    for row in trace:
        date = str(row["date"])
        for order in _orders(row):
            if str(order.get("side")) == "BUY":
                requested.setdefault(date, set()).add(str(order.get("symbol", "")))
    result: dict[str, dict[str, dict[str, float | None]]] = {}
    for date, symbols in sorted(requested.items()):
        point = pd.Timestamp(date)
        for symbol in sorted(symbols):
            frame = engine._raw.get(symbol)
            if frame is None or "close" not in frame:
                continue
            close = pd.to_numeric(frame["close"], errors="coerce").dropna()
            if point not in close.index:
                continue
            location = close.index.get_loc(point)
            if not isinstance(location, int):
                continue
            position = location
            current = float(close.iloc[position])
            if current <= 0.0:
                continue
            returns: dict[str, float | None] = {}
            for sessions in (5, 10, 20):
                future = position + sessions
                returns[f"{sessions}d"] = (
                    float(close.iloc[future]) / current - 1.0
                    if future < len(close)
                    else None
                )
            result.setdefault(date, {})[symbol] = returns
    return result


def summarize_exclusive_freeze_comparison(
    *,
    baseline_trace: Sequence[Mapping[str, Any]],
    candidate_trace: Sequence[Mapping[str, Any]],
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    forward_returns: Mapping[
        str,
        Mapping[str, Mapping[str, float | None]],
    ] | None = None,
) -> dict[str, Any]:
    """Summarize aligned traces without treating counterfactuals as accounting PnL."""

    baseline_dates = tuple(str(row.get("date", "")) for row in baseline_trace)
    candidate_dates = tuple(str(row.get("date", "")) for row in candidate_trace)
    if not baseline_dates or baseline_dates != candidate_dates:
        raise ValueError("exclusive-freeze traces require identical non-empty calendars")

    direct_sells = 0
    gross_cap_events = 0
    healthy_reductions = 0
    caps_equal = True
    events: list[dict[str, Any]] = []
    forward = forward_returns or {}
    for baseline_row, candidate_row in zip(
        baseline_trace,
        candidate_trace,
        strict=True,
    ):
        baseline_evidence = _risk_evidence(baseline_row)
        candidate_evidence = _risk_evidence(candidate_row)
        caps_equal = caps_equal and (
            baseline_evidence.get("target_gross_cap")
            == candidate_evidence.get("target_gross_cap")
        )
        baseline_orders = _orders(baseline_row)
        candidate_orders = _orders(candidate_row)
        baseline_ids = {_order_identity(order) for order in baseline_orders}
        candidate_ids = {_order_identity(order) for order in candidate_orders}
        candidate_only = [
            order
            for order in candidate_orders
            if _order_identity(order) not in baseline_ids
        ]
        direct_sells += sum(
            str(order.get("side")) == "SELL" for order in candidate_only
        )
        gross_cap_events += sum(
            str(order.get("reason_code")) == "RISK_GROSS_CAP"
            for order in candidate_orders
        )
        gross_cap_events += _gross_cap_event_count(
            candidate_evidence.get("risk_events", ())
        )
        if not (
            bool(candidate_evidence.get("sentinel_freeze_new_risk", False))
            and not bool(candidate_evidence.get("base_freeze_new_risk", False))
        ):
            continue
        blocked = [
            dict(order)
            for order in baseline_orders
            if str(order.get("side")) == "BUY"
            and _order_identity(order) not in candidate_ids
        ]
        blocked_symbols = {str(order.get("symbol", "")) for order in blocked}
        event_reductions = _healthy_reductions(
            baseline_row,
            candidate_row,
            blocked_symbols=blocked_symbols,
        )
        healthy_reductions += event_reductions
        incremental = list(
            candidate_evidence.get("sentinel_causal_incremental_families", ())
        )
        earlier = list(
            candidate_evidence.get("sentinel_causal_earlier_families", ())
        )
        comparison_class = (
            "incremental_same_day"
            if incremental
            else "earlier_confirmed"
            if earlier
            else "not_comparable"
        )
        event_direct_sells = sum(
            str(order.get("side")) == "SELL" for order in candidate_only
        )
        events.append(
            {
                "date": str(candidate_row["date"]),
                "non_severe_direct": not bool(
                    candidate_evidence.get("sentinel_severe_direct", False)
                ),
                "coverage": str(
                    candidate_evidence.get("sentinel_causal_coverage_status", "NOT_READY")
                ),
                "confidence": float(
                    candidate_evidence.get("sentinel_causal_confidence", 0.0)
                ),
                "confirmation_history_trusted": bool(
                    candidate_evidence.get(
                        "sentinel_causal_confirmation_history_trusted",
                        False,
                    )
                ),
                "confirmation_days": int(
                    candidate_evidence.get("sentinel_causal_confirmation_days", 0)
                ),
                "active_families": list(
                    candidate_evidence.get("sentinel_causal_active_families", ())
                ),
                "incremental_families": incremental,
                "earlier_families": earlier,
                "comparison_class": comparison_class,
                "blocked_new_risk_count": len(blocked),
                "blocked_orders": blocked,
                "sentinel_direct_sell_count": event_direct_sells,
                "healthy_holding_reduction_count": event_reductions,
                "opportunity_cost": _opportunity_cost(
                    row=baseline_row,
                    blocked_orders=blocked,
                    forward_returns=forward,
                ),
            }
        )

    qualifying = sum(
        event["non_severe_direct"] is True
        and event["coverage"] == "READY"
        and float(event["confidence"]) >= 0.8
        and event["confirmation_history_trusted"] is True
        and int(event["confirmation_days"]) >= 2
        and len(event["active_families"]) >= 2
        and event["comparison_class"] in {"incremental_same_day", "earlier_confirmed"}
        and int(event["blocked_new_risk_count"]) > 0
        and int(event["sentinel_direct_sell_count"]) == 0
        and int(event["healthy_holding_reduction_count"]) == 0
        for event in events
    )
    return {
        "first_divergence": _first_divergence(baseline_trace, candidate_trace),
        "hard_gate": {
            "target_gross_cap_equal_to_base": caps_equal,
            "sentinel_direct_sell_count": direct_sells,
            "sentinel_risk_gross_cap_event_count": gross_cap_events,
            "healthy_holding_reduction_count": healthy_reductions,
        },
        "value_gate": {
            "passed": qualifying > 0,
            "qualifying_non_severe_events": qualifying,
        },
        "exclusive_freeze_events": events,
        "metrics": {
            "baseline": dict(baseline_metrics),
            "candidate": dict(candidate_metrics),
        },
        "counterfactual_is_accounting_pnl": False,
    }


def run_exclusive_freeze_comparison(
    *,
    data_dir: str | Path,
    symbols: Sequence[str],
    start: str,
    end: str,
    scenario: str,
    baseline_cfg: SystemConfig,
    candidate_cfg: SystemConfig,
) -> dict[str, Any]:
    """Replay one locked A/B cell through the sole production engine."""

    identities = validate_locked_configs(
        baseline=baseline_cfg,
        candidate=candidate_cfg,
    )
    normalized_symbols = tuple(sorted(set(symbols)))
    if not normalized_symbols:
        raise ValueError("exclusive-freeze runner requires at least one symbol")
    baseline_engine = ProductionEngine(data_dir, baseline_cfg)
    candidate_engine = ProductionEngine(data_dir, candidate_cfg)
    baseline_metrics, baseline_trace = trace_backtest(
        baseline_engine,
        symbols=normalized_symbols,
        start=start,
        end=end,
    )
    candidate_metrics, candidate_trace = trace_backtest(
        candidate_engine,
        symbols=normalized_symbols,
        start=start,
        end=end,
    )
    summary = summarize_exclusive_freeze_comparison(
        baseline_trace=baseline_trace,
        candidate_trace=candidate_trace,
        baseline_metrics=_compact_metrics(baseline_metrics),
        candidate_metrics=_compact_metrics(candidate_metrics),
        forward_returns=_forward_returns(
            engine=baseline_engine,
            trace=baseline_trace,
        ),
    )
    return {
        "schema": "uquant.sentinel-exclusive-freeze-comparison.v1",
        "scenario": scenario,
        "symbols": list(normalized_symbols),
        "start": start,
        "end": end,
        **identities,
        **summary,
    }
