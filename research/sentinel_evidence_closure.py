"""Point-in-time comparison of base and Sentinel market evidence."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pandas as pd

from uquant.atomic_io import atomic_write_text, validate_atomic_output_boundary
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import INDEX_SYMBOLS, ProductionEngine, code_fingerprint
from uquant.risk_sentinel.models import (
    BaseMarketRiskRow,
    RiskEvidenceTimeline,
    SentinelMarketRow,
    WarmupStatus,
)
from uquant.validation.universe import default_ai_universe

_TRUSTED_MARKET_FAMILIES: Final = frozenset(
    {"breadth_structure", "covariance_stress", "market_velocity"}
)
_HORIZONS: Final = (5, 10, 20)


def _first_dates(
    values: tuple[tuple[str, str], ...],
    *,
    label: str,
) -> dict[str, str]:
    result = dict(values)
    if len(result) != len(values) or not set(result).issubset(
        _TRUSTED_MARKET_FAMILIES
    ):
        raise ValueError(f"{label} must contain unique trusted market families")
    return result


def _aligned_rows(timeline: RiskEvidenceTimeline) -> None:
    sentinel_dates = tuple(row.date for row in timeline.sentinel_rows)
    base_dates = tuple(row.date for row in timeline.base_rows)
    if (
        not timeline.sessions
        or timeline.sessions != sentinel_dates
        or timeline.sessions != base_dates
    ):
        raise ValueError("base and Sentinel evidence history must be aligned")
    if tuple(sorted(set(timeline.sessions))) != timeline.sessions:
        raise ValueError("evidence history sessions must be uniquely ordered")
    if timeline.as_of != timeline.sessions[-1]:
        raise ValueError("evidence history as-of must equal its final session")


def _row_first_dates(
    rows: tuple[SentinelMarketRow, ...] | tuple[BaseMarketRiskRow, ...],
) -> dict[str, str]:
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
            if family not in _TRUSTED_MARKET_FAMILIES:
                raise ValueError("evidence rows must contain trusted market families")
            result.setdefault(family, row.date)
    return result


def _verified_first_dates(
    timeline: RiskEvidenceTimeline,
) -> tuple[dict[str, str], dict[str, str]]:
    sentinel_supplied = _first_dates(
        timeline.sentinel_first_family_dates,
        label="Sentinel first-family dates",
    )
    base_supplied = _first_dates(
        timeline.base_first_family_dates,
        label="base first-family dates",
    )
    sentinel_derived = _row_first_dates(timeline.sentinel_rows)
    base_derived = _row_first_dates(timeline.base_rows)
    if sentinel_supplied != sentinel_derived:
        raise ValueError("Sentinel first-family dates differ from rows")
    if base_supplied != base_derived:
        raise ValueError("base first-family dates differ from rows")
    return sentinel_derived, base_derived


def _forward_value(
    forward_returns: Mapping[str, Mapping[str, float | None]],
    *,
    trigger_date: str,
    horizon: int,
) -> float | None:
    value = forward_returns.get(trigger_date, {}).get(f"{horizon}d")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("forward returns must be finite numbers or null")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("forward returns must be finite numbers or null")
    return normalized


def analyze_evidence_closure(
    timeline: RiskEvidenceTimeline,
    *,
    forward_returns: Mapping[str, Mapping[str, float | None]],
) -> dict[str, Any]:
    """Classify first comparable market-family evidence without authority."""

    _aligned_rows(timeline)
    sentinel_first, base_first = _verified_first_dates(timeline)
    sessions = set(timeline.sessions)
    if any(date not in sessions for date in (*sentinel_first.values(), *base_first.values())):
        raise ValueError("first-family dates must belong to the aligned market history")

    events: list[dict[str, Any]] = []
    for family, trigger_date in sorted(
        sentinel_first.items(),
        key=lambda item: (item[1], item[0]),
    ):
        base_date = base_first.get(family)
        if base_date is None:
            relationship = "INCREMENTAL"
        elif trigger_date < base_date:
            relationship = "EARLIER"
        else:
            relationship = "DUPLICATE"
        returns = {
            horizon: _forward_value(
                forward_returns,
                trigger_date=trigger_date,
                horizon=horizon,
            )
            for horizon in _HORIZONS
        }
        return_20d = returns[20]
        if relationship == "DUPLICATE":
            outcome = "DUPLICATE"
        elif return_20d is None:
            outcome = "DATA_NOT_READY"
        elif return_20d > 0.0:
            outcome = "FALSE_POSITIVE"
        else:
            outcome = "DOWNSIDE_CONFIRMED"
        classification = (
            "FALSE_POSITIVE" if outcome == "FALSE_POSITIVE" else relationship
        )
        events.append(
            {
                "family": family,
                "trigger_date": trigger_date,
                "sentinel_first_date": trigger_date,
                "base_first_date": base_date,
                "relationship": relationship,
                "classification": classification,
                "outcome_status": outcome,
                "forward_tech_return_5d": returns[5],
                "forward_tech_return_10d": returns[10],
                "forward_tech_return_20d": return_20d,
                "diagnostic_opportunity_cost_return": (
                    None if return_20d is None else max(0.0, return_20d)
                ),
                "production_opportunity_cost": 0.0,
            }
        )

    relationships = [str(event["relationship"]) for event in events]
    return {
        "schema": "uquant.sentinel-evidence-closure.v1",
        "as_of": timeline.as_of,
        "source": "RiskEvidenceTimeline",
        "trusted_market_families": sorted(_TRUSTED_MARKET_FAMILIES),
        "account_history_used": False,
        "production_causal_confirmation_enabled": False,
        "counterfactual_is_accounting_pnl": False,
        "events": events,
        "summary": {
            "duplicate": relationships.count("DUPLICATE"),
            "earlier": relationships.count("EARLIER"),
            "false_positive": sum(
                event["classification"] == "FALSE_POSITIVE" for event in events
            ),
            "incremental": relationships.count("INCREMENTAL"),
            "total_first_family_events": len(events),
        },
    }


def _tech_forward_returns(
    close: pd.Series,
    *,
    trigger_dates: tuple[str, ...],
) -> dict[str, dict[str, float | None]]:
    values = pd.to_numeric(close, errors="coerce").dropna().astype(float)
    result: dict[str, dict[str, float | None]] = {}
    for trigger_date in trigger_dates:
        point = pd.Timestamp(trigger_date)
        positions = values.index.get_indexer(pd.DatetimeIndex([point]))
        if len(positions) != 1 or int(positions[0]) < 0:
            raise ValueError("Sentinel trigger date is missing from the tech index")
        position = int(positions[0])
        current = float(values.iloc[position])
        if current <= 0.0:
            raise ValueError("tech-index close must be positive")
        result[trigger_date] = {
            f"{horizon}d": (
                float(values.iloc[position + horizon]) / current - 1.0
                if position + horizon < len(values)
                else None
            )
            for horizon in _HORIZONS
        }
    return result


def run_evidence_closure(
    *,
    data_dir: str | Path,
    as_of: str,
    output: str | Path,
) -> dict[str, Any]:
    """Build and atomically persist account-free closure evidence."""

    protected = validate_atomic_output_boundary(
        output,
        protected_roots=(data_dir,),
    )
    if DEFAULT_CONFIG.risk_sentinel_causal_confirmation_enabled:
        raise RuntimeError("evidence closure requires causal authority disabled")
    universe = default_ai_universe()
    symbols = tuple(sorted({*universe.symbols, *INDEX_SYMBOLS}))
    engine = ProductionEngine(data_dir, DEFAULT_CONFIG)
    engine._load(symbols)
    timeline = engine._causal_risk_timeline(
        as_of=as_of,
        cfg=DEFAULT_CONFIG,
        universe=universe,
    )
    _aligned_rows(timeline)
    sentinel_first, _ = _verified_first_dates(timeline)
    trigger_dates = tuple(
        sorted(set(sentinel_first.values()))
    )
    forward_returns = _tech_forward_returns(
        engine._raw["sh000682"]["close"],
        trigger_dates=trigger_dates,
    )
    result = analyze_evidence_closure(
        timeline,
        forward_returns=forward_returns,
    )
    result["provenance"] = {
        "code_sha256": code_fingerprint(),
        "config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "data_sha256": engine.data.manifest(
            symbols,
            as_of=pd.Timestamp(as_of),
        ).digest,
        "universe_sha256": universe.sha256,
        "universe_size": len(universe.symbols),
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    atomic_write_text(output, encoded, protected_paths=protected)
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the production-shaped account-free evidence closure."""

    parser = argparse.ArgumentParser(
        description="Compare point-in-time base and Sentinel market evidence."
    )
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = run_evidence_closure(
        data_dir=args.data_dir,
        as_of=args.as_of,
        output=args.output,
    )
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
