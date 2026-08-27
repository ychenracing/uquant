"""Assemble and validate compact strategic evidence without changing economics."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from itertools import combinations
from pathlib import Path
from typing import Any

from uquant.atomic_io import atomic_write_text

from .absolute_policy import AbsolutePolicyResult, evaluate_absolute_policy
from .contract import load_contract
from .forced_owner import (
    ForcedOwnerCell,
    ForcedOwnerControl,
    forced_owner_cell_from_compact,
    required_forced_owner_cell_ids,
    validate_required_coverage,
    verify_forced_owner_trace_shard,
)
from .models import canonical_sha256, require_sha256
from .provenance import (
    canonical_json_bytes,
    read_gzip_shard,
    seal_payload,
    validate_provenance,
    verify_sealed_payload,
)
from .reachability import ReachabilityCellResult, ReachabilityCellSpec
from .witness_ablation import (
    ECONOMIC,
    FULL_REMOVAL,
    AblationCell,
    AblationSpec,
    FirstDivergences,
    ablation_cell_from_compact,
    enumerate_initial_specs,
    is_decisive,
    minimal_decisive_witness_sets,
    necessary_triple_support,
    rank_critical_symbols,
    select_bounded_search,
)
from .witness_ablation_runner import (
    BalancedIndustryUniverse,
    _divergences_from_compact,
    build_task4_scenario,
    validate_initial_coverage,
    verify_full_route_linkage,
)

_ARTIFACT_NAMES = (
    "README.md",
    "analysis.md",
    "compact_summary.json",
    "evidence_manifest.json",
)
_TASK5_LOGICAL_PATH = (
    "artifacts/strategic_evidence_closure/external/checkpoint5_state_reachability_84.jsonl.gz"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_sealed(path: Path, *, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    return verify_sealed_payload(decoded, label=label)


def _repository_relative(root: Path, path: Path, *, label: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} lies outside repository") from exc
    return relative.as_posix()


def _source_identity(root: Path, path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "logical_path": _repository_relative(root, path, label="source artifact"),
        "byte_size": path.stat().st_size,
        "bytes_sha256": _sha256_file(path),
        "payload_sha256": payload["payload_sha256"],
        "schema_version": payload.get("schema_version"),
    }


def _external_identity(
    *,
    logical_path: str,
    expected: Mapping[str, Any],
    physical_path: Path | None,
) -> dict[str, Any]:
    expected_sha = expected.get("bytes_sha256") or expected.get("output_bytes_sha256")
    expected_size = expected.get("byte_size") or expected.get("output_byte_size")
    expected_rows = expected.get("row_count") or expected.get("output_row_count")
    if (
        isinstance(expected_sha, bool)
        or not isinstance(expected_sha, str)
        or len(expected_sha) != 64
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or expected_rows <= 0
    ):
        raise ValueError(f"external shard expected identity is malformed: {logical_path}")
    available = physical_path is not None and physical_path.is_file()
    actual_sha = _sha256_file(physical_path) if available and physical_path is not None else None
    actual_size = physical_path.stat().st_size if available and physical_path is not None else None
    return {
        "logical_path": logical_path,
        "sealed_expected_identity": {
            "byte_size": expected_size,
            "bytes_sha256": expected_sha,
            "row_count": expected_rows,
        },
        "available_for_current_readback": available,
        "current_readback_verified": False,
        "actual_byte_size": actual_size,
        "actual_bytes_sha256": actual_sha,
    }


def _validate_frozen_provenance(
    contract: Any,
    value: object,
    *,
    label: str,
    exact_contract_identities: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} provenance is malformed")
    provenance = validate_provenance(value)
    if provenance["base_commit"] != contract.base_commit:
        raise ValueError(f"{label} base commit differs")
    identities = contract.raw["identities"]
    fields = [
        "config_sha256",
        "data_manifest_sha256",
        "window_sha256",
        "uv_lock_sha256",
    ]
    if exact_contract_identities:
        fields.extend(("production_source_sha256", "universe_sha256", "industry_mapping_sha256"))
    for field in fields:
        if provenance[field] != identities[field]:
            raise ValueError(f"{label} frozen identity differs: {field}")
    if exact_contract_identities:
        for field in ("python", "numpy", "pandas"):
            if provenance[field] != contract.raw["runtime"][field]:
                raise ValueError(f"{label} frozen runtime differs: {field}")
        uv = str(provenance["uv"])
        if uv != "pinned-by-uv-lock" and contract.raw["runtime"]["uv"] not in uv:
            raise ValueError(f"{label} frozen runtime differs: uv")
    return provenance


def _load_adjacent_manifest(summary_path: Path, name: str, *, label: str) -> dict[str, Any]:
    return _load_sealed(summary_path.parent / name, label=label)


def _validate_summary_manifest_link(
    root: Path,
    *,
    summary_path: Path,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    route: Mapping[str, Any],
    label: str,
) -> None:
    manifest_summary = manifest.get("summary")
    if not isinstance(manifest_summary, Mapping):
        raise ValueError(f"{label} manifest summary linkage is malformed")
    expected_summary = {
        "path": _repository_relative(root, summary_path, label=f"{label} summary"),
        "byte_size": summary_path.stat().st_size,
        "bytes_sha256": _sha256_file(summary_path),
        "payload_sha256": summary["payload_sha256"],
    }
    if manifest_summary != expected_summary or manifest.get("route_shard") != route:
        raise ValueError(f"{label} manifest linkage differs")


def _task3_compact_validation(
    root: Path,
    summary_path: Path,
    summary: Mapping[str, Any],
    contract: Any,
) -> tuple[tuple[ForcedOwnerCell, ...], dict[str, Any], dict[str, Any]]:
    if summary.get("schema_version") != 2 or summary.get("checkpoint") != "TASK3_FORCED_OWNER_FULL_ROUTE":
        raise ValueError("Task 3 compact schema differs")
    if summary.get("contract_payload_sha256") != contract.payload_sha256:
        raise ValueError("Task 3 contract linkage differs")
    raw_controls = summary.get("controls")
    raw_cells = summary.get("cells")
    if not isinstance(raw_controls, list) or not isinstance(raw_cells, list):
        raise ValueError("Task 3 controls or cells are malformed")
    controls: list[ForcedOwnerControl] = []
    for value in raw_controls:
        if not isinstance(value, Mapping) or set(value) != {"control_id", "owner", "owner_role"}:
            raise ValueError("Task 3 control schema differs")
        controls.append(
            ForcedOwnerControl(
                control_id=str(value["control_id"]),
                owner=str(value["owner"]),
                owner_role=str(value["owner_role"]),
            )
        )
    cells = tuple(forced_owner_cell_from_compact(value) for value in raw_cells)
    validate_required_coverage(cells, controls=controls)
    expected_ids = list(required_forced_owner_cell_ids(controls))
    if summary.get("required_cell_ids") != expected_ids:
        raise ValueError("Task 3 required cell identities differ")
    status_counts = dict(sorted(Counter(cell.status for cell in cells).items()))
    if summary.get("status_counts") != status_counts:
        raise ValueError("Task 3 status counts differ")
    provenance = _validate_frozen_provenance(
        contract,
        summary.get("provenance"),
        label="Task 3",
        exact_contract_identities=True,
    )
    reproduction = summary.get("baseline_reproduction")
    if (
        not isinstance(reproduction, Mapping)
        or not isinstance(reproduction.get("equality"), Mapping)
        or set(reproduction["equality"])
        != {"account", "equity", "fills", "metrics", "orders", "route", "targets"}
        or not all(value is True for value in reproduction["equality"].values())
    ):
        raise ValueError("Task 3 baseline reproduction differs")
    route = summary.get("route_shard")
    if not isinstance(route, Mapping) or route.get("cell_count") != 16:
        raise ValueError("Task 3 route identity is malformed")
    manifest = _load_adjacent_manifest(
        summary_path,
        "checkpoint3_forced_owner_manifest.json",
        label="Task 3 compact manifest",
    )
    if manifest.get("schema_version") != 1 or manifest.get("checkpoint") != "TASK3_FORCED_OWNER_FULL_ROUTE":
        raise ValueError("Task 3 manifest schema differs")
    _validate_summary_manifest_link(
        root,
        summary_path=summary_path,
        summary=summary,
        manifest=manifest,
        route=route,
        label="Task 3",
    )
    return cells, provenance, manifest


def _validate_search_cells(cells: tuple[AblationCell, ...]) -> None:
    for cell in cells:
        if cell.spec.scope not in {"CRITICAL_PAIR", "NECESSARY_TRIPLE"}:
            raise ValueError("Task 4 search cell scope differs")
        if cell.spec.axis != "FULL_REMOVAL" or cell.spec.evidence_class != "ECONOMIC":
            raise ValueError("Task 4 search cell classification differs")
        if cell.status not in {"SUCCESS", "REPLAY_ERROR", "INSUFFICIENT_SAMPLE"}:
            raise ValueError("Task 4 search cell status differs")
        if cell.status == "SUCCESS" and (
            cell.metrics is None or cell.final_account_sha256 is None or cell.trace_sha256 is None
        ):
            raise ValueError("Task 4 successful search cell lacks evidence")
        if cell.status != "SUCCESS" and cell.metrics is not None:
            raise ValueError("Task 4 terminal search cell carries metrics")


def _task4_scenario_validation(
    scenario: Mapping[str, Any],
    *,
    contract: Any,
    specs: tuple[Any, ...],
    provenance: Mapping[str, Any],
) -> None:
    raw_balanced = scenario.get("balanced_industry_universe")
    if not isinstance(raw_balanced, Mapping):
        raise ValueError("Task 4 balanced industry scenario is malformed")
    industries = raw_balanced.get("industries")
    if not isinstance(industries, Mapping):
        raise ValueError("Task 4 balanced industry scenario is malformed")
    try:
        balanced = BalancedIndustryUniverse(
            evidence_as_of=str(raw_balanced["evidence_as_of"]),
            per_industry=int(raw_balanced["per_industry"]),
            symbols=tuple(str(value) for value in raw_balanced["symbols"]),
            removed_symbols=tuple(str(value) for value in raw_balanced["removed_symbols"]),
            industries=tuple(sorted((str(symbol), str(industry)) for symbol, industry in industries.items())),
            industry_mapping_sha256=str(raw_balanced["industry_mapping_sha256"]),
            evidence_sha256=str(raw_balanced["evidence_sha256"]),
            symbols_sha256=str(raw_balanced["symbols_sha256"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Task 4 balanced industry scenario is malformed") from exc
    source_manifest = scenario.get("executable_source_manifest")
    if not isinstance(source_manifest, Mapping):
        raise ValueError("Task 4 historical source manifest is malformed")
    expected = build_task4_scenario(
        contract=contract,
        initial_specs=specs,
        balanced=balanced,
        source_manifest=source_manifest,
    )
    if dict(scenario) != expected:
        raise ValueError("Task 4 historical scenario fields differ")
    if provenance["research_source_sha256"] != source_manifest["manifest_sha256"]:
        raise ValueError("Task 4 historical research source linkage differs")
    canonical_baseline = scenario["comparison_baselines"]["CANONICAL_34"]
    if provenance["universe_sha256"] != canonical_baseline["symbols_sha256"]:
        raise ValueError("Task 4 historical universe linkage differs")
    expected_industry = canonical_sha256(
        {"as_of": contract.window["end"], "industries": dict(scenario["industries"])}
    )
    if provenance["industry_mapping_sha256"] != expected_industry:
        raise ValueError("Task 4 historical industry linkage differs")
    for field in ("python", "numpy", "pandas"):
        if provenance[field] != contract.raw["runtime"][field]:
            raise ValueError(f"Task 4 frozen runtime differs: {field}")
    if contract.raw["runtime"]["uv"] not in str(provenance["uv"]):
        raise ValueError("Task 4 frozen runtime differs: uv")


def _task4_claim_validation(
    summary: Mapping[str, Any],
    *,
    specs: tuple[Any, ...],
    search: tuple[AblationCell, ...],
    contract: Any,
) -> None:
    raw_divergences = summary.get("first_divergences")
    if not isinstance(raw_divergences, Mapping):
        raise ValueError("Task 4 divergences are malformed")
    divergences: dict[str, FirstDivergences] = {
        str(cell_id): _divergences_from_compact(value) for cell_id, value in raw_divergences.items()
    }
    scores: dict[str, float] = {}
    for spec in specs:
        if spec.scope != "CANONICAL_LEAVE_ONE_OUT" or spec.axis != FULL_REMOVAL:
            continue
        difference = divergences[spec.cell_id]
        scores[spec.subject] = (
            4.0 * int(difference.comparable and difference.route is not None)
            + 2.0 * int(difference.comparable and difference.state is not None)
            + float(difference.comparable and difference.economic is not None)
        )
    critical = tuple(str(value) for value in contract.raw["matrix"]["critical_symbols"])
    ranked = rank_critical_symbols(scores, preregistered=critical)
    if summary.get("critical_ranking") != list(ranked):
        raise ValueError("Task 4 critical ranking differs")
    pairs, _ = select_bounded_search(ranked, {})

    def search_spec(symbols: tuple[str, ...], *, scope: str) -> AblationSpec:
        removed = tuple(sorted(symbols))
        return AblationSpec(
            scope=scope,
            subject="+".join(removed),
            removed_symbols=removed,
            axis=FULL_REMOVAL,
            evidence_class=ECONOMIC,
        )

    pair_specs = tuple(search_spec(pair, scope="CRITICAL_PAIR") for pair in pairs)
    outcomes = {
        frozenset((spec.subject,)): divergences[spec.cell_id]
        for spec in specs
        if spec.scope == "CANONICAL_LEAVE_ONE_OUT" and spec.axis == FULL_REMOVAL
    }
    outcomes.update((frozenset(spec.removed_symbols), divergences[spec.cell_id]) for spec in pair_specs)
    support = {triple: necessary_triple_support(triple, outcomes) for triple in combinations(ranked, 3)}
    _, triples = select_bounded_search(ranked, support)
    triple_specs = tuple(search_spec(triple, scope="NECESSARY_TRIPLE") for triple in triples)
    expected_specs = (*pair_specs, *triple_specs)
    if tuple(cell.spec for cell in search) != expected_specs:
        raise ValueError("Task 4 bounded search specifications differ")
    for spec in triple_specs:
        outcomes[frozenset(spec.removed_symbols)] = divergences[spec.cell_id]
    minimal = minimal_decisive_witness_sets(outcomes)
    necessary_ids = [spec.cell_id for spec in triple_specs if is_decisive(divergences[spec.cell_id])]
    if (
        summary.get("critical_pair_cell_ids") != [spec.cell_id for spec in pair_specs]
        or summary.get("supported_triple_cell_ids") != [spec.cell_id for spec in triple_specs]
        or summary.get("necessary_triple_cell_ids") != necessary_ids
        or summary.get("minimal_witness_sets") != [list(values) for values in minimal]
    ):
        raise ValueError("Task 4 derived witness claims differ")
    roles = summary.get("symbol_roles")
    if not isinstance(roles, Mapping) or set(roles) != set(contract.canonical_universe):
        raise ValueError("Task 4 symbol role coverage differs")
    pair_members = {symbol for values in minimal if len(values) == 2 for symbol in values}
    for symbol in contract.canonical_universe:
        values = roles.get(symbol)
        if not isinstance(values, list) or len(values) != len(set(values)):
            raise ValueError("Task 4 symbol roles are malformed")
        difference = outcomes[frozenset((symbol,))]
        expected_qualification = difference.comparable and difference.route is not None
        expected_ghost = (
            expected_qualification
            and difference.state is None
            and difference.economic is None
            and "owner" not in values
        )
        if (
            ("qualification witness" in values) is not expected_qualification
            or ("ghost witness" in values) is not expected_ghost
            or ("decisive-pair member" in values) is not (symbol in pair_members)
        ):
            raise ValueError("Task 4 derived symbol roles differ")


def _task4_compact_validation(
    root: Path,
    summary_path: Path,
    summary: Mapping[str, Any],
    contract: Any,
) -> tuple[tuple[AblationCell, ...], dict[str, Any], dict[str, Any]]:
    if (
        summary.get("schema_version") != 1
        or summary.get("checkpoint") != "TASK4_WITNESS_ABLATION"
        or summary.get("completion_status") != "FINAL"
        or summary.get("contract_payload_sha256") != contract.payload_sha256
    ):
        raise ValueError("Task 4 compact schema or contract linkage differs")
    provenance = _validate_frozen_provenance(
        contract,
        summary.get("provenance"),
        label="Task 4",
    )
    scenario = summary.get("scenario")
    if not isinstance(scenario, Mapping) or canonical_sha256(dict(scenario)) != provenance["scenario_sha256"]:
        raise ValueError("Task 4 scenario linkage differs")
    raw_initial = summary.get("initial_cells")
    raw_search = summary.get("search_cells")
    if not isinstance(raw_initial, list) or not isinstance(raw_search, list):
        raise ValueError("Task 4 compact cells are malformed")
    specs = enumerate_initial_specs(contract)
    _task4_scenario_validation(
        scenario,
        contract=contract,
        specs=specs,
        provenance=provenance,
    )
    initial = tuple(ablation_cell_from_compact(value) for value in raw_initial)
    validate_initial_coverage(initial, specs=specs)
    if summary.get("required_initial_cell_ids") != [spec.cell_id for spec in specs]:
        raise ValueError("Task 4 required initial identities differ")
    expected_statuses = dict(sorted(Counter(cell.status for cell in initial).items()))
    if summary.get("initial_status_counts") != expected_statuses:
        raise ValueError("Task 4 initial status counts differ")
    search = tuple(ablation_cell_from_compact(value) for value in raw_search)
    _validate_search_cells(search)
    pair_ids = summary.get("critical_pair_cell_ids")
    triple_ids = summary.get("supported_triple_cell_ids")
    necessary_ids = summary.get("necessary_triple_cell_ids")
    if (
        not isinstance(pair_ids, list)
        or len(pair_ids) != 28
        or not isinstance(triple_ids, list)
        or not isinstance(necessary_ids, list)
        or not set(necessary_ids) <= set(triple_ids)
        or [cell.cell_id for cell in search] != [*pair_ids, *triple_ids]
    ):
        raise ValueError("Task 4 bounded search coverage differs")
    all_ids = [cell.cell_id for cell in (*initial, *search)]
    divergences = summary.get("first_divergences")
    if not isinstance(divergences, Mapping) or set(divergences) != set(all_ids):
        raise ValueError("Task 4 divergence linkage differs")
    _task4_claim_validation(
        summary,
        specs=specs,
        search=search,
        contract=contract,
    )
    route = summary.get("route_shard")
    if not isinstance(route, Mapping) or route.get("row_count") != sum(
        cell.partial_trace_row_count + cell.diagnostic_projection_row_count + 1
        for cell in (*initial, *search)
    ):
        raise ValueError("Task 4 route identity is malformed")
    manifest = _load_adjacent_manifest(
        summary_path,
        "checkpoint4_witness_ablation_manifest.json",
        label="Task 4 compact manifest",
    )
    if manifest.get("schema_version") != 1 or manifest.get("checkpoint") != "TASK4_WITNESS_ABLATION":
        raise ValueError("Task 4 manifest schema differs")
    _validate_summary_manifest_link(
        root,
        summary_path=summary_path,
        summary=summary,
        manifest=manifest,
        route=route,
        label="Task 4",
    )
    return (*initial, *search), provenance, manifest


def _task5_rows_validation(
    path: Path,
    *,
    summary: Mapping[str, Any],
    contract: Any,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    shard = read_gzip_shard(path)
    provenance = _validate_frozen_provenance(
        contract,
        shard.get("provenance"),
        label="Task 5",
    )
    for field in ("experiment_commit", "research_source_sha256", "scenario_sha256"):
        if summary.get(field) != provenance[field]:
            raise ValueError(f"Task 5 summary provenance differs: {field}")
    rows = tuple(verify_sealed_payload(row, label="Task 5 cell") for row in shard["rows"])
    expected = {
        (state_id, path_id) for state_id in contract.initial_state_ids for path_id in contract.path_ids
    }
    observed = [(str(row.get("state_id", "")), str(row.get("path_id", ""))) for row in rows]
    if len(rows) != 84 or len(set(observed)) != 84 or set(observed) != expected:
        raise ValueError("Task 5 exact cell coverage differs")
    expected_fields = {
        "cell_id",
        "state_id",
        "path_id",
        "state_source",
        "path_source",
        "evidence_class",
        "status",
        "observation_count",
        "input_bindings",
        "analysis",
        "analysis_sha256",
        "error",
        "payload_sha256",
    }
    for row in rows:
        if set(row) != expected_fields or row.get("evidence_class") != "DIAGNOSTIC_ONLY":
            raise ValueError("Task 5 cell schema or evidence class differs")
        if row.get("state_source") != "SYNTHETIC" or row.get("path_source") != "SYNTHETIC":
            raise ValueError("Task 5 cell sources are not synthetic")
        try:
            spec = ReachabilityCellSpec(
                state_id=str(row["state_id"]),
                path_id=str(row["path_id"]),
                cell_id=str(row["cell_id"]),
            )
            analysis = row["analysis"]
            bindings = row["input_bindings"]
            error = row["error"]
            if analysis is not None and not isinstance(analysis, Mapping):
                raise ValueError("Task 5 analysis is malformed")
            if not isinstance(bindings, Mapping):
                raise ValueError("Task 5 input bindings are malformed")
            if error is not None and not isinstance(error, Mapping):
                raise ValueError("Task 5 error is malformed")
            ReachabilityCellResult(
                spec=spec,
                state_source=str(row["state_source"]),
                path_source=str(row["path_source"]),
                status=str(row["status"]),
                observation_count=row["observation_count"],
                input_bindings={str(key): str(value) for key, value in bindings.items()},
                analysis=None if analysis is None else dict(analysis),
                analysis_sha256=row["analysis_sha256"],
                error=None if error is None else {str(key): str(value) for key, value in error.items()},
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Task 5 cell linkage differs") from exc
    statuses = dict(sorted(Counter(str(row["status"]) for row in rows).items()))
    if (
        summary.get("schema_version") != "uquant.strategic-evidence-reachability-summary.v1"
        or summary.get("evidence_class") != "DIAGNOSTIC_ONLY"
        or summary.get("synthetic_historical_return_claims") != "FORBIDDEN"
        or summary.get("cell_count") != 84
        or summary.get("output_row_count") != 84
        or summary.get("statuses") != statuses
        or summary.get("output_byte_size") != path.stat().st_size
        or summary.get("output_bytes_sha256") != _sha256_file(path)
    ):
        raise ValueError("Task 5 summary-to-shard linkage differs")
    return rows, provenance


def _task5_summary_validation(summary: Mapping[str, Any]) -> None:
    expected_fields = {
        "schema_version",
        "evidence_class",
        "experiment_commit",
        "research_source_sha256",
        "scenario_sha256",
        "cell_count",
        "statuses",
        "output_bytes_sha256",
        "output_byte_size",
        "output_row_count",
        "synthetic_historical_return_claims",
        "payload_sha256",
    }
    if (
        set(summary) != expected_fields
        or summary.get("schema_version") != "uquant.strategic-evidence-reachability-summary.v1"
        or summary.get("evidence_class") != "DIAGNOSTIC_ONLY"
        or summary.get("synthetic_historical_return_claims") != "FORBIDDEN"
        or summary.get("cell_count") != 84
        or summary.get("output_row_count") != 84
    ):
        raise ValueError("Task 5 compact summary schema differs")
    require_sha256(summary.get("research_source_sha256"), field="Task 5 research source")
    require_sha256(summary.get("scenario_sha256"), field="Task 5 scenario")
    require_sha256(summary.get("output_bytes_sha256"), field="Task 5 output bytes")
    experiment = summary.get("experiment_commit")
    if (
        not isinstance(experiment, str)
        or len(experiment) != 40
        or any(character not in "0123456789abcdef" for character in experiment)
    ):
        raise ValueError("Task 5 experiment commit is malformed")


def _forced_owner_answer(summary: Mapping[str, Any], *, evidence_valid: bool) -> dict[str, Any]:
    cells = summary.get("cells")
    rows = cells if isinstance(cells, list) else []
    native = [row for row in rows if str(row.get("cell_id", "")).endswith("NATIVE_ELIGIBILITY_DATE")]
    common = [row for row in rows if str(row.get("cell_id", "")).endswith("COMMON_ACTIVATION_DATE")]
    native_counts: dict[str, int] = {}
    common_counts: dict[str, int] = {}
    for bucket, target in ((native, native_counts), (common, common_counts)):
        for row in bucket:
            status = str(row.get("status", "MISSING"))
            target[status] = target.get(status, 0) + 1
    established = (
        evidence_valid
        and len(native) == 8
        and len(common) == 8
        and all(row.get("status") == "SUCCESS" for row in native)
        and all(row.get("status") == "SUCCESS" for row in common)
    )
    return {
        "answer": "ESTABLISHED" if established else "NOT_ESTABLISHED",
        "common_date_status_counts": dict(sorted(common_counts.items())),
        "native_date_status_counts": dict(sorted(native_counts.items())),
        "reason": (
            "Every native-date forced owner completed successfully."
            if established
            else "Native-date portability is incomplete or contains preserved terminal failures."
        ),
    }


def _witness_answer(summary: Mapping[str, Any], *, evidence_valid: bool) -> dict[str, Any]:
    witnesses = summary.get("minimal_witness_sets")
    roles = summary.get("symbol_roles")
    witness_rows = witnesses if isinstance(witnesses, list) else []
    role_map = roles if isinstance(roles, Mapping) else {}
    ghosts = sorted(
        str(symbol)
        for symbol, raw_roles in role_map.items()
        if isinstance(raw_roles, list) and "ghost witness" in raw_roles
    )
    singleton_count = sum(isinstance(item, list) and len(item) == 1 for item in witness_rows)
    return {
        "answer": "SENSITIVE" if evidence_valid and singleton_count else "NOT_DEMONSTRATED",
        "minimal_witness_set_count": len(witness_rows),
        "singleton_minimal_witness_count": singleton_count,
        "ghost_witnesses": ghosts,
        "critical_ranking": summary.get("critical_ranking", []),
    }


def _finding_count(rows: tuple[dict[str, Any], ...], observation_id: str) -> int:
    count = 0
    for row in rows:
        analysis = row.get("analysis")
        findings = analysis.get("findings") if isinstance(analysis, Mapping) else None
        if not isinstance(findings, list):
            continue
        if any(
            isinstance(finding, Mapping)
            and finding.get("observation_id") == observation_id
            and finding.get("observed") is True
            for finding in findings
        ):
            count += 1
    return count


def _reachability_answers(
    rows: tuple[dict[str, Any], ...],
    *,
    evidence_valid: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    crowning_count = 0
    target_metric: Any = None
    for row in rows:
        analysis = row.get("analysis")
        repeated = analysis.get("repeated_crowning") if isinstance(analysis, Mapping) else None
        if isinstance(repeated, Mapping) and repeated.get("satisfied") is True:
            crowning_count += 1
        if row.get("state_id") == "S09" and row.get("path_id") == "P05":
            metrics = analysis.get("metrics") if isinstance(analysis, Mapping) else None
            if isinstance(metrics, Mapping):
                target_metric = metrics.get("witness_missing_recovery_fraction")
    status_success = sum(row.get("status") == "SUCCESS" for row in rows)
    r7_count = _finding_count(rows, "R7")
    state_answer = {
        "answer": "PARTIAL_DIAGNOSTIC_ONLY" if evidence_valid else "NOT_ESTABLISHED",
        "cell_count": len(rows),
        "successful_cells": status_success,
        "R7_observed_cells": r7_count,
        "repeated_crowning_satisfied_cells": crowning_count,
        "evidence_class": "DIAGNOSTIC_ONLY",
        "historical_return_claim": "FORBIDDEN",
    }
    cash_answer = {
        "answer": (
            "CLOSED_FOR_FROZEN_CELL"
            if evidence_valid
            and not isinstance(target_metric, bool)
            and isinstance(target_metric, (int, float))
            and float(target_metric) >= 1.0
            else "NOT_CLOSED"
        ),
        "S09_P05_witness_missing_recovery_fraction": target_metric,
        "threshold": 1.0,
        "evidence_class": "DIAGNOSTIC_ONLY",
    }
    return state_answer, cash_answer


def _analysis_markdown(summary: Mapping[str, Any]) -> str:
    answers = summary["direct_answers"]
    capability = summary["absolute_policy"]["capability_pass"]
    return f"""# Strategic Evidence Closure Analysis

Runner/evidence completion and strategy capability are separate. The assembled evidence
reports runner success as `{str(summary["runner_success"]).lower()}` and the literal
capability decision as `{str(capability).lower()}`.

## Owner portability

**{answers["owner_portability"]["answer"]}**. Common-date controls completed, but native-date
results include preserved failures or absent native eligibility. They cannot be converted into
portable-owner capability by omitting the failed cells.

## Witness sensitivity

**{answers["witness_sensitivity"]["answer"]}**. The result contains
{answers["witness_sensitivity"]["singleton_minimal_witness_count"]} singleton minimal witnesses
and the ghost-witness list is retained in the compact summary.

## State reachability

**{answers["state_reachability"]["answer"]}**. The validated bundle contains
{answers["state_reachability"]["cell_count"]} synthetic `DIAGNOSTIC_ONLY` cells. R7 was observed in
{answers["state_reachability"]["R7_observed_cells"]} cells and repeated crowning satisfied
{answers["state_reachability"]["repeated_crowning_satisfied_cells"]} of
{answers["state_reachability"]["cell_count"]} cells. Synthetic
evidence supports no historical return claim.

## Cash vacancy

**{answers["cash_vacancy"]["answer"]}**. The frozen S09/P05 witness-missing recovery fraction
is `{answers["cash_vacancy"]["S09_P05_witness_missing_recovery_fraction"]}` versus the literal
threshold `1.0`; a successful runner does not make this capability pass.

## Literal policy

Capability is fail-closed. Missing cells, `REPLAY_ERROR`, `INSUFFICIENT_SAMPLE`, null literal
metrics, and the unregistered p10/p90 percentile method remain explicit machine-readable
failures. No threshold, scenario, or economic result was retuned.
"""


def _readme_markdown() -> str:
    return """# Strategic Evidence Closure Artifacts

This directory contains compact, sealed evidence for Tasks 3-6. Large deterministic route
and reachability shards remain external; `evidence_manifest.json` binds their logical paths,
byte sizes, SHA-256 identities, and row counts as the **sealed expected identity**. The separate
availability and verification fields describe assembly-time readback only; a later validator
reports live readback for whichever recovered shards are supplied. Absence is explicit and is
never treated as verification. `compact_summary.json` separates experiment completion from the
literal capability result. `analysis.md` answers the four research questions without converting
synthetic diagnostics into historical-return claims.

Validate tracked evidence and any available external shards with:

```bash
python -m scripts.run_strategic_evidence_closure validate
```
"""


def assemble_evidence_artifacts(
    *,
    root: Path,
    output_dir: Path,
    source_paths: Mapping[str, Path],
    task5_shard: Path | None,
    task3_shard: Path | None = None,
    task4_shard: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Seal compact evidence even when the literal capability result is false."""

    repository = root.resolve()
    required = {"task3", "task4", "task5"}
    if set(source_paths) != required:
        raise ValueError("Task 6 source artifact keys differ")
    task3_path = source_paths["task3"].resolve()
    task4_path = source_paths["task4"].resolve()
    task5_path = source_paths["task5"].resolve()
    task3 = _load_sealed(task3_path, label="Task 3 compact evidence")
    task4 = _load_sealed(task4_path, label="Task 4 compact evidence")
    task5 = _load_sealed(task5_path, label="Task 5 compact evidence")
    contract = load_contract(repository / "benchmarks/strategic_evidence_closure_contract.json")
    task3_cells, task3_provenance, task3_manifest = _task3_compact_validation(
        repository,
        task3_path,
        task3,
        contract,
    )
    task4_cells, task4_provenance, task4_manifest = _task4_compact_validation(
        repository,
        task4_path,
        task4,
        contract,
    )
    _task5_summary_validation(task5)

    readback_errors: dict[str, str | None] = {
        "task3": "NOT_SUPPLIED" if task3_shard is None else None,
        "task4": "NOT_SUPPLIED" if task4_shard is None else None,
        "task5": "NOT_SUPPLIED" if task5_shard is None else None,
    }
    reach_rows: tuple[dict[str, Any], ...] = ()
    if task5_shard is not None:
        try:
            reach_rows, _ = _task5_rows_validation(
                task5_shard,
                summary=task5,
                contract=contract,
            )
        except ValueError as exc:
            readback_errors["task5"] = str(exc)

    external = {
        "task3": _external_identity(
            logical_path=str(
                task3.get("route_shard", {}).get("logical_path")
                or (
                    "artifacts/strategic_evidence_closure/external/"
                    "checkpoint3_forced_owner_full_routes.jsonl.gz"
                )
            ),
            expected=task3.get("route_shard", {}),
            physical_path=task3_shard,
        ),
        "task4": _external_identity(
            logical_path=str(task4.get("route_shard", {}).get("logical_path")),
            expected=task4.get("route_shard", {}),
            physical_path=task4_shard,
        ),
        "task5": _external_identity(
            logical_path=_TASK5_LOGICAL_PATH,
            expected=task5,
            physical_path=task5_shard,
        ),
    }
    if task3_shard is not None:
        try:
            metadata = verify_forced_owner_trace_shard(
                task3_shard,
                expected_cells=task3_cells,
                expected_provenance=task3_provenance,
            ).metadata
            expected_route = task3["route_shard"]
            if any(
                metadata.get(key) != value for key, value in expected_route.items() if key != "logical_path"
            ):
                raise ValueError("Task 3 current route readback differs from sealed identity")
            external["task3"]["current_readback_verified"] = True
        except ValueError as exc:
            readback_errors["task3"] = str(exc)
    if task4_shard is not None:
        try:
            expected_route = task4["route_shard"]
            metadata = verify_full_route_linkage(
                task4_shard,
                expected_cell_ids=[cell.cell_id for cell in task4_cells],
                expected_cells=task4_cells,
                expected_provenance=task4_provenance,
                expected_resume_identity=str(expected_route["resume_identity"]),
            )
            if any(
                metadata.get(key) != value for key, value in expected_route.items() if key != "logical_path"
            ):
                raise ValueError("Task 4 current route readback differs from sealed identity")
            external["task4"]["current_readback_verified"] = True
        except ValueError as exc:
            readback_errors["task4"] = str(exc)
    if task5_shard is not None and readback_errors["task5"] is None:
        external["task5"]["current_readback_verified"] = True
    evidence_integrity = {
        "task3_compact_schema_and_linkage": True,
        "task3_supplied_shard_readback": (
            task3_shard is None or external["task3"]["current_readback_verified"]
        ),
        "task4_compact_schema_and_linkage": True,
        "task4_supplied_shard_readback": (
            task4_shard is None or external["task4"]["current_readback_verified"]
        ),
        "task5_compact_schema": True,
        "task5_shard_readback": external["task5"]["current_readback_verified"],
        "task5_shard_identity": external["task5"]["current_readback_verified"],
        "task5_exact_cell_count": len(reach_rows) == 84,
    }
    policy: AbsolutePolicyResult = evaluate_absolute_policy(
        contract,
        forced_owner=task3,
        witness=task4,
        reachability_rows=reach_rows,
    )
    state_answer, cash_answer = _reachability_answers(
        reach_rows,
        evidence_valid=all(
            evidence_integrity[key]
            for key in (
                "task5_compact_schema",
                "task5_shard_readback",
                "task5_shard_identity",
                "task5_exact_cell_count",
            )
        ),
    )
    runner_success = all(evidence_integrity.values()) and policy.runner_success
    compact = seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-closure-summary.v1",
            "contract_payload_sha256": contract.payload_sha256,
            "runner_success": runner_success,
            "capability_pass": policy.capability_pass,
            "dry_run": dry_run,
            "evidence_integrity": evidence_integrity,
            "readback_errors": readback_errors,
            "direct_answers": {
                "owner_portability": _forced_owner_answer(
                    task3,
                    evidence_valid=evidence_integrity["task3_compact_schema_and_linkage"],
                ),
                "witness_sensitivity": _witness_answer(
                    task4,
                    evidence_valid=evidence_integrity["task4_compact_schema_and_linkage"],
                ),
                "state_reachability": state_answer,
                "cash_vacancy": cash_answer,
            },
            "absolute_policy": policy.compact(),
            "source_payload_sha256": {
                "task3": task3["payload_sha256"],
                "task4": task4["payload_sha256"],
                "task5": task5["payload_sha256"],
            },
        }
    )

    target = output_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    compact_path = target / "compact_summary.json"
    analysis_path = target / "analysis.md"
    readme_path = target / "README.md"
    atomic_write_text(compact_path, canonical_json_bytes(compact).decode("utf-8") + "\n")
    atomic_write_text(analysis_path, _analysis_markdown(compact))
    atomic_write_text(readme_path, _readme_markdown())

    source_identities = {
        "task3": _source_identity(repository, task3_path, task3),
        "task4": _source_identity(repository, task4_path, task4),
        "task5": _source_identity(repository, task5_path, task5),
    }
    source_manifests = {
        "task3": _source_identity(
            repository,
            task3_path.parent / "checkpoint3_forced_owner_manifest.json",
            task3_manifest,
        ),
        "task4": _source_identity(
            repository,
            task4_path.parent / "checkpoint4_witness_ablation_manifest.json",
            task4_manifest,
        ),
    }
    generated_files = {
        path.name: {
            "byte_size": path.stat().st_size,
            "bytes_sha256": _sha256_file(path),
        }
        for path in (readme_path, analysis_path, compact_path)
    }
    manifest = seal_payload(
        {
            "schema_version": "uquant.strategic-evidence-closure-manifest.v1",
            "contract_payload_sha256": contract.payload_sha256,
            "source_evidence": source_identities,
            "source_manifests": source_manifests,
            "external_shards": external,
            "files": generated_files,
            "runner_success": runner_success,
            "capability_pass": policy.capability_pass,
        }
    )
    manifest_path = target / "evidence_manifest.json"
    atomic_write_text(manifest_path, canonical_json_bytes(manifest).decode("utf-8") + "\n")
    checksum_paths = (readme_path, analysis_path, compact_path, manifest_path)
    checksums = "".join(f"{_sha256_file(path)}  {path.name}\n" for path in checksum_paths)
    atomic_write_text(target / "SHA256SUMS", checksums)
    return {
        "runner_success": runner_success,
        "capability_pass": policy.capability_pass,
        "compact_payload_sha256": compact["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "failed_check_ids": list(policy.failed_check_ids),
    }


def validate_evidence_artifacts(
    output_dir: Path,
    *,
    external_paths: Mapping[str, Path],
    root: Path | None = None,
) -> dict[str, Any]:
    """Validate seals, tracked bytes, checksums, and supplied external shard bytes."""

    target = output_dir.resolve()
    compact = _load_sealed(target / "compact_summary.json", label="compact summary")
    manifest = _load_sealed(target / "evidence_manifest.json", label="evidence manifest")
    if manifest.get("capability_pass") != compact.get("capability_pass") or manifest.get(
        "runner_success"
    ) != compact.get("runner_success"):
        raise ValueError("manifest/compact runner or capability decision differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping):
        raise ValueError("evidence manifest tracked files are malformed")
    for name, identity in files.items():
        path = target / str(name)
        if not isinstance(identity, Mapping) or not path.is_file():
            raise ValueError("evidence manifest tracked file is missing")
        if identity.get("byte_size") != path.stat().st_size or identity.get("bytes_sha256") != _sha256_file(
            path
        ):
            raise ValueError(f"evidence manifest tracked file differs: {name}")
    external = manifest.get("external_shards")
    if not isinstance(external, Mapping):
        raise ValueError("evidence manifest external shards are malformed")
    if not set(external_paths) <= {"task3", "task4", "task5"}:
        raise ValueError("external shard task key differs")
    for task, path in external_paths.items():
        identity = external.get(task)
        expected = identity.get("sealed_expected_identity") if isinstance(identity, Mapping) else None
        if not isinstance(expected, Mapping):
            raise ValueError(f"external shard sealed identity is missing: {task}")
        if expected.get("byte_size") != path.stat().st_size or expected.get("bytes_sha256") != _sha256_file(
            path
        ):
            raise ValueError(f"external shard differs from sealed expected identity: {task}")
    validation_context: dict[str, Any] = {}
    if root is not None:
        repository = root.resolve()
        source_evidence = manifest.get("source_evidence")
        if not isinstance(source_evidence, Mapping):
            raise ValueError("source evidence identities are malformed")
        for task, identity in source_evidence.items():
            if not isinstance(identity, Mapping):
                raise ValueError(f"source evidence identity is malformed: {task}")
            logical = identity.get("logical_path")
            if not isinstance(logical, str) or Path(logical).is_absolute() or ".." in Path(logical).parts:
                raise ValueError(f"source evidence logical path is unsafe: {task}")
            path = repository / logical
            if identity.get("byte_size") != path.stat().st_size or identity.get(
                "bytes_sha256"
            ) != _sha256_file(path):
                raise ValueError(f"source evidence bytes differ: {task}")
            validation_context[f"{task}_path"] = path
        source_manifests = manifest.get("source_manifests")
        if not isinstance(source_manifests, Mapping):
            raise ValueError("source manifest identities are malformed")
        for task, identity in source_manifests.items():
            if not isinstance(identity, Mapping):
                raise ValueError(f"source manifest identity is malformed: {task}")
            logical = identity.get("logical_path")
            if not isinstance(logical, str) or Path(logical).is_absolute() or ".." in Path(logical).parts:
                raise ValueError(f"source manifest logical path is unsafe: {task}")
            path = repository / logical
            if identity.get("byte_size") != path.stat().st_size or identity.get(
                "bytes_sha256"
            ) != _sha256_file(path):
                raise ValueError(f"source manifest bytes differ: {task}")
        contract = load_contract(repository / "benchmarks/strategic_evidence_closure_contract.json")
        task3 = _load_sealed(validation_context["task3_path"], label="Task 3 compact evidence")
        task4 = _load_sealed(validation_context["task4_path"], label="Task 4 compact evidence")
        task5 = _load_sealed(validation_context["task5_path"], label="Task 5 compact evidence")
        task3_cells, task3_provenance, _ = _task3_compact_validation(
            repository,
            validation_context["task3_path"],
            task3,
            contract,
        )
        task4_cells, task4_provenance, _ = _task4_compact_validation(
            repository,
            validation_context["task4_path"],
            task4,
            contract,
        )
        _task5_summary_validation(task5)
        validation_context.update(
            {
                "contract": contract,
                "task3": task3,
                "task3_cells": task3_cells,
                "task3_provenance": task3_provenance,
                "task4": task4,
                "task4_cells": task4_cells,
                "task4_provenance": task4_provenance,
                "task5": task5,
            }
        )

    current_readback = {task: False for task in ("task3", "task4", "task5")}
    if external_paths and root is None:
        raise ValueError("repository root is required for task-specific external shard validation")
    for task, path in external_paths.items():
        if task == "task3":
            metadata = verify_forced_owner_trace_shard(
                path,
                expected_cells=validation_context["task3_cells"],
                expected_provenance=validation_context["task3_provenance"],
            ).metadata
            route = validation_context["task3"]["route_shard"]
        elif task == "task4":
            route = validation_context["task4"]["route_shard"]
            metadata = verify_full_route_linkage(
                path,
                expected_cell_ids=[cell.cell_id for cell in validation_context["task4_cells"]],
                expected_cells=validation_context["task4_cells"],
                expected_provenance=validation_context["task4_provenance"],
                expected_resume_identity=str(route["resume_identity"]),
            )
        else:
            _task5_rows_validation(
                path,
                summary=validation_context["task5"],
                contract=validation_context["contract"],
            )
            route = validation_context["task5"]
            metadata = {
                "byte_size": path.stat().st_size,
                "bytes_sha256": _sha256_file(path),
                "row_count": 84,
            }
        expected = external[task]["sealed_expected_identity"]
        if any(metadata.get(key) != expected.get(key) for key in ("byte_size", "bytes_sha256", "row_count")):
            raise ValueError(f"external shard task-specific readback differs: {task}")
        current_readback[task] = True

    checksum_path = target / "SHA256SUMS"
    try:
        checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("SHA256SUMS is unreadable") from exc
    expected_lines = [
        f"{_sha256_file(target / name)}  {name}"
        for name in ("README.md", "analysis.md", "compact_summary.json", "evidence_manifest.json")
    ]
    if checksum_lines != expected_lines:
        raise ValueError("SHA256SUMS differs from tracked evidence")
    return {
        "recorded_runner_success": manifest.get("runner_success"),
        "capability_pass": manifest.get("capability_pass"),
        "current_readback_verified": current_readback,
        "compact_payload_sha256": compact["payload_sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
    }


__all__ = ("assemble_evidence_artifacts", "validate_evidence_artifacts")
