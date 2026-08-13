"""Reproducible multi-regime strategy-promotion matrix."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil

# Security: git is invoked only with fixed, non-shell argument vectors below.
import subprocess  # nosec B404
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from ..config import SystemConfig
from ..engine import ProductionEngine
from .manifest import verify_data_manifest

_BASELINE_SCHEMA_VERSION = 3
_POLICY_SCHEMA_VERSION = 2
_REPOSITORY = "ychenracing/uquant"
_REVIEWED_REFERENCE_COMMIT = "ea4fb1cef59256f76ef9f810440c87ef53108aa2"
_REVIEWED_REFERENCE_PATH = "benchmarks/promotion_baseline.json"
_REVIEWED_DATA_CONTRACT: dict[str, Any] = {
    "snapshot_id": "20260809T094222Z-causal-tech-index-rebase",
    "files_verified": 36,
    "manifest_sha256": "343009138d22f8d4a20768f706207fe4d4bcd03581b0c5945c5485ecbd28788d",
    "checksums_sha256": "ba460d65f791f238d8a4a16ac62e2225c1832caa6f4da5003166a894edf80e29",
}
_REVIEWED_INITIAL_CASH = 2_000_000.0
_REVIEWED_EXECUTION_SEMANTICS = (
    "ProductionEngine daily-close decision and next-session open execution"
)
_AGGREGATE_POLICY_CEILINGS = {
    "continuous_median_max_drawdown": 0.28,
    "continuous_worst_max_drawdown": 0.35,
    "choppy_2024_max_drawdown": 0.18,
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_EXECUTION_CONTRACT: dict[str, Any] = {
    "engine": "uquant.engine.ProductionEngine",
    "decision": "daily_close_t",
    "execution": "next_tradable_open",
    "intraday_exit": False,
    "prelisting": "invisible_until_first_observable_row",
}
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "validation_fingerprint",
    "provenance",
    "policy",
    "pools",
    "scenarios",
    "profiles",
    "references",
}
_PROVENANCE_FIELDS = {"data", "dataset", "execution", "reference"}
_POLICY_NUMERIC_FIELDS = {
    "wealth_floor_ratio",
    "drawdown_tolerance",
    "absolute_max_drawdown",
    "order_tolerance",
    "order_ceiling_ratio",
    "turnover_ceiling_ratio",
    "turnover_tolerance",
    "continuous_median_max_drawdown",
    "continuous_worst_max_drawdown",
    "choppy_2024_max_drawdown",
}
_POLICY_FIELDS = _POLICY_NUMERIC_FIELDS | {"schema_version"}
_REFERENCE_FIELDS = {
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "annual_turnover",
}


@dataclass(frozen=True, slots=True)
class Scenario:
    """One point-in-time replay window plus optional sub-period objective."""

    start: str
    end: str
    urgent_start: str = ""
    urgent_end: str = ""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"promotion baseline contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise RuntimeError(f"promotion baseline contains a non-standard number: {value}")


def _exact_fields(value: Any, expected: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"promotion {label} must be an object")
    observed = set(value)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"promotion {label} is missing fields: {missing}")
    if unexpected:
        raise RuntimeError(f"promotion {label} has unexpected fields: {unexpected}")
    return value


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _dataset_fingerprint(spec: Mapping[str, Any]) -> str:
    return _fingerprint(
        {
            "pools": spec["pools"],
            "scenarios": spec["scenarios"],
            "profiles": spec["profiles"],
        }
    )


def _observations_fingerprint(references: Mapping[str, Any]) -> str:
    observations = {
        name: {
            "final_wealth": reference["final_wealth"],
            "max_drawdown": reference["max_drawdown"],
            "account_orders": reference["account_orders"],
            "annual_turnover": reference["annual_turnover"],
        }
        for name, reference in sorted(references.items())
    }
    return _fingerprint(observations)


def _validation_fingerprint(spec: Mapping[str, Any]) -> str:
    return _fingerprint({name: spec[name] for name in sorted(_TOP_LEVEL_FIELDS - {"validation_fingerprint"})})


def _load_spec(path: str | Path) -> tuple[bytes, dict[str, Any]]:
    """Load and fully validate one immutable promotion specification."""
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
    missing = sorted(_TOP_LEVEL_FIELDS - payload.keys())
    unexpected = sorted(payload.keys() - _TOP_LEVEL_FIELDS)
    if missing:
        raise RuntimeError(f"promotion baseline is missing sections: {missing}")
    if unexpected:
        raise RuntimeError(f"promotion baseline has unexpected sections: {unexpected}")
    if payload["schema_version"] != _BASELINE_SCHEMA_VERSION:
        raise RuntimeError("unsupported promotion baseline schema")
    for name in ("provenance", "policy", "pools", "scenarios", "profiles", "references"):
        if not isinstance(payload[name], dict):
            raise RuntimeError(f"promotion baseline section must be an object: {name}")
    _validate_spec(payload)
    payload["provenance"] = _validated_provenance(payload)
    validation_fingerprint = payload["validation_fingerprint"]
    if not isinstance(validation_fingerprint, str) or not _SHA256.fullmatch(validation_fingerprint):
        raise RuntimeError("promotion validation_fingerprint must be SHA-256")
    if validation_fingerprint != _validation_fingerprint(payload):
        raise RuntimeError("promotion validation fingerprint is stale")
    return raw, payload


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"promotion value must be numeric: {label}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeError(f"promotion value must be finite: {label}")
    return numeric


def _positive_integer(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"promotion {label} must be a positive integer")
    return int(value)


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RuntimeError(f"promotion {label} must be SHA-256")
    return value


def _nonempty_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"promotion {label} must be a non-empty string")
    return value


def _validated_provenance(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate every immutable input and reviewed-reference identifier."""
    root = _exact_fields(spec["provenance"], _PROVENANCE_FIELDS, label="provenance")
    data = _exact_fields(
        root["data"],
        {"snapshot_id", "files_verified", "manifest_sha256", "checksums_sha256"},
        label="provenance.data",
    )
    normalized_data = {
        "snapshot_id": _nonempty_text(data["snapshot_id"], label="provenance.data.snapshot_id"),
        "files_verified": _positive_integer(data["files_verified"], label="provenance.data.files_verified"),
        "manifest_sha256": _sha256(data["manifest_sha256"], label="provenance.data.manifest_sha256"),
        "checksums_sha256": _sha256(data["checksums_sha256"], label="provenance.data.checksums_sha256"),
    }

    dataset = _exact_fields(
        root["dataset"],
        {
            "matrix_sha256",
            "pool_count",
            "scenario_count",
            "profile_count",
            "reference_cell_count",
        },
        label="provenance.dataset",
    )
    normalized_dataset = {
        "matrix_sha256": _sha256(dataset["matrix_sha256"], label="provenance.dataset.matrix_sha256"),
        "pool_count": _positive_integer(dataset["pool_count"], label="provenance.dataset.pool_count"),
        "scenario_count": _positive_integer(
            dataset["scenario_count"], label="provenance.dataset.scenario_count"
        ),
        "profile_count": _positive_integer(
            dataset["profile_count"], label="provenance.dataset.profile_count"
        ),
        "reference_cell_count": _positive_integer(
            dataset["reference_cell_count"], label="provenance.dataset.reference_cell_count"
        ),
    }
    expected_dataset = {
        "matrix_sha256": _dataset_fingerprint(spec),
        "pool_count": len(spec["pools"]),
        "scenario_count": len(spec["scenarios"]),
        "profile_count": len(spec["profiles"]),
        "reference_cell_count": len(spec["references"]),
    }
    if normalized_dataset != expected_dataset:
        raise RuntimeError("promotion provenance dataset does not match the frozen matrix")

    execution = _exact_fields(
        root["execution"],
        set(_EXECUTION_CONTRACT) | {"initial_cash"},
        label="provenance.execution",
    )
    for name, expected in _EXECUTION_CONTRACT.items():
        if execution[name] != expected:
            raise RuntimeError(f"promotion execution contract mismatch: {name}")
    initial_cash = _finite_number(execution["initial_cash"], label="provenance.execution.initial_cash")
    if initial_cash <= 0:
        raise RuntimeError("promotion execution initial_cash must be positive")
    normalized_execution = {**_EXECUTION_CONTRACT, "initial_cash": initial_cash}

    reference = _exact_fields(
        root["reference"],
        {
            "repository",
            "reference_path",
            "reference_commit",
            "source_sha256",
            "observations_sha256",
        },
        label="provenance.reference",
    )
    repository = _nonempty_text(reference["repository"], label="provenance.reference.repository")
    reference_path = _nonempty_text(reference["reference_path"], label="provenance.reference.reference_path")
    path = Path(reference_path)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != reference_path:
        raise RuntimeError("promotion reference_path must be a safe repository-relative path")
    reference_commit = _nonempty_text(
        reference["reference_commit"], label="provenance.reference.reference_commit"
    )
    if not _COMMIT.fullmatch(reference_commit):
        raise RuntimeError("promotion reference_commit must be immutable")
    normalized_reference = {
        "repository": repository,
        "reference_path": reference_path,
        "reference_commit": reference_commit,
        "source_sha256": _sha256(reference["source_sha256"], label="provenance.reference.source_sha256"),
        "observations_sha256": _sha256(
            reference["observations_sha256"],
            label="provenance.reference.observations_sha256",
        ),
    }
    if normalized_reference["observations_sha256"] != _observations_fingerprint(spec["references"]):
        raise RuntimeError("promotion reviewed observations fingerprint is stale")

    return {
        "data": normalized_data,
        "dataset": normalized_dataset,
        "execution": normalized_execution,
        "reference": normalized_reference,
    }


def _validate_spec(spec: dict[str, Any]) -> None:
    """Reject ambiguous or stale promotion contracts before any replay starts."""
    policy = spec["policy"]
    missing_policy = sorted(_POLICY_FIELDS - policy.keys())
    unexpected_policy = sorted(policy.keys() - _POLICY_FIELDS)
    if missing_policy:
        raise RuntimeError(f"promotion policy is missing fields: {missing_policy}")
    if unexpected_policy:
        raise RuntimeError(f"promotion policy has unexpected fields: {unexpected_policy}")
    if policy["schema_version"] != _POLICY_SCHEMA_VERSION:
        raise RuntimeError("unsupported promotion policy schema")
    numeric_policy = {
        name: _finite_number(policy[name], label=f"policy.{name}") for name in _POLICY_NUMERIC_FIELDS
    }
    if not 0 < numeric_policy["wealth_floor_ratio"] <= 1:
        raise RuntimeError("promotion wealth_floor_ratio must be in (0, 1]")
    if not 0 <= numeric_policy["absolute_max_drawdown"] <= 1:
        raise RuntimeError("promotion absolute_max_drawdown must be in [0, 1]")
    if numeric_policy["drawdown_tolerance"] < 0:
        raise RuntimeError("promotion drawdown_tolerance cannot be negative")
    if (
        numeric_policy["order_tolerance"] < 0
        or int(numeric_policy["order_tolerance"]) != numeric_policy["order_tolerance"]
    ):
        raise RuntimeError("promotion order_tolerance must be a nonnegative integer")
    if numeric_policy["order_ceiling_ratio"] < 1:
        raise RuntimeError("promotion order_ceiling_ratio cannot be below one")
    if numeric_policy["turnover_ceiling_ratio"] < 1:
        raise RuntimeError("promotion turnover_ceiling_ratio cannot be below one")
    if numeric_policy["turnover_tolerance"] < 0:
        raise RuntimeError("promotion turnover_tolerance cannot be negative")
    for name in (
        "continuous_median_max_drawdown",
        "continuous_worst_max_drawdown",
        "choppy_2024_max_drawdown",
    ):
        if not 0 <= numeric_policy[name] <= 1:
            raise RuntimeError(f"promotion {name} must be in [0, 1]")
    if numeric_policy["continuous_median_max_drawdown"] > numeric_policy["continuous_worst_max_drawdown"]:
        raise RuntimeError("promotion continuous median ceiling cannot exceed its worst ceiling")

    pools = spec["pools"]
    for pool_name, symbols in pools.items():
        if not isinstance(pool_name, str) or not isinstance(symbols, list) or not symbols:
            raise RuntimeError("promotion pools must be named non-empty lists")
        if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise RuntimeError(f"promotion pool has an invalid symbol: {pool_name}")
        if len(symbols) != len(set(symbols)):
            raise RuntimeError(f"promotion pool must contain unique symbols: {pool_name}")

    scenarios: dict[str, Scenario] = {}
    for scenario_name, values in spec["scenarios"].items():
        if not isinstance(scenario_name, str) or not isinstance(values, dict):
            raise RuntimeError("promotion scenarios must be named objects")
        try:
            scenario = Scenario(**values)
            start = date.fromisoformat(scenario.start)
            end = date.fromisoformat(scenario.end)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"promotion scenario is invalid: {scenario_name}") from exc
        if start > end:
            raise RuntimeError(f"promotion scenario has reversed dates: {scenario_name}")
        if bool(scenario.urgent_start) != bool(scenario.urgent_end):
            raise RuntimeError(f"promotion scenario has an incomplete urgent interval: {scenario_name}")
        if scenario.urgent_start:
            try:
                urgent_start = date.fromisoformat(scenario.urgent_start)
                urgent_end = date.fromisoformat(scenario.urgent_end)
            except ValueError as exc:
                raise RuntimeError(
                    f"promotion scenario has an invalid urgent interval: {scenario_name}"
                ) from exc
            if not start <= urgent_start <= urgent_end <= end:
                raise RuntimeError(f"promotion urgent interval is outside its scenario: {scenario_name}")
        scenarios[scenario_name] = scenario

    referenced_cells: set[str] = set()
    for profile_name, cells in spec["profiles"].items():
        if not isinstance(cells, list) or not cells:
            raise RuntimeError(f"promotion profile must be a non-empty list: {profile_name}")
        profile_cells: set[str] = set()
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {"pool", "scenario"}:
                raise RuntimeError(f"promotion profile cell is invalid: {profile_name}")
            pool_name = cell["pool"]
            scenario_name = cell["scenario"]
            name = f"{pool_name}/{scenario_name}"
            if pool_name not in pools or scenario_name not in scenarios:
                raise RuntimeError(f"promotion cell references an unknown input: {name}")
            if name in profile_cells:
                raise RuntimeError(f"promotion profile repeats a cell: {name}")
            profile_cells.add(name)
        referenced_cells.update(profile_cells)

    references = spec["references"]
    unknown_references: list[str] = []
    for name, reference in references.items():
        if not isinstance(reference, dict):
            raise RuntimeError(f"promotion reference must be an object: {name}")
        parts = name.split("/", maxsplit=1)
        if len(parts) != 2 or parts[0] not in pools or parts[1] not in scenarios:
            unknown_references.append(name)
            continue
        expected_reference = set(_REFERENCE_FIELDS)
        if "urgent_return_floor" in reference:
            expected_reference.add("urgent_return_floor")
        missing_reference = sorted(_REFERENCE_FIELDS - reference.keys())
        unexpected_reference = sorted(reference.keys() - expected_reference)
        if missing_reference:
            raise RuntimeError(f"promotion reference is missing metrics: {name} {missing_reference}")
        if unexpected_reference:
            raise RuntimeError(f"promotion reference has unexpected metrics: {name} {unexpected_reference}")
        final_wealth = _finite_number(reference["final_wealth"], label=f"references.{name}.final_wealth")
        max_drawdown = _finite_number(reference["max_drawdown"], label=f"references.{name}.max_drawdown")
        account_orders = _finite_number(
            reference["account_orders"], label=f"references.{name}.account_orders"
        )
        annual_turnover = _finite_number(
            reference["annual_turnover"], label=f"references.{name}.annual_turnover"
        )
        if final_wealth <= 0 or not 0 <= max_drawdown <= 1:
            raise RuntimeError(f"promotion reference has invalid performance: {name}")
        if account_orders < 0 or int(account_orders) != account_orders:
            raise RuntimeError(f"promotion reference has invalid order count: {name}")
        if annual_turnover < 0:
            raise RuntimeError(f"promotion reference has negative turnover: {name}")
        if "urgent_return_floor" in reference:
            urgent_floor = _finite_number(
                reference["urgent_return_floor"],
                label=f"references.{name}.urgent_return_floor",
            )
            if not -1 < urgent_floor < 1:
                raise RuntimeError(f"promotion urgent floor is invalid: {name}")
    if unknown_references:
        raise RuntimeError(f"promotion references contain unknown cells: {sorted(unknown_references)}")
    missing_references = sorted(referenced_cells - references.keys())
    if missing_references:
        raise RuntimeError(f"promotion profiles have no frozen references: {missing_references}")
    unused_references = sorted(references.keys() - referenced_cells)
    if unused_references:
        raise RuntimeError(f"promotion references are not selected by a profile: {unused_references}")


def _urgent_return(result: dict[str, Any], scenario: Scenario) -> float | None:
    """Calculate the return over a scenario's optional urgent interval."""
    if not scenario.urgent_start or not scenario.urgent_end:
        return None
    raw_curve = result.get("equity_curve")
    if not isinstance(raw_curve, list):
        raise RuntimeError("promotion equity curve must be a list")
    curve: dict[str, float] = {}
    for item in raw_curve:
        if not isinstance(item, dict) or not {"date", "equity"} <= set(item):
            raise RuntimeError("promotion equity curve contains an invalid point")
        point_date = str(item["date"])
        equity = _finite_number(item["equity"], label=f"equity_curve.{point_date}")
        if equity <= 0 or point_date in curve:
            raise RuntimeError("promotion equity curve contains an invalid point")
        curve[point_date] = equity
    start = curve.get(scenario.urgent_start)
    end = curve.get(scenario.urgent_end)
    if start is None or end is None:
        raise RuntimeError("promotion urgent interval is absent from the equity curve")
    return end / start - 1.0


def _compact(result: dict[str, Any], scenario: Scenario) -> dict[str, float | int | None]:
    final_wealth = _finite_number(result.get("final_wealth"), label="result.final_wealth")
    max_drawdown = _finite_number(result.get("max_drawdown"), label="result.max_drawdown")
    annual_turnover = _finite_number(result.get("annual_turnover"), label="result.annual_turnover")
    account_orders = result.get("account_orders")
    if (
        final_wealth <= 0
        or not 0 <= max_drawdown <= 1
        or annual_turnover < 0
        or isinstance(account_orders, bool)
        or not isinstance(account_orders, int)
        or account_orders < 0
    ):
        raise RuntimeError("promotion replay returned invalid performance metrics")
    return {
        "final_wealth": final_wealth,
        "max_drawdown": max_drawdown,
        "account_orders": account_orders,
        "annual_turnover": annual_turnover,
        "urgent_return": _urgent_return(result, scenario),
    }


def _violations(
    *,
    name: str,
    result: dict[str, float | int | None],
    reference: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    """Return every policy breach for one replay cell."""
    failures: list[str] = []
    wealth_floor = float(reference["final_wealth"]) * float(policy["wealth_floor_ratio"])
    if float(result["final_wealth"] or 0.0) < wealth_floor:
        failures.append(f"{name}: final_wealth below {wealth_floor:.6f}")
    drawdown_ceiling = min(
        float(policy["absolute_max_drawdown"]),
        float(reference["max_drawdown"]) + float(policy["drawdown_tolerance"]),
    )
    if float(result["max_drawdown"] or 0.0) > drawdown_ceiling:
        failures.append(f"{name}: max_drawdown above {drawdown_ceiling:.6f}")
    reference_orders = int(reference["account_orders"])
    order_ceiling = max(
        reference_orders + int(policy["order_tolerance"]),
        math.ceil(reference_orders * float(policy["order_ceiling_ratio"])),
    )
    if int(result["account_orders"] or 0) > order_ceiling:
        failures.append(f"{name}: account_orders above {order_ceiling}")
    turnover_ceiling = max(
        float(reference["annual_turnover"]) * float(policy["turnover_ceiling_ratio"]),
        float(reference["annual_turnover"]) + float(policy["turnover_tolerance"]),
    )
    if float(result["annual_turnover"] or 0.0) > turnover_ceiling:
        failures.append(f"{name}: annual_turnover above {turnover_ceiling:.6f}")
    urgent_floor = reference.get("urgent_return_floor")
    if urgent_floor is not None:
        observed = result.get("urgent_return")
        if observed is None or float(observed) < float(urgent_floor):
            failures.append(f"{name}: urgent_return below {float(urgent_floor):.6f}")
    return failures


def _source_fingerprint_from_entries(entries: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    if not entries:
        raise RuntimeError("cannot fingerprint promotion production source")
    for relative, content in entries:
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _production_source_fingerprint(root: Path) -> str:
    paths = [root / "pyproject.toml", *sorted((root / "uquant").rglob("*.py"))]
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
        completed = subprocess.run(
            [_git_executable(), "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(label) from exc
    return completed.stdout


def _production_source_fingerprint_at_commit(root: Path, commit: str) -> str:
    listing = _git_stdout(
        root,
        ["ls-tree", "-r", "--name-only", commit, "--", "pyproject.toml", "uquant"],
        label="cannot inspect promotion reviewed source commit",
    )
    paths = sorted(
        path
        for path in listing.splitlines()
        if path == "pyproject.toml" or (path.startswith("uquant/") and path.endswith(".py"))
    )
    entries = [
        (
            path,
            _git_stdout(
                root,
                ["show", f"{commit}:{path}"],
                label="cannot read promotion reviewed source commit",
            ).encode(),
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
        ],
        label="cannot inspect promotion candidate source",
    )
    if status.strip():
        raise RuntimeError("promotion candidate provenance requires committed source")
    commit = _git_stdout(
        root,
        ["rev-parse", "HEAD"],
        label="cannot resolve promotion candidate commit",
    ).strip()
    if not _COMMIT.fullmatch(commit):
        raise RuntimeError("cannot resolve promotion candidate commit")
    return commit


def _verify_reviewed_reference(root: Path, spec: Mapping[str, Any]) -> None:
    """Anchor the complete gate contract to the immutable reviewed ancestor."""
    provenance = spec["provenance"]
    if not isinstance(provenance, Mapping):
        raise RuntimeError("promotion reviewed provenance is malformed")
    reference = provenance["reference"]
    if not isinstance(reference, Mapping):
        raise RuntimeError("promotion reviewed reference is malformed")
    expected_identity = {
        "repository": _REPOSITORY,
        "reference_path": _REVIEWED_REFERENCE_PATH,
        "reference_commit": _REVIEWED_REFERENCE_COMMIT,
    }
    observed_identity = {name: reference.get(name) for name in expected_identity}
    if observed_identity != expected_identity:
        raise RuntimeError("promotion reviewed reference identity is not the approved ancestor")

    commit = str(reference["reference_commit"])
    try:
        ancestor = subprocess.run(
            [
                _git_executable(),
                "-C",
                str(root),
                "merge-base",
                "--is-ancestor",
                commit,
                "HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
        )  # nosec B603
    except OSError as exc:
        raise RuntimeError("cannot inspect promotion reviewed reference commit") from exc
    if ancestor.returncode != 0:
        raise RuntimeError("promotion reviewed reference commit is not an ancestor of HEAD")
    observed_source = _production_source_fingerprint_at_commit(root, commit)
    if observed_source != reference["source_sha256"]:
        raise RuntimeError("promotion reviewed reference source fingerprint is stale")
    historical = _git_stdout(
        root,
        ["show", f"{commit}:{reference['reference_path']}"],
        label="cannot read promotion reviewed reference",
    )
    try:
        payload = json.loads(
            historical,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
        if not isinstance(payload, Mapping):
            raise TypeError
        historical_metadata = payload["metadata"]
        historical_policy = payload["policy"]
        historical_pools = payload["pools"]
        historical_scenarios = payload["scenarios"]
        historical_profiles = payload["profiles"]
        historical_references = payload["references"]
        if not all(
            isinstance(section, Mapping)
            for section in (
                historical_metadata,
                historical_policy,
                historical_pools,
                historical_scenarios,
                historical_profiles,
                historical_references,
            )
        ):
            raise TypeError
        observations_sha256 = _observations_fingerprint(historical_references)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("promotion reviewed reference is malformed") from exc
    if observations_sha256 != reference["observations_sha256"]:
        raise RuntimeError("promotion reviewed observations do not match their cited commit")

    data = provenance.get("data")
    execution = provenance.get("execution")
    if data != _REVIEWED_DATA_CONTRACT:
        raise RuntimeError("promotion data contract differs from the approved snapshot")
    if not isinstance(execution, Mapping) or execution.get("initial_cash") != _REVIEWED_INITIAL_CASH:
        raise RuntimeError("promotion execution cash differs from the approved contract")
    if historical_metadata.get("data_snapshot_id") != _REVIEWED_DATA_CONTRACT["snapshot_id"]:
        raise RuntimeError("promotion approved snapshot does not match its cited ancestor")
    if historical_metadata.get("execution_semantics") != _REVIEWED_EXECUTION_SEMANTICS:
        raise RuntimeError("promotion approved execution semantics are malformed")

    for name, historical_section in (
        ("pools", historical_pools),
        ("scenarios", historical_scenarios),
        ("profiles", historical_profiles),
    ):
        if spec[name] != historical_section:
            raise RuntimeError(f"promotion {name} differ from the approved matrix")

    policy = spec["policy"]
    if not isinstance(policy, Mapping):
        raise RuntimeError("promotion policy is malformed")
    for name in ("wealth_floor_ratio",):
        if float(policy[name]) < float(historical_policy[name]):
            raise RuntimeError(f"promotion policy is weaker than the approved contract: {name}")
    for name in (
        "drawdown_tolerance",
        "absolute_max_drawdown",
        "order_tolerance",
        "order_ceiling_ratio",
        "turnover_ceiling_ratio",
        "turnover_tolerance",
    ):
        if float(policy[name]) > float(historical_policy[name]):
            raise RuntimeError(f"promotion policy is weaker than the approved contract: {name}")
    for name, ceiling in _AGGREGATE_POLICY_CEILINGS.items():
        if float(policy[name]) > ceiling:
            raise RuntimeError(f"promotion aggregate policy is weaker than the approved ceiling: {name}")

    references = spec["references"]
    if not isinstance(references, Mapping):
        raise RuntimeError("promotion references are malformed")
    for name, historical_reference in historical_references.items():
        if not isinstance(historical_reference, Mapping):
            raise RuntimeError("promotion reviewed reference is malformed")
        historical_floor = historical_reference.get("urgent_return_floor")
        if historical_floor is None:
            continue
        current_reference = references.get(name)
        if not isinstance(current_reference, Mapping):
            raise RuntimeError(f"promotion reviewed reference is missing: {name}")
        current_floor = current_reference.get("urgent_return_floor")
        if current_floor is None or float(current_floor) < float(historical_floor):
            raise RuntimeError(f"promotion urgent floor is weaker than the approved contract: {name}")


def _runtime_provenance(data_dir: str | Path) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[2]
    commit = _production_commit(repository_root)
    source_sha256 = _production_source_fingerprint(repository_root)
    if source_sha256 != _production_source_fingerprint_at_commit(repository_root, commit):
        raise RuntimeError("promotion candidate source does not match its committed source")
    return {
        "data": verify_data_manifest(data_dir),
        "production": {
            "repository": _REPOSITORY,
            "commit": commit,
            "source_sha256": source_sha256,
        },
    }


@contextmanager
def _immutable_validation_inputs(
    *,
    baseline_path: Path,
    baseline_sha256: str,
    data_dir: str | Path,
    runtime_before: Mapping[str, Any],
) -> Iterator[None]:
    """Reject baseline, candidate-source, or frozen-data mutation during replay."""
    try:
        yield
    finally:
        try:
            current_baseline = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
            runtime_after = _runtime_provenance(data_dir)
        except Exception as exc:
            raise RuntimeError("promotion source or data changed during validation") from exc
        if current_baseline != baseline_sha256:
            raise RuntimeError("promotion baseline changed during validation")
        if runtime_after != runtime_before:
            raise RuntimeError("promotion source or data changed during validation")


def run_promotion(
    *,
    data_dir: str | Path,
    baseline: str | Path,
    profile: str = "quick",
) -> dict[str, Any]:
    """Run a frozen matrix and return a machine-readable pass/fail report."""
    baseline_path = Path(baseline)
    baseline_bytes, spec = _load_spec(baseline_path)
    baseline_sha256 = hashlib.sha256(baseline_bytes).hexdigest()
    profiles = spec.get("profiles", {})
    if profile not in profiles:
        raise RuntimeError(f"unknown promotion profile: {profile}")
    selected = profiles[profile]
    pools = spec["pools"]
    scenarios = {name: Scenario(**values) for name, values in spec["scenarios"].items()}
    references = spec["references"]
    policy = spec["policy"]
    runtime_before = _runtime_provenance(data_dir)
    if runtime_before["data"] != spec["provenance"]["data"]:
        raise RuntimeError("promotion baseline data provenance does not match this replay")
    configured_cash = float(SystemConfig().initial_cash)
    if configured_cash != spec["provenance"]["execution"]["initial_cash"]:
        raise RuntimeError("promotion execution initial_cash does not match production config")
    repository_root = Path(__file__).resolve().parents[2]
    _verify_reviewed_reference(repository_root, spec)

    with _immutable_validation_inputs(
        baseline_path=baseline_path,
        baseline_sha256=baseline_sha256,
        data_dir=data_dir,
        runtime_before=runtime_before,
    ):
        engine = ProductionEngine(data_dir)
        cells: dict[str, dict[str, float | int | None]] = {}
        failures: list[str] = []
        for cell in selected:
            pool_name = str(cell["pool"])
            scenario_name = str(cell["scenario"])
            name = f"{pool_name}/{scenario_name}"
            scenario = scenarios[scenario_name]
            raw = engine.backtest(
                symbols=tuple(pools[pool_name]),
                start=scenario.start,
                end=scenario.end,
            )
            result = _compact(raw, scenario)
            cells[name] = result
            failures.extend(
                _violations(
                    name=name,
                    result=result,
                    reference=references[name],
                    policy=policy,
                )
            )

        continuous_drawdowns = [
            float(cells[f"{cell['pool']}/{cell['scenario']}"]["max_drawdown"] or 0.0)
            for cell in selected
            if cell["scenario"] == "continuous"
        ]
        choppy_drawdowns = [
            float(cells[f"{cell['pool']}/{cell['scenario']}"]["max_drawdown"] or 0.0)
            for cell in selected
            if cell["scenario"] == "choppy_2024"
        ]
        continuous_median = median(continuous_drawdowns) if continuous_drawdowns else None
        continuous_worst = max(continuous_drawdowns) if continuous_drawdowns else None
        choppy_worst = max(choppy_drawdowns) if choppy_drawdowns else None
        continuous_median_ceiling = float(policy["continuous_median_max_drawdown"])
        continuous_worst_ceiling = float(policy["continuous_worst_max_drawdown"])
        choppy_ceiling = float(policy["choppy_2024_max_drawdown"])
        continuous_violations: list[str] = []
        choppy_violations: list[str] = []
        if continuous_median is None or continuous_worst is None:
            continuous_violations.append(f"{profile}: continuous aggregate gate has no selected cells")
        else:
            if continuous_median > continuous_median_ceiling:
                continuous_violations.append(
                    f"aggregate/continuous: median_max_drawdown above {continuous_median_ceiling:.6f}"
                )
            if continuous_worst > continuous_worst_ceiling:
                continuous_violations.append(
                    f"aggregate/continuous: worst_max_drawdown above {continuous_worst_ceiling:.6f}"
                )
        if choppy_worst is None:
            choppy_violations.append(f"{profile}: choppy_2024 aggregate gate has no selected cells")
        elif choppy_worst > choppy_ceiling:
            choppy_violations.append(f"aggregate/choppy_2024: worst_max_drawdown above {choppy_ceiling:.6f}")
        failures.extend(continuous_violations)
        failures.extend(choppy_violations)

        wealth_values = [float(item["final_wealth"] or 0.0) for item in cells.values()]
        drawdowns = [float(item["max_drawdown"] or 0.0) for item in cells.values()]
        return {
            "schema_version": _BASELINE_SCHEMA_VERSION,
            "profile": profile,
            "baseline_sha256": baseline_sha256,
            "validation_fingerprint": spec["validation_fingerprint"],
            "provenance": {
                "baseline": spec["provenance"],
                "candidate": runtime_before,
            },
            "passed": not failures,
            "failures": failures,
            "summary": {
                "cells": len(cells),
                "median_final_wealth": median(wealth_values),
                "median_max_drawdown": median(drawdowns),
                "total_account_orders": sum(int(item["account_orders"] or 0) for item in cells.values()),
            },
            "aggregate_gates": {
                "continuous": {
                    "passed": bool(
                        continuous_median is not None
                        and continuous_worst is not None
                        and continuous_median <= continuous_median_ceiling
                        and continuous_worst <= continuous_worst_ceiling
                    ),
                    "cells": len(continuous_drawdowns),
                    "median_max_drawdown": continuous_median,
                    "worst_max_drawdown": continuous_worst,
                    "median_ceiling": continuous_median_ceiling,
                    "worst_ceiling": continuous_worst_ceiling,
                    "violations": continuous_violations,
                },
                "choppy_2024": {
                    "passed": bool(choppy_worst is not None and choppy_worst <= choppy_ceiling),
                    "cells": len(choppy_drawdowns),
                    "worst_max_drawdown": choppy_worst,
                    "ceiling": choppy_ceiling,
                    "violations": choppy_violations,
                },
            },
            "results": cells,
        }
