"""Deterministic universe stress matrix executed through the production engine."""

from __future__ import annotations

import hashlib
import json
import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..engine import ProductionEngine, code_fingerprint
from ..leader import INDUSTRY, REFERENCE_UNIVERSE
from .provenance import (
    assert_replay_signature_unchanged,
    bounded_data_fingerprint,
    config_fingerprint,
    validation_fingerprint,
)

PRIMARY = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
ORDERED_UNIVERSE = PRIMARY + tuple(symbol for symbol in REFERENCE_UNIVERSE if symbol not in PRIMARY)
SEEDS = (20260807, 20260817, 20260827)
RANDOM_SIZES = (3, 5, 9, 15, 22, 32)
RANDOM_PER_SIZE_SEED = 50
STRESS_START = "2025-04-01"
STRESS_END = "2026-07-20"
ROOT = Path(__file__).resolve().parents[2]
TRADE_COMMON_STRESS_PATH = ROOT / "benchmarks" / "trade_common_stress.json"
TRADE_COMMON_ADAPTER_PATH = ROOT / "scripts" / "run_trade_common_stress.py"
LEGACY_COMMON_ADAPTER_PATH = ROOT / "scripts" / "run_legacy_common_adapter.py"
TRADE_SOURCE_ROOT = ROOT.parent / "frozen_benchmarks" / "trade"


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    scenario_type: str
    symbols: tuple[str, ...]


_WORKER_ENGINE: ProductionEngine | None = None


def _init_worker(data_dir: str) -> None:
    global _WORKER_ENGINE
    _WORKER_ENGINE = ProductionEngine(data_dir)


def _run_scenario(scenario: Scenario) -> dict[str, Any]:
    if _WORKER_ENGINE is None:
        raise RuntimeError("stress worker was not initialized")
    result = _WORKER_ENGINE.backtest(
        symbols=scenario.symbols,
        start=STRESS_START,
        end=STRESS_END,
    )
    return {
        **asdict(scenario),
        "symbol_count": len(scenario.symbols),
        "final_wealth": result["final_wealth"],
        "total_return": result["total_return"],
        "max_drawdown": result["max_drawdown"],
        "account_orders": result["account_orders"],
        "sharpe": result["sharpe"],
        "calmar": result["calmar"],
        "worst_20d": result["worst_20d"],
        "worst_60d": result["worst_60d"],
    }


def _historical_structure_scenarios(data_dir: Path) -> list[Scenario]:
    """Select loser-heavy and low-correlation pools using only pre-window data."""
    closes: dict[str, pd.Series] = {}
    scores: dict[str, float] = {}
    cutoff = pd.Timestamp(STRESS_START) - pd.Timedelta(days=1)
    for symbol in ORDERED_UNIVERSE:
        frame = pd.read_csv(data_dir / f"{symbol}.csv", parse_dates=["date"])
        bounded = frame.loc[frame["date"] <= cutoff].set_index("date")["close"].tail(121)
        if len(bounded) >= 61:
            closes[symbol] = bounded
            scores[symbol] = float(bounded.iloc[-1] / bounded.iloc[0] - 1.0)
    losers = tuple(sorted(scores, key=lambda symbol: (scores[symbol], symbol))[:15])
    returns = pd.DataFrame(
        {symbol: series.pct_change(fill_method=None) for symbol, series in closes.items()}
    ).tail(120)
    correlation = returns.corr().abs()
    first = min(
        correlation,
        key=lambda symbol: (float(correlation[symbol].drop(symbol).median()), symbol),
    )
    selected = [first]
    while len(selected) < min(9, len(correlation)):
        remaining = [symbol for symbol in correlation if symbol not in selected]
        selected.append(
            min(
                remaining,
                key=lambda symbol: (
                    float(correlation.loc[symbol, selected].median()),
                    symbol,
                ),
            )
        )
    return [
        Scenario("structure-loser-heavy", "structure", losers),
        Scenario(
            "structure-low-correlation",
            "structure",
            tuple(sorted(selected)),
        ),
    ]


def _structural_scenarios(data_dir: Path) -> list[Scenario]:
    by_industry: dict[str, list[str]] = {}
    for symbol in ORDERED_UNIVERSE:
        by_industry.setdefault(INDUSTRY.get(symbol, "unknown"), []).append(symbol)
    scenarios: list[Scenario] = []
    for industry, symbols in sorted(by_industry.items()):
        if len(symbols) >= 2:
            scenarios.append(
                Scenario(f"structure-{industry}", "structure", tuple(sorted(symbols)))
            )
    scenarios.extend(
        [
            Scenario(
                "structure-high-correlation",
                "structure",
                tuple(sorted(by_industry.get("optical", []) + by_industry.get("compute", []))),
            ),
            Scenario(
                "structure-memory-compute",
                "structure",
                tuple(sorted(by_industry.get("memory", []) + by_industry.get("compute", []))),
            ),
            Scenario(
                "structure-diversified",
                "structure",
                tuple(
                    sorted(
                        symbols[0]
                        for industry, symbols in by_industry.items()
                        if industry != "unknown" and symbols
                    )
                ),
            ),
            Scenario(
                "structure-mature-heavy",
                "structure",
                tuple(sorted(ORDERED_UNIVERSE[:15])),
            ),
            Scenario(
                "structure-emerging-heavy",
                "structure",
                tuple(sorted(ORDERED_UNIVERSE[-15:])),
            ),
        ]
    )
    scenarios.extend(_historical_structure_scenarios(data_dir))
    return [item for item in scenarios if item.symbols]


def build_scenarios(data_dir: Path) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for size in (1, 3, 5, 9, 10, 12, 13, 15, 16, 22, 32):
        scenarios.append(
            Scenario(f"prefix-{size:02d}", "prefix", tuple(sorted(ORDERED_UNIVERSE[:size])))
        )
    for dropped in PRIMARY:
        scenarios.append(
            Scenario(
                f"leave-one-out-{dropped}",
                "leave_one_out",
                tuple(sorted(symbol for symbol in PRIMARY if symbol != dropped)),
            )
        )
    for added in ORDERED_UNIVERSE:
        if added not in PRIMARY:
            scenarios.append(
                Scenario(
                    f"add-one-{added}",
                    "add_one",
                    tuple(sorted((*PRIMARY, added))),
                )
            )
    replacement_candidates = [
        symbol for symbol in ORDERED_UNIVERSE if symbol not in PRIMARY
    ]
    for dropped, added in zip(
        PRIMARY,
        replacement_candidates[: len(PRIMARY)],
        strict=True,
    ):
        scenarios.append(
            Scenario(
                f"replace-one-{dropped}-with-{added}",
                "replace_one",
                tuple(
                    sorted(
                        added if symbol == dropped else symbol
                        for symbol in PRIMARY
                    )
                ),
            )
        )
    scenarios.append(
        Scenario("permutation-primary-reversed", "permutation", tuple(reversed(PRIMARY)))
    )
    scenarios.extend(_structural_scenarios(data_dir))
    for seed in SEEDS:
        for size in RANDOM_SIZES:
            rng = random.Random(seed * 100 + size)
            seen: set[tuple[str, ...]] = set()
            while len(seen) < RANDOM_PER_SIZE_SEED:
                seen.add(tuple(sorted(rng.sample(ORDERED_UNIVERSE, size))))
            for sample, symbols in enumerate(sorted(seen), start=1):
                scenarios.append(
                    Scenario(
                        f"random-{seed}-{size:02d}-{sample:02d}",
                        "random_subset",
                        symbols,
                    )
                )
    identifiers = [item.scenario_id for item in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("stress scenario identifiers are not unique")
    random_count = sum(item.scenario_type == "random_subset" for item in scenarios)
    if random_count != 900:
        raise RuntimeError(f"random stress matrix must contain 900 samples, got {random_count}")
    return scenarios


def scenario_fingerprint(scenarios: list[Scenario]) -> str:
    """Canonical identity shared by new and frozen-Trade stress adapters."""
    return hashlib.sha256(
        json.dumps([asdict(item) for item in scenarios], sort_keys=True).encode()
    ).hexdigest()


def _file_fingerprint(path: Path) -> str:
    if not path.is_file():
        return "MISSING"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _python_source_fingerprint(root: Path) -> str:
    if not root.is_dir():
        return "MISSING"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and bool(np.isfinite(value))
    )


def _validate_scenario_results(
    results: Any,
    scenarios: list[Scenario],
    *,
    extended_metrics: bool,
) -> list[dict[str, Any]]:
    """Fail closed unless result rows are one-for-one with the scenario contract."""
    if not isinstance(results, list):
        raise ValueError("stress results must be a list")
    expected = {scenario.scenario_id: scenario for scenario in scenarios}
    if len(results) != len(expected):
        raise ValueError(
            f"stress result count mismatch: expected {len(expected)}, got {len(results)}"
        )
    seen: set[str] = set()
    numeric_fields = [
        "final_wealth",
        "total_return",
        "max_drawdown",
        "account_orders",
    ]
    if extended_metrics:
        numeric_fields.extend(("sharpe", "calmar", "worst_20d", "worst_60d"))
    normalized: list[dict[str, Any]] = []
    for raw in results:
        if not isinstance(raw, dict):
            raise ValueError("stress result rows must be objects")
        identifier = raw.get("scenario_id")
        if not isinstance(identifier, str) or identifier not in expected:
            raise ValueError(f"unknown stress scenario id: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"duplicate stress scenario id: {identifier}")
        seen.add(identifier)
        scenario = expected[identifier]
        if raw.get("scenario_type") != scenario.scenario_type:
            raise ValueError(f"scenario type mismatch for {identifier}")
        symbols = raw.get("symbols")
        if not isinstance(symbols, (list, tuple)) or tuple(symbols) != scenario.symbols:
            raise ValueError(f"scenario symbols mismatch for {identifier}")
        if raw.get("symbol_count") != len(scenario.symbols):
            raise ValueError(f"scenario symbol count mismatch for {identifier}")
        for field in numeric_fields:
            if not _finite_number(raw.get(field)):
                raise ValueError(f"non-finite {field} for {identifier}")
        if float(raw["final_wealth"]) <= 0:
            raise ValueError(f"non-positive final wealth for {identifier}")
        if not 0 <= float(raw["max_drawdown"]) <= 1:
            raise ValueError(f"invalid max drawdown for {identifier}")
        if (
            not isinstance(raw["account_orders"], int)
            or isinstance(raw["account_orders"], bool)
            or raw["account_orders"] < 0
        ):
            raise ValueError(f"invalid account order count for {identifier}")
        if abs(float(raw["total_return"]) - (float(raw["final_wealth"]) - 1.0)) > 1e-12:
            raise ValueError(f"wealth/return reconciliation failed for {identifier}")
        if not extended_metrics:
            sleeve_fills = raw.get("sleeve_fill_count")
            if (
                not isinstance(sleeve_fills, int)
                or isinstance(sleeve_fills, bool)
                or sleeve_fills < raw["account_orders"]
            ):
                raise ValueError(f"invalid Trade sleeve fill count for {identifier}")
        normalized.append(raw)
    if seen != set(expected):
        raise ValueError("stress scenario coverage is incomplete")
    return sorted(normalized, key=lambda row: str(row["scenario_id"]))


def _load_trade_common_stress(
    data_dir: Path,
    scenarios: list[Scenario],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load and cryptographically bind the frozen Trade common-scenario replay."""
    try:
        payload = json.loads(TRADE_COMMON_STRESS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(
            "formal Trade common stress artifact is missing; run "
            "scripts/run_trade_common_stress.py without --limit"
        ) from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("formal Trade common stress artifact is unreadable") from error
    expected_contract = {
        "initial_cash": 2_000_000.0,
        "start": STRESS_START,
        "end": STRESS_END,
        "signal": "close_t",
        "execution": "next_tradable_open",
        "account_orders": "unique executed fill_date/symbol/side",
    }
    scenario_hash = scenario_fingerprint(scenarios)
    expected_data = {
        "through": STRESS_END,
        "sha256": bounded_data_fingerprint(data_dir, end=STRESS_END),
    }
    checks = {
        "schema_version": payload.get("schema_version") == 1,
        "formal_complete": payload.get("formal_complete") is True,
        "adapter_sha256": payload.get("adapter_sha256")
        == _file_fingerprint(TRADE_COMMON_ADAPTER_PATH),
        "common_adapter_sha256": payload.get("common_adapter_sha256")
        == _file_fingerprint(LEGACY_COMMON_ADAPTER_PATH),
        "trade_source_sha256": payload.get("trade_source_sha256")
        == _python_source_fingerprint(TRADE_SOURCE_ROOT),
        "contract": payload.get("contract") == expected_contract,
        "data_provenance": payload.get("data_provenance") == expected_data,
        "scenario_sha256": payload.get("scenario_sha256") == scenario_hash,
        "scenario_count": payload.get("scenario_count") == len(scenarios),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError(
            "formal Trade common stress artifact failed provenance checks: "
            + ", ".join(failed)
        )
    rows = _validate_scenario_results(
        payload.get("results"), scenarios, extended_metrics=False
    )
    return payload, rows


def _signature(data_dir: Path, scenarios: list[Scenario]) -> dict[str, Any]:
    data_hash = bounded_data_fingerprint(data_dir, end=STRESS_END)
    scenario_hash = scenario_fingerprint(scenarios)
    return {
        "production_code_sha256": code_fingerprint(),
        "validation_code_sha256": validation_fingerprint(),
        "data_sha256": data_hash,
        "scenario_sha256": scenario_hash,
        "config_sha256": config_fingerprint(),
        "trade_common_stress_sha256": _file_fingerprint(TRADE_COMMON_STRESS_PATH),
    }


def artifact_is_current(path: Path, data_dir: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scenarios = build_scenarios(data_dir)
    try:
        trade_payload, trade_rows = _load_trade_common_stress(data_dir, scenarios)
        rows = _validate_scenario_results(
            payload.get("results"), scenarios, extended_metrics=True
        )
    except ValueError:
        return False
    return bool(
        payload.get("schema_version") == 2
        and payload.get("signature") == _signature(data_dir, scenarios)
        and payload.get("common_scenario_sha256") == scenario_fingerprint(scenarios)
        and payload.get("trade_artifact_sha256")
        == _file_fingerprint(TRADE_COMMON_STRESS_PATH)
        and payload.get("summary") == _summarize(rows)
        and payload.get("trade_summary") == _summarize(trade_rows)
        and trade_payload.get("formal_complete") is True
    )


def _quantile(rows: list[dict[str, Any]], key: str, quantile: float) -> float:
    return float(np.quantile([float(row[key]) for row in rows], quantile))


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_type.setdefault(str(row["scenario_type"]), []).append(row)
    random_rows = by_type["random_subset"]
    prefix = {int(row["symbol_count"]): row for row in by_type["prefix"]}
    base = prefix[5]
    add_changes = [row["final_wealth"] / base["final_wealth"] - 1.0 for row in by_type["add_one"]]
    remove_changes = [
        row["final_wealth"] / base["final_wealth"] - 1.0 for row in by_type["leave_one_out"]
    ]
    boundaries = {
        f"{left}->{right}": prefix[right]["final_wealth"] / prefix[left]["final_wealth"] - 1.0
        for left, right in ((9, 10), (12, 13), (15, 16))
    }
    permutation = by_type["permutation"][0]
    return {
        "scenario_count": len(results),
        "random": {
            "scenario_count": len(random_rows),
            "return_median": float(np.median([row["total_return"] for row in random_rows])),
            "return_p10": _quantile(random_rows, "total_return", 0.10),
            "return_worst": min(row["total_return"] for row in random_rows),
            "drawdown_p90": _quantile(random_rows, "max_drawdown", 0.90),
            "drawdown_worst": max(row["max_drawdown"] for row in random_rows),
            "orders_p90": _quantile(random_rows, "account_orders", 0.90),
            "orders_worst": max(row["account_orders"] for row in random_rows),
        },
        "add_one": {
            "scenario_count": len(add_changes),
            "worst_wealth_change": min(add_changes),
        },
        "leave_one_out": {
            "scenario_count": len(remove_changes),
            "worst_wealth_change": min(remove_changes),
        },
        "size_boundaries": boundaries,
        "permutation": {
            "scenario_count": 1,
            "wealth_change": permutation["final_wealth"] / base["final_wealth"] - 1.0,
            "drawdown_change": permutation["max_drawdown"] - base["max_drawdown"],
            "order_change": permutation["account_orders"] - base["account_orders"],
            "verified_by": "actual reversed-input production replay plus decision-digest contract test",
        },
        "replace_one": {
            "scenario_count": len(by_type["replace_one"]),
            "worst_wealth_change": min(
                row["final_wealth"] / base["final_wealth"] - 1.0
                for row in by_type["replace_one"]
            ),
        },
        "structures": sorted(row["scenario_id"] for row in by_type["structure"]),
    }


def run_stress(data_dir: Path, output_path: Path, *, workers: int | None = None) -> dict[str, Any]:
    scenarios = build_scenarios(data_dir)
    trade_payload, trade_results = _load_trade_common_stress(data_dir, scenarios)
    initial_signature = _signature(data_dir, scenarios)
    worker_count = workers or min(4, max(1, os.cpu_count() or 1))
    results: list[dict[str, Any]] = []
    print(f"stress: executing {len(scenarios)} scenarios with {worker_count} workers", flush=True)
    with ProcessPoolExecutor(
        max_workers=worker_count,
        initializer=_init_worker,
        initargs=(str(data_dir),),
    ) as pool:
        for index, row in enumerate(pool.map(_run_scenario, scenarios, chunksize=1), start=1):
            results.append(row)
            if index % 50 == 0 or index == len(scenarios):
                print(f"stress: {index}/{len(scenarios)}", flush=True)
    results.sort(key=lambda row: row["scenario_id"])
    current_signature = _signature(data_dir, scenarios)
    assert_replay_signature_unchanged(
        initial_signature,
        current_signature,
        replay="stress",
    )
    payload = {
        "schema_version": 2,
        "engine": "ProductionEngine.backtest",
        "window": [STRESS_START, STRESS_END],
        "seeds": list(SEEDS),
        "random_samples_per_size_per_seed": RANDOM_PER_SIZE_SEED,
        "signature": initial_signature,
        "common_scenario_sha256": scenario_fingerprint(scenarios),
        "trade_artifact_sha256": _file_fingerprint(TRADE_COMMON_STRESS_PATH),
        "trade_source_sha256": trade_payload["trade_source_sha256"],
        "summary": _summarize(results),
        "trade_summary": _summarize(trade_results),
        "results": results,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
