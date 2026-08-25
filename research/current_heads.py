"""Fail-closed contracts for the four-current-HEAD comparison evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, cast

from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
from uquant.validation.competitor import CANONICAL_EXECUTION_CONTRACT
from uquant.validation.generalization_contract import (
    CORE_SYMBOLS,
    INDUSTRY_MIN_SAMPLE,
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
)

REQUIRED_SYSTEMS: Final = ("uquant", "aquant", "qwenquant", "trade")
MATRIX_STATUSES: Final = ("SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE")
REQUIRED_METRICS: Final = (
    "final_wealth",
    "total_return",
    "cagr",
    "sharpe",
    "calmar",
    "max_drawdown",
    "account_orders",
    "gross_turnover",
    "annual_turnover",
    "acute_return",
    "top1_concentration",
    "top3_concentration",
    "pnl_hhi",
)

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORIES = {
    "uquant": "ychenracing/uquant",
    "aquant": "ychenracing/aquant",
    "qwenquant": "ychenracing/qwenquant",
    "trade": "ychenracing/trade",
}
_REPOSITORY_FIELDS = {
    "repository",
    "commit",
    "tree_sha",
    "python_source_sha256",
    "python_source_files",
    "lock_sha256",
    "lock_files",
    "adapter_sha256",
    "read_only",
}
_CELL_FIELDS = {
    "cell_id",
    "axis",
    "system",
    "window",
    "start",
    "end",
    "name",
    "family",
    "symbols",
    "effective_symbols",
    "status",
    "metrics",
    "error",
    "provenance",
}
_CELL_PROVENANCE_FIELDS = {
    "system_commit",
    "data_sha256",
    "config_sha256",
    "runtime_sha256",
    "evidence_sha256",
}

_THIN_ADAPTER_IMPORT = b"from research import current_heads_competitor_matrix as _implementation"


def _current_heads_adapter_sha256(adapter_path: Path) -> str:
    """Project the reviewed thin entry to its byte-exact research owner."""

    entry = adapter_path.read_bytes()
    owner = adapter_path.parent.parent / "research" / "current_heads_competitor_matrix.py"
    payload = owner.read_bytes() if _THIN_ADAPTER_IMPORT in entry and owner.is_file() else entry
    return hashlib.sha256(payload).hexdigest()


def canonical_sha256(value: Any) -> str:
    """Hash strict canonical JSON without accepting NaN or lossy values."""

    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def python_source_sha256(root: Path) -> str:
    """Hash every Python source by stable relative path and exact file bytes."""

    paths = sorted(root.rglob("*.py"), key=lambda item: item.relative_to(root).as_posix())
    if not paths:
        raise ValueError(f"Python source tree is empty: {root}")
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Python source must be a regular file: {path}")
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def dependency_files_sha256(root: Path, relative_paths: Sequence[str]) -> str:
    """Hash an explicit, ordered-independent dependency-lock file set."""

    normalized = tuple(sorted(relative_paths))
    if not normalized or len(normalized) != len(set(normalized)):
        raise ValueError("dependency lock file set must be non-empty and unique")
    digest = hashlib.sha256()
    for relative in normalized:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"dependency lock must be a regular file: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _load_hashed_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load current-head contract: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("current-head contract must be an object")
    claimed = payload.get("payload_sha256")
    body = {key: value for key, value in payload.items() if key != "payload_sha256"}
    if not isinstance(claimed, str) or claimed != canonical_sha256(body):
        raise ValueError("current-head payload SHA-256 mismatch")
    return payload


def load_comparison_contract(path: Path) -> dict[str, Any]:
    """Load and validate the exact shared market, execution, and matrix axes."""

    payload = _load_hashed_payload(path)
    expected_execution = {
        **CANONICAL_EXECUTION_CONTRACT.to_payload(),
        "stock_adjustment": "qfq",
        "index_adjustment": "raw",
        "position_direction": "cash_long_only",
        "star_board_rules": True,
        "price_limits": True,
        "capacity": True,
    }
    expected_windows = {
        name: {
            "start": bounds[0],
            "end": bounds[1],
            "acute_start": AI_ERA_ACUTE_WINDOWS[name][0],
            "acute_end": AI_ERA_ACUTE_WINDOWS[name][1],
        }
        for name, bounds in AI_ERA_WINDOWS.items()
    }
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported current-head comparison contract schema")
    if payload.get("contract_id") != "current-heads-comparison-v1":
        raise ValueError("current-head comparison contract ID mismatch")
    if payload.get("systems") != list(REQUIRED_SYSTEMS):
        raise ValueError("current-head comparison systems mismatch")
    if payload.get("market") != "A-share AI supply chain":
        raise ValueError("current-head comparison market mismatch")
    if payload.get("execution_contract") != expected_execution:
        raise ValueError("current-head execution contract mismatch")
    if payload.get("windows") != expected_windows:
        raise ValueError("current-head official or acute windows mismatch")
    pools = payload.get("official_pools")
    if not isinstance(pools, dict) or tuple(pools) != ("a", "b", "c", "d", "e"):
        raise ValueError("current-head official pools mismatch")
    if any(
        not isinstance(symbols, list) or not symbols or len(symbols) != len(set(symbols))
        for symbols in pools.values()
    ):
        raise ValueError("current-head official pool membership is malformed")
    generalization = payload.get("generalization")
    expected_generalization = {
        "records_per_window": 39,
        "ready_records_per_window": 32,
        "insufficient_records_per_window": 7,
        "full_universe": True,
        "remove_one_core": list(CORE_SYMBOLS),
        "remove_all_core": True,
        "no_optical": True,
        "industry_balanced": True,
        "effective_subindustries": True,
        "industry_min_sample": INDUSTRY_MIN_SAMPLE,
        "random_pool_sizes": list(RANDOM_POOL_SIZES),
        "random_seed_indexes": list(RANDOM_SEED_INDEXES),
        "random_base_seed": RANDOM_BASE_SEED,
    }
    if generalization != expected_generalization:
        raise ValueError("current-head generalization contract mismatch")
    if payload.get("metrics") != list(REQUIRED_METRICS):
        raise ValueError("current-head metric schema mismatch")
    if payload.get("statuses") != list(MATRIX_STATUSES):
        raise ValueError("current-head status schema mismatch")
    if payload.get("expected_cells") != {
        "official_pool": 120,
        "generalization": 936,
        "total": 1056,
    }:
        raise ValueError("current-head expected matrix dimensions mismatch")
    return payload


def _require_hash(value: Any, *, field: str, pattern: re.Pattern[str], label: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field} must be {label}")


def load_source_registry(
    path: Path,
    *,
    adapter_path: Path | None = None,
    expected_heads: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Load four source identities and reject stale or incomplete evidence."""

    payload = _load_hashed_payload(path)
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported current-head source registry schema")
    if payload.get("registry_id") != "current-heads-source-registry-v1":
        raise ValueError("current-head source registry ID mismatch")
    if payload.get("systems") != list(REQUIRED_SYSTEMS):
        raise ValueError("current-head source registry systems mismatch")
    repositories = payload.get("repositories")
    if not isinstance(repositories, dict) or tuple(repositories) != REQUIRED_SYSTEMS:
        raise ValueError("current-head source registry repositories mismatch")
    observed_adapter = _current_heads_adapter_sha256(adapter_path) if adapter_path is not None else None
    expected = dict(expected_heads) if expected_heads is not None else None
    if expected is not None and set(expected) != set(REQUIRED_SYSTEMS):
        raise ValueError("expected remote HEAD set is incomplete")
    for name in REQUIRED_SYSTEMS:
        entry = repositories[name]
        if not isinstance(entry, dict) or set(entry) != _REPOSITORY_FIELDS:
            raise ValueError(f"{name} source registry fields are incomplete")
        if entry["repository"] != _REPOSITORIES[name]:
            raise ValueError(f"{name} repository identity mismatch")
        _require_hash(entry["commit"], field="commit", pattern=_SHA40, label="a 40-character SHA")
        _require_hash(entry["tree_sha"], field="tree_sha", pattern=_SHA40, label="a 40-character SHA")
        for field in ("python_source_sha256", "lock_sha256", "adapter_sha256"):
            _require_hash(entry[field], field=field, pattern=_SHA256, label="SHA-256")
        if not isinstance(entry["python_source_files"], int) or entry["python_source_files"] < 1:
            raise ValueError(f"{name} Python source file count is invalid")
        lock_files = entry["lock_files"]
        if (
            not isinstance(lock_files, list)
            or not lock_files
            or not all(isinstance(item, str) and item for item in lock_files)
            or lock_files != sorted(set(lock_files))
        ):
            raise ValueError(f"{name} dependency lock file list is invalid")
        if entry["read_only"] is not True:
            raise ValueError(f"{name} source registry must be read-only")
        if observed_adapter is not None and entry["adapter_sha256"] != observed_adapter:
            raise ValueError(f"{name} adapter SHA-256 mismatch")
        if expected is not None and entry["commit"] != expected[name]:
            raise ValueError(f"{name} remote HEAD mismatch")
    return payload


def validate_matrix_cell(
    cell: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> None:
    """Fail closed on one committed current-HEAD matrix record."""

    if set(cell) != _CELL_FIELDS:
        raise ValueError("matrix cell fields are incomplete or unexpected")
    system = cell.get("system")
    axis = cell.get("axis")
    window = cell.get("window")
    name = cell.get("name")
    family = cell.get("family")
    if system not in REQUIRED_SYSTEMS:
        raise ValueError("matrix cell system is unknown")
    if axis not in {"official_pool", "generalization"}:
        raise ValueError("matrix cell axis is unknown")
    windows = contract.get("windows")
    if not isinstance(windows, Mapping) or window not in windows:
        raise ValueError("matrix cell window is unknown")
    bounds = windows[window]
    if not isinstance(bounds, Mapping) or (
        cell.get("start"),
        cell.get("end"),
    ) != (bounds.get("start"), bounds.get("end")):
        raise ValueError("matrix cell window bounds differ from the contract")
    if not isinstance(name, str) or not name or not isinstance(family, str) or not family:
        raise ValueError("matrix cell identity is incomplete")
    expected_id = f"{axis}/{system}/{window}/{name}"
    if cell.get("cell_id") != expected_id:
        raise ValueError("matrix cell ID differs from its dimensions")
    symbols = cell.get("symbols")
    effective = cell.get("effective_symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or not all(isinstance(item, str) and item for item in symbols)
        or len(symbols) != len(set(symbols))
    ):
        raise ValueError("matrix cell symbols are malformed")
    if (
        not isinstance(effective, list)
        or not all(isinstance(item, str) and item for item in effective)
        or len(effective) != len(set(effective))
    ):
        raise ValueError("matrix cell effective_symbols are malformed")
    symbols_list = cast(list[str], symbols)
    effective_list = cast(list[str], effective)
    if not set(effective_list).issubset(symbols_list):
        raise ValueError("matrix effective symbols are outside the requested pool")
    if axis == "official_pool":
        pools = contract.get("official_pools")
        if (
            family != "official_pool"
            or not isinstance(pools, Mapping)
            or name not in pools
            or symbols_list != pools[name]
        ):
            raise ValueError("official pool membership differs from the contract")

    status = cell.get("status")
    metrics = cell.get("metrics")
    error = cell.get("error")
    if status == "SUCCESS":
        if not isinstance(metrics, Mapping) or error is not None:
            raise ValueError("SUCCESS requires metrics only")
        if set(metrics) != set(REQUIRED_METRICS):
            raise ValueError("SUCCESS metric schema differs")
        for field, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"matrix metric is not numeric: {field}")
            if not math.isfinite(float(value)):
                raise ValueError(f"matrix metric is not finite: {field}")
        orders = metrics["account_orders"]
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise ValueError("matrix account_orders is invalid")
        if float(metrics["final_wealth"]) <= 0:
            raise ValueError("matrix final_wealth is invalid")
        if not 0 <= float(metrics["max_drawdown"]) <= 1:
            raise ValueError("matrix max_drawdown is invalid")
        for field in (
            "gross_turnover",
            "annual_turnover",
            "top1_concentration",
            "top3_concentration",
            "pnl_hhi",
        ):
            if float(metrics[field]) < 0:
                raise ValueError(f"matrix metric is negative: {field}")
    elif status in {"REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
        if (
            metrics is not None
            or not isinstance(error, Mapping)
            or set(error) != {"class", "message"}
            or not all(isinstance(value, str) and value for value in error.values())
        ):
            raise ValueError(f"{status} requires explicit error only")
    else:
        raise ValueError("matrix cell status is unknown")

    provenance = cell.get("provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != _CELL_PROVENANCE_FIELDS:
        raise ValueError("matrix cell provenance is incomplete")
    repository = registry.get("repositories", {}).get(system)
    if not isinstance(repository, Mapping):
        raise ValueError("matrix system is absent from source registry")
    _require_hash(
        provenance.get("system_commit"),
        field="system_commit",
        pattern=_SHA40,
        label="a 40-character SHA",
    )
    if provenance.get("system_commit") != repository.get("commit"):
        raise ValueError("matrix cell commit differs from source registry")
    for field in _CELL_PROVENANCE_FIELDS - {"system_commit"}:
        _require_hash(provenance.get(field), field=field, pattern=_SHA256, label="SHA-256")


def _matrix_quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _matrix_aggregates(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for system in REQUIRED_SYSTEMS:
        result[system] = {}
        for axis in ("official_pool", "generalization"):
            group = [item for item in cells if item["system"] == system and item["axis"] == axis]
            success = [item for item in group if item["status"] == "SUCCESS"]
            metric_summary: dict[str, Any] = {}
            for field in REQUIRED_METRICS:
                values = [float(item["metrics"][field]) for item in success]
                if values:
                    metric_summary[field] = {
                        "min": min(values),
                        "p10": _matrix_quantile(values, 0.10),
                        "median": _matrix_quantile(values, 0.50),
                        "p90": _matrix_quantile(values, 0.90),
                        "max": max(values),
                    }
            result[system][axis] = {
                "cells": len(group),
                "success": len(success),
                "replay_error": sum(item["status"] == "REPLAY_ERROR" for item in group),
                "insufficient_sample": sum(item["status"] == "INSUFFICIENT_SAMPLE" for item in group),
                "metrics": metric_summary,
            }
    return result


def load_current_heads_matrix(
    path: Path,
    *,
    contract_path: Path,
    registry_path: Path,
    adapter_path: Path | None = None,
) -> dict[str, Any]:
    """Load and independently read back the complete 1,056-cell matrix."""

    contract = load_comparison_contract(contract_path)
    registry = load_source_registry(registry_path, adapter_path=adapter_path)
    payload = _load_hashed_payload(path)
    expected_fields = {
        "schema_version",
        "contract_sha256",
        "source_registry_sha256",
        "adapter_sha256",
        "data",
        "runtimes",
        "summary",
        "aggregates",
        "legacy_source_diagnostic",
        "cells",
        "payload_sha256",
    }
    if set(payload) != expected_fields or payload.get("schema_version") != 1:
        raise ValueError("current-head matrix schema is incomplete or unsupported")
    if payload.get("contract_sha256") != contract["payload_sha256"]:
        raise ValueError("matrix comparison contract identity differs")
    if payload.get("source_registry_sha256") != registry["payload_sha256"]:
        raise ValueError("matrix source registry identity differs")
    if adapter_path is not None:
        observed_adapter = _current_heads_adapter_sha256(adapter_path)
        if payload.get("adapter_sha256") != observed_adapter:
            raise ValueError("matrix adapter identity differs")
    data = payload.get("data")
    if (
        not isinstance(data, Mapping)
        or set(data) != {"snapshot", "bounded_windows"}
        or not isinstance(data["snapshot"], Mapping)
        or not isinstance(data["bounded_windows"], Mapping)
        or set(data["bounded_windows"]) != set(contract["windows"])
    ):
        raise ValueError("matrix bounded-data provenance is incomplete")
    runtimes = payload.get("runtimes")
    if (
        not isinstance(runtimes, Mapping)
        or set(runtimes) != set(REQUIRED_SYSTEMS)
        or any(not isinstance(runtimes[name], Mapping) for name in REQUIRED_SYSTEMS)
    ):
        raise ValueError("matrix runtime provenance is incomplete")
    uquant_runtimes = runtimes["uquant"]
    if (
        not isinstance(uquant_runtimes, Mapping)
        or set(uquant_runtimes) != {"official", "generalization"}
        or any(not isinstance(uquant_runtimes[axis], Mapping) for axis in ("official", "generalization"))
    ):
        raise ValueError("uquant axis-specific runtime provenance is incomplete")
    cells = payload.get("cells")
    if not isinstance(cells, list) or len(cells) != contract["expected_cells"]["total"]:
        raise ValueError("current-head matrix does not contain exactly 1,056 cells")
    if any(not isinstance(cell, Mapping) for cell in cells):
        raise ValueError("current-head matrix contains a malformed cell")
    for cell in cells:
        validate_matrix_cell(cell, contract=contract, registry=registry)
        system_runtime = runtimes[cell["system"]]
        if cell["system"] == "uquant":
            system_runtime = system_runtime[
                "official" if cell["axis"] == "official_pool" else "generalization"
            ]
        expected_runtime = canonical_sha256(system_runtime)
        if cell["provenance"]["runtime_sha256"] != expected_runtime:
            raise ValueError("matrix cell runtime differs from system runtime provenance")

    identifiers = [str(cell["cell_id"]) for cell in cells]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("current-head matrix contains duplicate cell identities")
    signatures_by_system: dict[str, set[tuple[Any, ...]]] = {}
    for system in REQUIRED_SYSTEMS:
        system_cells = [cell for cell in cells if cell["system"] == system]
        official = [cell for cell in system_cells if cell["axis"] == "official_pool"]
        generalization = [cell for cell in system_cells if cell["axis"] == "generalization"]
        if len(system_cells) != 264 or len(official) != 30 or len(generalization) != 234:
            raise ValueError(f"{system} matrix dimensions are incomplete")
        expected_official = {
            f"official_pool/{system}/{window}/{pool}"
            for window in contract["windows"]
            for pool in contract["official_pools"]
        }
        if {cell["cell_id"] for cell in official} != expected_official:
            raise ValueError(f"{system} official matrix identities differ")
        if sum(cell["status"] == "INSUFFICIENT_SAMPLE" for cell in generalization) != 42:
            raise ValueError(f"{system} insufficient-sample evidence count differs")
        signatures_by_system[system] = {
            (
                cell["axis"],
                cell["window"],
                cell["start"],
                cell["end"],
                cell["name"],
                cell["family"],
                tuple(cell["symbols"]),
            )
            for cell in system_cells
        }
    reference = signatures_by_system[REQUIRED_SYSTEMS[0]]
    if any(signatures_by_system[system] != reference for system in REQUIRED_SYSTEMS[1:]):
        raise ValueError("systems do not share one identical preregistered request matrix")

    summary = {
        "cells": len(cells),
        "success": sum(cell["status"] == "SUCCESS" for cell in cells),
        "replay_error": sum(cell["status"] == "REPLAY_ERROR" for cell in cells),
        "insufficient_sample": sum(cell["status"] == "INSUFFICIENT_SAMPLE" for cell in cells),
        "official_pool_cells": sum(cell["axis"] == "official_pool" for cell in cells),
        "generalization_cells": sum(cell["axis"] == "generalization" for cell in cells),
    }
    if payload.get("summary") != summary:
        raise ValueError("matrix summary differs from its literal cells")
    if payload.get("aggregates") != _matrix_aggregates(cells):
        raise ValueError("matrix aggregates differ from their literal cells")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a committed current-HEAD matrix from an independent entry point."""

    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(prog="python -m research.current_heads")
    parser.add_argument(
        "--matrix",
        type=Path,
        default=root / "benchmarks/current_heads_competitor_matrix.json",
    )
    parser.add_argument(
        "--contract",
        type=Path,
        default=root / "benchmarks/current_heads_comparison_contract.json",
    )
    parser.add_argument(
        "--source-registry",
        type=Path,
        default=root / "benchmarks/current_heads_source_registry.json",
    )
    parser.add_argument(
        "--adapter",
        type=Path,
        default=root / "scripts/run_current_heads_competitor_matrix.py",
    )
    args = parser.parse_args(argv)
    payload = load_current_heads_matrix(
        args.matrix,
        contract_path=args.contract,
        registry_path=args.source_registry,
        adapter_path=args.adapter,
    )
    print(json.dumps(payload["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
