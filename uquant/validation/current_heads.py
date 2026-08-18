"""Fail-closed contracts for the four-current-HEAD comparison evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from .ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
from .competitor import CANONICAL_EXECUTION_CONTRACT
from .generalization_contract import (
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
        not isinstance(symbols, list)
        or not symbols
        or len(symbols) != len(set(symbols))
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
    observed_adapter = (
        hashlib.sha256(adapter_path.read_bytes()).hexdigest() if adapter_path is not None else None
    )
    expected = dict(expected_heads) if expected_heads is not None else None
    if expected is not None and set(expected) != set(REQUIRED_SYSTEMS):
        raise ValueError("expected remote HEAD set is incomplete")
    for name in REQUIRED_SYSTEMS:
        entry = repositories[name]
        if not isinstance(entry, dict) or set(entry) != _REPOSITORY_FIELDS:
            raise ValueError(f"{name} source registry fields are incomplete")
        if entry["repository"] != _REPOSITORIES[name]:
            raise ValueError(f"{name} repository identity mismatch")
        _require_hash(
            entry["commit"], field="commit", pattern=_SHA40, label="a 40-character SHA"
        )
        _require_hash(
            entry["tree_sha"], field="tree_sha", pattern=_SHA40, label="a 40-character SHA"
        )
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

