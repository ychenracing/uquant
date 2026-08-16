"""The single blocking 2023+ AI-era production-promotion contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess  # nosec B404 - fixed git commands, never a shell
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Final, cast

from ..config import DEFAULT_CONFIG, config_fingerprint
from ..config_governance import GOVERNANCE_PATH
from ..engine import ProductionEngine
from .ai_era import (
    AI_ERA_ACUTE_WINDOWS,
    AI_ERA_START,
    AI_ERA_WINDOWS,
    require_ai_era_interval,
    runtime_environment_provenance,
)
from .manifest import verify_data_manifest

SCHEMA_VERSION: Final = 4
REPOSITORY: Final = "ychenracing/uquant"
CONFIG_PARAMETER_GOVERNANCE_PATH: Final = GOVERNANCE_PATH.as_posix()
REQUIRED_POOLS: Final = ("a", "b", "c", "d", "e")
# These hashes are independent trust anchors.  Editing the baseline and
# recomputing its self-fingerprint cannot silently change the reviewed A-E
# universe or replace/erase the production champion.
REQUIRED_POOL_SHA256: Final = "69526f3d1b7fa777c5729a6883f5f50a236c57fc54434ae675270f05f918f95d"
REQUIRED_CHAMPION_SHA256: Final = "e7f773becb1fa2880013172a69b1cf55ea82586a0621e9de9c60deaa89f331f8"
REQUIRED_BASELINE_SHA256: Final = "6f04569dc1449673b885e485a5e3ea051f4eb2898f2af876ac9c3b4fb6b7eb29"
REFERENCE_REGISTRY_PATH: Final = "benchmarks/reference_registry.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")

EXECUTION_CONTRACT: Final[dict[str, Any]] = {
    "engine": "uquant.engine.ProductionEngine",
    "initial_cash": 2_000_000.0,
    "market": "A-share AI supply chain",
    "positioning": "cash-only long",
    "decision": "daily close t",
    "execution": "next tradable open",
    "intraday_exit": False,
    "automation": "human-assisted, no broker submission",
    "prelisting": "invisible until first observable row",
}

# These intervals retain the report's explicit regression protections. They
# overlap the six official windows, but are evaluated independently so a good
# half-year aggregate cannot hide the reported 2023, 2024, or bull regression.
PROTECTED_INTERVALS: Final[dict[str, dict[str, str]]] = {
    "year_2023": {"start": "2023-01-03", "end": "2023-12-29"},
    "year_2024": {"start": "2024-01-02", "end": "2024-12-31"},
    "bull": {"start": "2025-04-01", "end": "2026-06-30"},
}

# Hard limits are compiled into validation before champion metrics are frozen.
# A baseline edit therefore cannot weaken a gate or bless a bad candidate.
AI_ERA_POLICY: Final[dict[str, Any]] = {
    "schema_version": 1,
    "official": {
        "h1_2023": {
            "min_final_wealth": 4.50,
            "max_drawdown": 0.18,
            "max_account_orders": 3,
            "max_annual_turnover": 5.0,
            "min_acute_return": -0.15,
        },
        "h2_2023": {
            "min_final_wealth": 1.00,
            "max_drawdown": 0.16,
            "max_account_orders": 7,
            "max_annual_turnover": 8.0,
            "min_acute_return": -0.10,
        },
        "h1_2024": {
            "min_final_wealth": 1.68,
            "max_drawdown": 0.18,
            "max_account_orders": 9,
            "max_annual_turnover": 7.0,
            "min_acute_return": -0.21,
        },
        "h2_2024": {
            "min_final_wealth": 1.00,
            "max_drawdown": 0.10,
            "max_account_orders": 11,
            "max_annual_turnover": 15.0,
            "min_acute_return": -0.10,
        },
        "bull_crash_2025_2026": {
            "min_final_wealth": 8.00,
            "max_drawdown": 0.30,
            "max_account_orders": 25,
            "max_annual_turnover": 20.0,
            "min_acute_return": -0.03,
        },
        "continuous_ai_era": {
            "min_final_wealth": 15.00,
            "max_drawdown": 0.2725,
            "max_account_orders": 15,
            # The phase-one candidate must first prove the explicit crash
            # floor; the resulting AI-era turnover is then frozen in the
            # champion and becomes blocking through champion_tolerance.
            "max_annual_turnover": None,
            "min_acute_return": -0.03,
        },
    },
    "protected": {
        "year_2023": {
            "groups": {
                "abcde": {
                    "pools": ["a", "b", "c", "d", "e"],
                    "min_final_wealth": 3.94,
                    "max_drawdown": 0.2725,
                    "max_account_orders": 4,
                    "max_annual_turnover": 4.2241,
                }
            }
        },
        "year_2024": {
            "groups": {
                "abcde": {
                    "pools": ["a", "b", "c", "d", "e"],
                    "min_final_wealth": 1.7314,
                    "max_drawdown": 0.18,
                    "max_account_orders": 12,
                    "max_annual_turnover": 2.9965,
                }
            }
        },
        "bull": {
            "groups": {
                "abc": {
                    "pools": ["a", "b", "c"],
                    "min_final_wealth": 12.827,
                    "max_drawdown": 0.18,
                    "max_account_orders": 11,
                    "max_annual_turnover": None,
                },
                "de": {
                    "pools": ["d", "e"],
                    "min_final_wealth": 12.933,
                    "max_drawdown": 0.20,
                    "max_account_orders": 13,
                    "max_annual_turnover": 14.451,
                },
            }
        },
    },
    "champion_tolerance": {
        "wealth_floor_ratio": 0.99,
        "drawdown_tolerance": 0.005,
        "order_tolerance": 1,
        "turnover_tolerance": 0.25,
        "acute_return_tolerance": 0.0,
    },
}

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "validation_fingerprint",
    "contract",
    "pools",
    "policy",
    "champion",
    "provenance",
}
_METRIC_FIELDS = {
    "final_wealth",
    "cagr",
    "max_drawdown",
    "sharpe",
    "calmar",
    "account_orders",
    "annual_turnover",
    "gross_turnover",
    "acute_return",
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"promotion baseline contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise RuntimeError(f"promotion baseline contains a non-standard number: {value}")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validation_fingerprint(spec: Mapping[str, Any]) -> str:
    """Hash every immutable gate input except the hash field itself."""

    return _fingerprint({key: spec[key] for key in sorted(spec) if key != "validation_fingerprint"})


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"promotion value must be numeric: {label}")
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"promotion value must be finite: {label}")
    return number


def _validate_metric_payload(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != _METRIC_FIELDS:
        raise RuntimeError(f"promotion champion metric payload is malformed: {label}")
    for field in _METRIC_FIELDS - {"account_orders", "acute_return"}:
        _finite_number(value[field], label=f"{label}.{field}")
    orders = value["account_orders"]
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise RuntimeError(f"promotion champion account_orders is malformed: {label}")
    acute = value["acute_return"]
    if acute is not None:
        _finite_number(acute, label=f"{label}.acute_return")


def _contract_payload() -> dict[str, Any]:
    return {
        "ai_era_start": AI_ERA_START,
        "windows": {
            name: {"start": start, "end": end}
            for name, (start, end) in AI_ERA_WINDOWS.items()
        },
        "acute_windows": {
            name: {"start": start, "end": end}
            for name, (start, end) in AI_ERA_ACUTE_WINDOWS.items()
        },
        "protected_intervals": deepcopy(PROTECTED_INTERVALS),
        "execution": deepcopy(EXECUTION_CONTRACT),
    }


def _validate_spec(spec: Mapping[str, Any]) -> None:
    """Reject any incomplete, weakened, or pre-2023 economic contract."""

    if set(spec) != _TOP_LEVEL_FIELDS:
        raise RuntimeError("promotion baseline must contain the exact schema-v4 sections")
    if spec["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError("unsupported promotion baseline schema")
    contract = spec["contract"]
    if not isinstance(contract, Mapping):
        raise RuntimeError("promotion contract is malformed")
    for section in ("windows", "acute_windows", "protected_intervals"):
        intervals = contract.get(section)
        if not isinstance(intervals, Mapping):
            raise RuntimeError(f"promotion contract section is malformed: {section}")
        for name, interval in intervals.items():
            if not isinstance(interval, Mapping) or set(interval) != {"start", "end"}:
                raise RuntimeError(f"promotion interval is malformed: {section}.{name}")
            require_ai_era_interval(str(interval["start"]), str(interval["end"]))
    if spec["contract"] != _contract_payload():
        raise RuntimeError("promotion contract differs from the exact official windows")
    if spec["policy"] != AI_ERA_POLICY:
        raise RuntimeError("promotion policy differs from compiled AI-era policy")

    pools = spec["pools"]
    if not isinstance(pools, Mapping) or tuple(pools) != REQUIRED_POOLS:
        raise RuntimeError("promotion must contain ordered pools a-e")
    for pool, raw_symbols in pools.items():
        if (
            not isinstance(raw_symbols, list)
            or not raw_symbols
            or len(raw_symbols) != len(set(raw_symbols))
            or any(not isinstance(symbol, str) or not symbol for symbol in raw_symbols)
        ):
            raise RuntimeError(f"promotion pool is malformed: {pool}")
    if _fingerprint(pools) != REQUIRED_POOL_SHA256:
        raise RuntimeError("promotion pools differ from the compiled reviewed universe")

    for name, acute in contract["acute_windows"].items():
        window = contract["windows"][name]
        if not window["start"] <= acute["start"] <= acute["end"] <= window["end"]:
            raise RuntimeError(f"promotion acute interval is outside its official window: {name}")

    champion = spec["champion"]
    if not isinstance(champion, Mapping) or set(champion) != {
        "production_commit",
        "cells",
        "protected",
    }:
        raise RuntimeError("promotion champion is malformed")
    commit = champion["production_commit"]
    cells = champion["cells"]
    protected = champion["protected"]
    if not isinstance(commit, str) or not isinstance(cells, Mapping) or not isinstance(protected, Mapping):
        raise RuntimeError("promotion champion is malformed")
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("promotion champion commit is not immutable")
    expected_cells = {
        f"{pool}/{window}" for pool in REQUIRED_POOLS for window in AI_ERA_WINDOWS
    }
    expected_protected = {
        f"{pool}/{interval}" for pool in REQUIRED_POOLS for interval in PROTECTED_INTERVALS
    }
    if set(cells) != expected_cells or set(protected) != expected_protected:
        raise RuntimeError("promotion champion does not cover the full AI-era matrix")
    for name, metrics in {**cells, **protected}.items():
        _validate_metric_payload(metrics, label=name)
    if _fingerprint(champion) != REQUIRED_CHAMPION_SHA256:
        raise RuntimeError("promotion champion differs from the compiled reviewed evidence")

    provenance = spec["provenance"]
    if not isinstance(provenance, Mapping) or set(provenance) != {"data", "reference"}:
        raise RuntimeError("promotion provenance is malformed")
    data = provenance["data"]
    if not isinstance(data, Mapping) or set(data) != {
        "snapshot_id",
        "files_verified",
        "manifest_sha256",
        "checksums_sha256",
    }:
        raise RuntimeError("promotion data provenance is malformed")
    if not isinstance(data["snapshot_id"], str) or not data["snapshot_id"]:
        raise RuntimeError("promotion data snapshot is missing")
    if isinstance(data["files_verified"], bool) or not isinstance(data["files_verified"], int):
        raise RuntimeError("promotion data file count is malformed")
    for field in ("manifest_sha256", "checksums_sha256"):
        if not isinstance(data[field], str) or not _SHA256.fullmatch(data[field]):
            raise RuntimeError(f"promotion data provenance is malformed: {field}")
    reference = provenance["reference"]
    if not isinstance(reference, Mapping) or set(reference) != {"repository", "commit", "purpose"}:
        raise RuntimeError("promotion reference provenance is malformed")
    if reference["repository"] != REPOSITORY:
        raise RuntimeError("promotion reference repository is malformed")
    if not isinstance(reference["commit"], str) or not _COMMIT.fullmatch(reference["commit"]):
        raise RuntimeError("promotion reference commit is not immutable")
    if not isinstance(reference["purpose"], str) or not reference["purpose"]:
        raise RuntimeError("promotion reference purpose is missing")

    validation_hash = spec["validation_fingerprint"]
    if not isinstance(validation_hash, str) or not _SHA256.fullmatch(validation_hash):
        raise RuntimeError("promotion validation_fingerprint must be SHA-256")
    if validation_hash != _validation_fingerprint(spec):
        raise RuntimeError("promotion validation fingerprint is stale")
    if validation_hash != REQUIRED_BASELINE_SHA256:
        raise RuntimeError("promotion differs from the compiled reviewed baseline")


def _load_spec(path: str | Path) -> tuple[bytes, dict[str, Any]]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"promotion baseline is missing or not a regular file: {path}")
    try:
        raw = source.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"promotion baseline is missing or corrupt: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("promotion baseline must be a JSON object")
    _validate_spec(payload)
    return raw, payload


def _source_fingerprint_from_entries(entries: list[tuple[str, bytes]]) -> str:
    if not entries:
        raise RuntimeError("cannot fingerprint promotion production source")
    digest = hashlib.sha256()
    for relative, content in entries:
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(4, "big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _production_paths(root: Path) -> list[Path]:
    paths = [
        root / "pyproject.toml",
        root / "requirements.txt",
        root / "uv.lock",
        root / REFERENCE_REGISTRY_PATH,
        root / CONFIG_PARAMETER_GOVERNANCE_PATH,
        *((root / "uquant").rglob("*.py")),
    ]
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def _production_source_fingerprint(root: Path) -> str:
    paths = _production_paths(root)
    if any(not path.is_file() for path in paths):
        raise RuntimeError("cannot fingerprint promotion production source")
    return _source_fingerprint_from_entries(
        [(path.relative_to(root).as_posix(), path.read_bytes()) for path in paths]
    )


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for promotion provenance")
    return executable


def _git_stdout(root: Path, arguments: list[str], *, label: str) -> str:
    try:
        result = subprocess.run(
            [_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return result.stdout


def _production_source_fingerprint_at_commit(root: Path, commit: str) -> str:
    listing = _git_stdout(
        root,
        [
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            REFERENCE_REGISTRY_PATH,
            CONFIG_PARAMETER_GOVERNANCE_PATH,
            "uquant",
        ],
        label="cannot inspect promotion production commit",
    )
    paths = sorted(
        path
        for path in listing.splitlines()
        if path
        in {
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            REFERENCE_REGISTRY_PATH,
            CONFIG_PARAMETER_GOVERNANCE_PATH,
        }
        or (path.startswith("uquant/") and path.endswith(".py"))
    )
    entries = [
        (
            path,
            _git_stdout(
                root,
                ["show", f"{commit}:{path}"],
                label="cannot read promotion production commit",
            ).encode("utf-8"),
        )
        for path in paths
    ]
    return _source_fingerprint_from_entries(entries)


def _production_commit(root: Path) -> str:
    status = _git_stdout(
        root,
        [
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "uquant",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            REFERENCE_REGISTRY_PATH,
            CONFIG_PARAMETER_GOVERNANCE_PATH,
        ],
        label="cannot inspect promotion candidate source",
    )
    if status.strip():
        raise RuntimeError("promotion candidate provenance requires committed production source")
    commit = _git_stdout(root, ["rev-parse", "HEAD"], label="cannot resolve promotion HEAD").strip()
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("cannot resolve promotion HEAD")
    return commit


def _runtime_provenance(data_dir: str | Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    commit = _production_commit(root)
    source_sha256 = _production_source_fingerprint(root)
    if source_sha256 != _production_source_fingerprint_at_commit(root, commit):
        raise RuntimeError("promotion source does not match its committed HEAD")
    return {
        "data": verify_data_manifest(data_dir),
        "production": {
            "repository": REPOSITORY,
            "commit": commit,
            "source_sha256": source_sha256,
        },
        "environment": runtime_environment_provenance(root),
        "effective_config_sha256": config_fingerprint(),
    }


def _artifact_binding(runtime: Mapping[str, Any], *, generated_at: str) -> dict[str, str]:
    """Flatten every mandatory release identity into explicit artifact fields."""

    production = cast(Mapping[str, Any], runtime["production"])
    data = cast(Mapping[str, Any], runtime["data"])
    environment = cast(Mapping[str, Any], runtime["environment"])
    return {
        "production_commit": str(production["commit"]),
        "production_source_sha256": str(production["source_sha256"]),
        "effective_config_sha256": str(runtime["effective_config_sha256"]),
        "data_snapshot_id": str(data["snapshot_id"]),
        "data_manifest_sha256": str(data["manifest_sha256"]),
        "python_full_version": str(environment["python_full_version"]),
        "numpy_version": str(environment["numpy_version"]),
        "pandas_version": str(environment["pandas_version"]),
        "uv_version": str(environment["uv_version"]),
        "uv_lock_sha256": str(environment["uv_lock_sha256"]),
        "generated_at": generated_at,
    }


@contextmanager
def _immutable_validation_inputs(
    *,
    baseline_path: Path,
    baseline_sha256: str,
    data_dir: str | Path,
    runtime_before: Mapping[str, Any],
) -> Iterator[None]:
    try:
        yield
    finally:
        try:
            current_baseline = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            runtime_after = _runtime_provenance(data_dir)
        except Exception as exc:
            raise RuntimeError("promotion source, config, baseline, or data changed during replay") from exc
        if current_baseline != baseline_sha256 or runtime_after != runtime_before:
            raise RuntimeError("promotion source, config, baseline, or data changed during replay")


def _acute_return(result: Mapping[str, Any], *, start: str, end: str) -> float:
    raw_curve = result.get("equity_curve")
    if not isinstance(raw_curve, list):
        raise RuntimeError("promotion equity curve must be a list")
    curve: dict[str, float] = {}
    for item in raw_curve:
        if not isinstance(item, Mapping) or not {"date", "equity"} <= set(item):
            raise RuntimeError("promotion equity curve contains an invalid point")
        point_date = str(item["date"])
        equity = _finite_number(item["equity"], label=f"equity_curve.{point_date}")
        if equity <= 0 or point_date in curve:
            raise RuntimeError("promotion equity curve contains an invalid point")
        curve[point_date] = equity
    if start not in curve or end not in curve:
        raise RuntimeError("promotion acute interval is absent from the equity curve")
    return curve[end] / curve[start] - 1.0


def _compact(result: Mapping[str, Any], *, acute: tuple[str, str] | None) -> dict[str, Any]:
    metrics = {
        name: _finite_number(result.get(name), label=f"result.{name}")
        for name in (
            "final_wealth",
            "cagr",
            "max_drawdown",
            "sharpe",
            "calmar",
            "annual_turnover",
            "gross_turnover",
        )
    }
    orders = result.get("account_orders")
    if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
        raise RuntimeError("promotion replay returned invalid account_orders")
    if (
        metrics["final_wealth"] <= 0
        or not 0 <= metrics["max_drawdown"] <= 1
        or metrics["annual_turnover"] < 0
        or metrics["gross_turnover"] < 0
    ):
        raise RuntimeError("promotion replay returned invalid performance metrics")
    return {
        **metrics,
        "account_orders": orders,
        "acute_return": (
            _acute_return(result, start=acute[0], end=acute[1]) if acute is not None else None
        ),
    }


def _hard_violations(*, name: str, metrics: Mapping[str, Any], gate: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    comparisons = (
        ("final_wealth", "min_final_wealth", False),
        ("max_drawdown", "max_drawdown", True),
        ("account_orders", "max_account_orders", True),
        ("annual_turnover", "max_annual_turnover", True),
        ("acute_return", "min_acute_return", False),
    )
    for metric_name, gate_name, maximum in comparisons:
        if gate_name not in gate or gate[gate_name] is None:
            continue
        observed = metrics[metric_name]
        limit = gate[gate_name]
        breached = observed is None or (observed > limit if maximum else observed < limit)
        if breached:
            direction = "above" if maximum else "below"
            failures.append(f"{name}: {metric_name} {direction} {float(limit):.6f}")
    return failures


def _protected_gate(interval: str, pool: str) -> Mapping[str, Any]:
    groups = AI_ERA_POLICY["protected"][interval]["groups"]
    for gate in groups.values():
        if pool in gate["pools"]:
            return cast(Mapping[str, Any], gate)
    raise RuntimeError(f"promotion protected policy omits pool: {pool}/{interval}")


def _champion_violations(
    *, name: str, metrics: Mapping[str, Any], champion: Mapping[str, Any]
) -> list[str]:
    if not champion:
        raise RuntimeError(f"promotion champion evidence is missing: {name}")
    tolerance = AI_ERA_POLICY["champion_tolerance"]
    failures: list[str] = []
    if metrics["final_wealth"] < champion["final_wealth"] * tolerance["wealth_floor_ratio"]:
        failures.append(f"{name}: final_wealth regressed from production champion")
    if metrics["max_drawdown"] > champion["max_drawdown"] + tolerance["drawdown_tolerance"]:
        failures.append(f"{name}: max_drawdown regressed from production champion")
    if metrics["account_orders"] > champion["account_orders"] + tolerance["order_tolerance"]:
        failures.append(f"{name}: account_orders regressed from production champion")
    if metrics["annual_turnover"] > champion["annual_turnover"] + tolerance["turnover_tolerance"]:
        failures.append(f"{name}: annual_turnover regressed from production champion")
    if (
        champion["acute_return"] is not None
        and metrics["acute_return"] is not None
        and metrics["acute_return"]
        < champion["acute_return"] - tolerance["acute_return_tolerance"]
    ):
        failures.append(f"{name}: acute_return regressed from production champion")
    return failures


def run_promotion(
    *,
    data_dir: str | Path,
    baseline: str | Path = Path("benchmarks") / "promotion_baseline.json",
    profile: str = "full",
) -> dict[str, Any]:
    """Run all six windows and protected intervals; no partial profile exists."""

    if profile != "full":
        raise RuntimeError("AI-era promotion supports only the blocking full profile")
    baseline_path = Path(baseline)
    baseline_bytes, spec = _load_spec(baseline_path)
    baseline_sha256 = hashlib.sha256(baseline_bytes).hexdigest()
    runtime = _runtime_provenance(data_dir)
    if runtime["data"] != spec["provenance"]["data"]:
        raise RuntimeError("promotion baseline data provenance does not match this replay")
    if float(DEFAULT_CONFIG.initial_cash) != float(EXECUTION_CONTRACT["initial_cash"]):
        raise RuntimeError("promotion execution cash does not match production config")

    cells: dict[str, dict[str, Any]] = {}
    protected: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    champion = spec["champion"]
    with _immutable_validation_inputs(
        baseline_path=baseline_path,
        baseline_sha256=baseline_sha256,
        data_dir=data_dir,
        runtime_before=runtime,
    ):
        engine = ProductionEngine(data_dir)
        for pool, symbols in spec["pools"].items():
            for window, (start, end) in AI_ERA_WINDOWS.items():
                name = f"{pool}/{window}"
                raw = engine.backtest(symbols=tuple(symbols), start=start, end=end)
                if raw.get("effective_config_sha256") != runtime["effective_config_sha256"]:
                    raise RuntimeError(f"promotion effective config drifted during replay: {name}")
                metrics = _compact(raw, acute=AI_ERA_ACUTE_WINDOWS[window])
                cells[name] = metrics
                failures.extend(
                    _hard_violations(
                        name=name,
                        metrics=metrics,
                        gate=AI_ERA_POLICY["official"][window],
                    )
                )
                failures.extend(
                    _champion_violations(
                        name=name,
                        metrics=metrics,
                        champion=champion["cells"].get(name, {}),
                    )
                )
            for interval, bounds in PROTECTED_INTERVALS.items():
                name = f"{pool}/{interval}"
                raw = engine.backtest(
                    symbols=tuple(symbols),
                    start=bounds["start"],
                    end=bounds["end"],
                )
                if raw.get("effective_config_sha256") != runtime["effective_config_sha256"]:
                    raise RuntimeError(f"promotion effective config drifted during replay: {name}")
                metrics = _compact(raw, acute=None)
                protected[name] = metrics
                failures.extend(
                    _hard_violations(
                        name=name,
                        metrics=metrics,
                        gate=_protected_gate(interval, pool),
                    )
                )
                failures.extend(
                    _champion_violations(
                        name=name,
                        metrics=metrics,
                        champion=champion["protected"].get(name, {}),
                    )
                )

    all_metrics = [*cells.values(), *protected.values()]
    generated_at = datetime.now(UTC).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "profile": "full",
        "passed": not failures,
        "failures": failures,
        "cells": cells,
        "protected": protected,
        "summary": {
            "official_cells": len(cells),
            "protected_cells": len(protected),
            "median_final_wealth": median(item["final_wealth"] for item in all_metrics),
            "worst_max_drawdown": max(item["max_drawdown"] for item in all_metrics),
            "total_account_orders": sum(item["account_orders"] for item in all_metrics),
        },
        "provenance": {
            "candidate": runtime,
            "binding": _artifact_binding(runtime, generated_at=generated_at),
            "baseline_sha256": baseline_sha256,
            "validation_fingerprint": spec["validation_fingerprint"],
            "champion_commit": champion["production_commit"],
            "generated_at": generated_at,
        },
    }
