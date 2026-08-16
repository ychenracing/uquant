"""Executable fail-closed readback for blocking CI artifact conclusions."""

from __future__ import annotations

import argparse
import copy
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from ..config import config_fingerprint
from .ai_era import AI_ERA_WINDOWS, runtime_environment_provenance
from .generalization import GeneralizationObservation
from .generalization_contract import (
    RANDOM_BASE_SEED,
    RANDOM_POOL_SIZES,
    RANDOM_SEED_INDEXES,
)
from .generalization_matrix import (
    _aggregate,
    _hash_json,
    _head_and_source,
    _industry_sha256,
)
from .generalization_reference import (
    GeneralizationBaseline,
    GeneralizationPolicy,
    _candidate_contract_sha256,
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
    load_generalization_policy,
)
from .manifest import verify_data_manifest
from .promotion import _artifact_binding, _runtime_provenance
from .universe import load_ai_universe

_ROOT = Path(__file__).resolve().parents[2]
_OFFICIAL_WINDOWS = tuple(AI_ERA_WINDOWS)
_PHASE1_PROVENANCE_FIELDS = {
    "candidate",
    "binding",
    "baseline_sha256",
    "validation_fingerprint",
    "champion_commit",
    "generated_at",
}


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON key: {key}")
        payload[key] = value
    return payload


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def run_phase1_validation(
    *,
    artifact: str | Path,
    report_output: str | Path,
    upstream_result: str,
    data_dir: str | Path = "data/frozen",
    expected_candidate: Mapping[str, Any] | None = None,
    checkout_head: str | None = None,
) -> dict[str, Any]:
    """Validate full Phase 1 provenance and always write a diagnostic report."""
    failures: list[str] = []
    payload: dict[str, Any] | None = None
    try:
        if expected_candidate is None:
            try:
                expected = _runtime_provenance(data_dir)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"cannot construct authoritative Phase 1 provenance: {exc}"
                ) from exc
        else:
            expected = copy.deepcopy(dict(expected_candidate))
        expected_production = expected.get("production")
        expected_head = (
            checkout_head
            if checkout_head is not None
            else (
                str(expected_production.get("commit"))
                if isinstance(expected_production, Mapping)
                else ""
            )
        )
        payload = _load_json_object(Path(artifact), label="Phase 1 artifact")
        if payload.get("passed") is not True:
            failures.append("Phase 1 gate did not pass")
        provenance = payload.get("provenance")
        if not isinstance(provenance, Mapping):
            failures.append("Phase 1 provenance is missing or malformed")
        elif set(provenance) != _PHASE1_PROVENANCE_FIELDS:
            failures.append("Phase 1 provenance fields are incomplete or unexpected")
        else:
            candidate = provenance.get("candidate")
            binding = provenance.get("binding")
            generated_at = provenance.get("generated_at")
            if candidate != expected:
                failures.append("Phase 1 candidate provenance differs from exact checkout inputs")
            if not isinstance(generated_at, str) or not generated_at:
                failures.append("Phase 1 generated_at provenance is malformed")
            elif binding != _artifact_binding(expected, generated_at=generated_at):
                failures.append("Phase 1 flattened binding differs from full candidate provenance")
            if not isinstance(candidate, Mapping):
                failures.append("Phase 1 candidate provenance is malformed")
            else:
                production = candidate.get("production")
                if (
                    not isinstance(production, Mapping)
                    or production.get("commit") != expected_head
                ):
                    failures.append("Phase 1 candidate does not bind exact checkout HEAD")
            if not isinstance(binding, Mapping) or binding.get("production_commit") != expected_head:
                failures.append("Phase 1 artifact binding does not bind exact checkout HEAD")
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        failures.append(str(exc))
    if upstream_result != "success":
        failures.append(f"upstream Phase 1 result was {upstream_result}")
    report: dict[str, Any] = {"passed": not failures, "failures": failures}
    _write_json(Path(report_output), report)
    return report


def _scenario_payload(cells: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "window": {
                "name": cell["window"],
                "start": cell["start"],
                "end": cell["end"],
            },
            "name": cell["scenario"],
            "family": cell["family"],
            "symbols": cell["symbols"],
            "reference_symbols": cell["reference_symbols"],
            "removed_symbols": cell["removed_symbols"],
            "status": cell["status"],
            "industry": cell["industry"],
            "pool_size": cell["pool_size"],
            "seed_index": cell["seed_index"],
            "derived_seed": cell["derived_seed"],
            "evidence": cell["evidence"],
        }
        for cell in cells
    ]


def _shard_fingerprints(window: str, cells: list[Mapping[str, Any]]) -> dict[str, str]:
    if not cells:
        raise ValueError(f"{window}: shard has no cells")
    first = cells[0]
    return {
        "window_fingerprint": _hash_json(
            [{"name": window, "start": first["start"], "end": first["end"]}]
        ),
        "scenario_fingerprint": _hash_json(_scenario_payload(cells)),
        "evidence_fingerprint": _hash_json(
            [{"window": window, "evidence": first["evidence"]}]
        ),
    }


def _valid_aggregate(cells: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    metrics: list[Mapping[str, float | int]] = []
    observations: list[GeneralizationObservation] = []
    errors = 0
    expected = 0
    for cell in cells:
        if cell.get("economic") is not True:
            continue
        expected += 1
        if cell.get("replay_error") is not None:
            errors += 1
            continue
        raw_metrics = cell.get("metrics")
        if not isinstance(raw_metrics, Mapping):
            raise ValueError("economic cell is missing aggregate metrics")
        compact = cast(Mapping[str, float | int], raw_metrics)
        orders = compact.get("account_orders")
        if isinstance(orders, bool) or not isinstance(orders, int):
            raise ValueError("economic cell account_orders is not an integer")
        metrics.append(compact)
        observations.append(
            GeneralizationObservation(
                name=f'{cell["window"]}/{cell["scenario"]}',
                family=str(cell["family"]),
                final_wealth=float(compact["final_wealth"]),
                max_drawdown=float(compact["max_drawdown"]),
                account_orders=orders,
                symbol_pnl=(),
            )
        )
    return _aggregate(
        metrics,
        observations,
        expected_cells=expected,
        replay_error_cells=errors,
    )


def _current_common_provenance(data_dir: str | Path) -> dict[str, Any]:
    checkout_head, checkout_source = _head_and_source(_ROOT)
    universe = load_ai_universe()
    return {
        "head": checkout_head,
        "source_sha256": checkout_source,
        "effective_config_sha256": config_fingerprint(),
        "data": verify_data_manifest(data_dir),
        "runtime": runtime_environment_provenance(_ROOT),
        "universe_sha256": universe.sha256,
        "industry_sha256": _industry_sha256(universe),
        "lookback_sessions": 120,
    }


def _policy_scenario_contract() -> dict[str, Any]:
    payload = _load_json_object(
        _ROOT / "benchmarks" / "ai_era_generalization_policy.json",
        label="Generalization policy",
    )
    contract = payload.get("scenario_contract")
    if not isinstance(contract, dict):
        raise ValueError("Generalization policy scenario contract is malformed")
    return contract


def _merge_artifacts(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    common_provenance: Mapping[str, Any],
    scenario_contract: Mapping[str, Any],
) -> dict[str, Any]:
    first = copy.deepcopy(dict(artifacts[_OFFICIAL_WINDOWS[0]]))
    merged_cells = [
        copy.deepcopy(dict(cell))
        for window in _OFFICIAL_WINDOWS
        for cell in cast(list[Mapping[str, Any]], artifacts[window]["cells"])
    ]
    first["passed"] = all(artifact.get("passed") is True for artifact in artifacts.values())
    first["failures"] = [
        str(failure)
        for artifact in artifacts.values()
        for failure in cast(list[Any], artifact.get("failures", []))
    ]
    first["provenance"] = {
        **copy.deepcopy(dict(common_provenance)),
        "window_fingerprint": scenario_contract["window_fingerprint"],
        "scenario_fingerprint": scenario_contract["scenario_fingerprint"],
        "evidence_fingerprint": scenario_contract["evidence_fingerprint"],
    }
    first["aggregates"] = {
        "all": _valid_aggregate(merged_cells),
        "by_window": {
            window: _valid_aggregate(
                [cell for cell in merged_cells if cell.get("window") == window]
            )
            for window in _OFFICIAL_WINDOWS
        },
    }
    first["cells"] = merged_cells
    return first


def run_generalization_validation(
    *,
    shard_root: str | Path,
    artifact_prefix: str,
    report_output: str | Path,
    merged_output: str | Path,
    upstream_result: str,
    data_dir: str | Path | None = "data/frozen",
    expected_common_provenance: Mapping[str, Any] | None = None,
    expected_schema_version: int = 2,
    baseline: GeneralizationBaseline | None = None,
    policy: GeneralizationPolicy | None = None,
) -> dict[str, Any]:
    """Validate one exact attempt's six shards and always write diagnostics."""
    failures: list[str] = []
    structural_failure = False
    artifacts: dict[str, dict[str, Any]] = {}
    summaries: dict[str, Any] = {}
    policy_report: dict[str, Any] | None = None

    def fail(message: str, *, structural: bool = True) -> None:
        nonlocal structural_failure
        failures.append(message)
        structural_failure = structural_failure or structural

    try:
        if _OFFICIAL_WINDOWS != (
            "h1_2023",
            "h2_2023",
            "h1_2024",
            "h2_2024",
            "bull_crash_2025_2026",
            "continuous_ai_era",
        ):
            fail("official window set or order changed")
        if (
            RANDOM_BASE_SEED != 20260810
            or RANDOM_SEED_INDEXES != (0, 1, 2, 3, 4)
            or RANDOM_POOL_SIZES != (5, 9, 15, 20)
        ):
            fail("fixed random-pool contract changed")
        trusted_baseline = load_generalization_baseline() if baseline is None else baseline
        trusted_policy = load_generalization_policy() if policy is None else policy
        expected_common = (
            _current_common_provenance(cast(str | Path, data_dir))
            if expected_common_provenance is None
            else copy.deepcopy(dict(expected_common_provenance))
        )
        scenario_contract = _policy_scenario_contract()
        root = Path(shard_root)
        expected_directories = {
            f"{artifact_prefix}-{window}" for window in _OFFICIAL_WINDOWS
        }
        observed_directories = {path.name for path in root.iterdir()} if root.is_dir() else set()
        if observed_directories != expected_directories:
            fail(
                "downloaded shard artifact set differs: "
                f"expected={sorted(expected_directories)} "
                f"observed={sorted(observed_directories)}"
            )
        for window in _OFFICIAL_WINDOWS:
            directory = root / f"{artifact_prefix}-{window}"
            paths = sorted(directory.rglob("*.json")) if directory.is_dir() else []
            if len(paths) != 1:
                fail(f"{window}: expected exactly one JSON shard, found {len(paths)}")
                continue
            try:
                candidate = _load_json_object(paths[0], label=f"{window} shard")
            except ValueError as exc:
                fail(str(exc))
                continue
            cells_value = candidate.get("cells")
            provenance = candidate.get("provenance")
            if not isinstance(cells_value, list) or any(
                not isinstance(cell, Mapping) for cell in cells_value
            ):
                fail(f"{window}: shard cells are malformed")
                continue
            cells = cast(list[Mapping[str, Any]], cells_value)
            if len(cells) != 39:
                fail(f"{window}: shard does not contain exactly 39 records")
            if candidate.get("schema_version") != expected_schema_version:
                fail(f"{window}: shard schema differs from the required version")
            if candidate.get("gate") != "ai-era-generalization":
                fail(f"{window}: shard gate identity differs")
            if not isinstance(provenance, Mapping):
                fail(f"{window}: shard provenance is malformed")
                continue
            observed_windows = {cell.get("window") for cell in cells}
            if observed_windows != {window}:
                fail(f"{window}: shard contains mixed windows: {sorted(map(str, observed_windows))}")
            identifiers = [f'{cell.get("window")}/{cell.get("scenario")}' for cell in cells]
            duplicates = sorted(
                {identifier for identifier in identifiers if identifiers.count(identifier) > 1}
            )
            if duplicates:
                fail(f"{window}: duplicate cell records: {duplicates}")
            expected_ids = {
                identifier
                for identifier in trusted_baseline.cells
                if identifier.startswith(f"{window}/")
            }
            if set(identifiers) != expected_ids:
                fail(f"{window}: exact 39-cell identity set differs")
            for cell, identifier in zip(cells, identifiers, strict=True):
                reference = trusted_baseline.cells.get(identifier)
                if reference is None:
                    continue
                try:
                    contract_sha256 = _candidate_contract_sha256(cell)
                except (TypeError, ValueError) as exc:
                    fail(f"{identifier}: contract is malformed: {exc}")
                    continue
                evidence = cell.get("evidence")
                evidence_sha256 = (
                    evidence.get("sha256") if isinstance(evidence, Mapping) else None
                )
                if contract_sha256 != reference.contract_sha256:
                    fail(f"{identifier}: contract differs from frozen evidence")
                if evidence_sha256 != reference.evidence_sha256:
                    fail(f"{identifier}: evidence identity differs from frozen evidence")
            try:
                expected_provenance = {
                    **copy.deepcopy(expected_common),
                    **_shard_fingerprints(window, cells),
                }
            except (KeyError, TypeError, ValueError) as exc:
                fail(f"{window}: cannot recompute shard provenance: {exc}")
            else:
                if dict(provenance) != expected_provenance:
                    fail(f"{window}: provenance differs from exact HEAD and inputs")
            if candidate.get("passed") is not True:
                fail(f"{window}: shard gate failed", structural=False)
            summaries[window] = {
                "artifact": paths[0].as_posix(),
                "passed": candidate.get("passed"),
                "failures": candidate.get("failures"),
                "records": len(cells),
                "head": provenance.get("head"),
            }
            artifacts[window] = candidate
        if len(artifacts) == len(_OFFICIAL_WINDOWS) and not structural_failure:
            merged = _merge_artifacts(
                artifacts,
                common_provenance=expected_common,
                scenario_contract=scenario_contract,
            )
            if len(cast(list[Any], merged["cells"])) != 234:
                fail("merged matrix does not contain exactly 234 records")
            _write_json(Path(merged_output), merged)
            policy_report = evaluate_generalization_policy_artifact(
                merged,
                baseline=trusted_baseline,
                policy=trusted_policy,
                data_dir=data_dir,
            )
            if policy_report.get("passed") is not True:
                fail("merged generalization policy/evidence validation failed", structural=False)
                failures.extend(str(item) for item in policy_report.get("failures", []))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        fail(f"Generalization aggregator raised {type(exc).__name__}: {exc}")
    if upstream_result != "success":
        fail(f"generalization shard job result was {upstream_result}", structural=False)
    report: dict[str, Any] = {
        "passed": not failures,
        "shard_job_result": upstream_result,
        "official_windows": list(_OFFICIAL_WINDOWS),
        "shards": summaries,
        "policy": policy_report,
        "failures": failures,
    }
    _write_json(Path(report_output), report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m uquant.validation.ci_artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    phase1 = subparsers.add_parser("phase1")
    phase1.add_argument("--artifact", required=True)
    phase1.add_argument("--report-output", required=True)
    phase1.add_argument("--upstream-result", required=True)
    phase1.add_argument("--data-dir", default="data/frozen")
    generalization = subparsers.add_parser("generalization")
    generalization.add_argument("--shard-root", required=True)
    generalization.add_argument("--artifact-prefix", required=True)
    generalization.add_argument("--report-output", required=True)
    generalization.add_argument("--merged-output", required=True)
    generalization.add_argument("--upstream-result", required=True)
    generalization.add_argument("--data-dir", default="data/frozen")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one CI artifact validator and return a process-compatible status."""
    args = _parser().parse_args(argv)
    if args.command == "phase1":
        report = run_phase1_validation(
            artifact=args.artifact,
            report_output=args.report_output,
            upstream_result=args.upstream_result,
            data_dir=args.data_dir,
        )
    else:
        report = run_generalization_validation(
            shard_root=args.shard_root,
            artifact_prefix=args.artifact_prefix,
            report_output=args.report_output,
            merged_output=args.merged_output,
            upstream_result=args.upstream_result,
            data_dir=args.data_dir,
        )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
