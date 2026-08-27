from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from research.strategic_evidence.contract import load_contract
from research.strategic_evidence.forced_owner import (
    NO_NATIVE_ELIGIBILITY,
    ForcedOwnerCell,
    enumerate_forced_owner_controls,
    write_forced_owner_trace_shard,
)
from research.strategic_evidence.forced_owner_runner import (
    _write_summary_and_manifest,
    build_forced_owner_scenario,
    economically_compatible_provenance,
    economically_compatible_selection_evidence,
    verify_frozen_inputs,
    verify_task3_outputs,
)
from research.strategic_evidence.provenance import validate_provenance

ROOT = Path(__file__).parents[1]


def test_frozen_input_verifier_rejects_manifest_identity_drift() -> None:
    """Catches a matrix being attributed to a manifest other than sealed v1."""

    contract = load_contract(ROOT / "benchmarks/strategic_evidence_closure_contract.json")
    raw = deepcopy(contract.raw)
    raw["identities"]["data_manifest_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="data manifest identity differs"):
        verify_frozen_inputs(ROOT, replace(contract, raw=raw))


def test_matrix_scenario_binds_all_16_control_mode_ids() -> None:
    """Catches resume provenance treating an overlapping owner as a 14-cell run."""

    controls = enumerate_forced_owner_controls(
        positive_controls=("p1", "p2", "p3", "p4", "shared"),
        negative_controls={
            "LOWEST_LIQUID_LEADER_SCORE": "n1",
            "NEGATIVE_RET120_AND_WEAK_TREND": "shared",
            "LOWEST_SECULAR_CONFIDENCE_FAILING_ABSOLUTE": "n3",
        },
    )
    scenario = build_forced_owner_scenario(
        contract_payload_sha256="a" * 64,
        controls=controls,
        universe=("p1",),
        window={"start": "2023-01-03", "end": "2026-08-05"},
        activation_date="2023-01-04",
        target_gross=0.95,
        random_seed=20260826,
    )

    assert len(scenario["required_cell_ids"]) == 16
    assert len(set(scenario["required_cell_ids"])) == 16


def test_resume_rebind_allows_only_behavior_neutral_research_identity_change() -> None:
    """Catches resume reuse after a production or scenario identity mutation."""

    original = {
        "base_commit": "a" * 40,
        "experiment_commit": "b" * 40,
        "production_source_sha256": "c" * 64,
        "research_source_sha256": "d" * 64,
        "config_sha256": "e" * 64,
        "data_manifest_sha256": "f" * 64,
        "universe_sha256": "1" * 64,
        "industry_mapping_sha256": "2" * 64,
        "window_sha256": "3" * 64,
        "scenario_sha256": "4" * 64,
        "python": "3.12",
        "numpy": "2",
        "pandas": "3",
        "uv": "x",
        "uv_lock_sha256": "5" * 64,
        "generated_at": "2026-08-26T00:00:00Z",
    }
    rebound = {
        **original,
        "experiment_commit": "6" * 40,
        "research_source_sha256": "7" * 64,
    }

    assert economically_compatible_provenance(original, rebound) is True
    assert economically_compatible_provenance(
        original, {**rebound, "scenario_sha256": "8" * 64}
    ) is False
    assert economically_compatible_provenance(
        original, {**rebound, "production_source_sha256": "9" * 64}
    ) is False


def test_resume_rebind_allows_only_absent_to_empty_nonfinite_metadata() -> None:
    """Catches a representation migration accepting changed causal factor evidence."""

    original = {"owner_role": "POSITIVE_CONTROL", "momentum60": 0.5}
    normalized = {**original, "nonfinite_fields": []}

    assert economically_compatible_selection_evidence(original, normalized) is True
    assert economically_compatible_selection_evidence(
        original, {**normalized, "momentum60": 0.6}
    ) is False
    assert economically_compatible_selection_evidence(
        original, {**normalized, "nonfinite_fields": ["momentum60"]}
    ) is False


def test_compact_evidence_seals_are_portable_across_repository_roots(
    tmp_path: Path,
) -> None:
    """Catches checkout and external-shard paths entering compact evidence seals."""

    source = json.loads(
        (
            ROOT
            / "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json"
        ).read_text(encoding="utf-8")
    )
    provenance = validate_provenance(source["provenance"])
    cells = tuple(
        ForcedOwnerCell(
            control_id=str(raw["control_id"]),
            owner=str(raw["owner"]),
            mode=str(raw["mode"]),
            intervention_date=None,
            status=NO_NATIVE_ELIGIBILITY,
            selection_evidence=dict(raw["selection_evidence"]),
            metrics=None,
            metric_null_reasons={"all_economic_metrics": NO_NATIVE_ELIGIBILITY},
            final_account_sha256=None,
            trace_sha256=None,
            intervention_count=0,
            intervention_provenance=None,
        )
        for raw in source["cells"]
    )
    route_path = tmp_path / "runtime-routes.jsonl.gz"
    route_metadata = write_forced_owner_trace_shard(
        route_path,
        cells=cells,
        results={},
        provenance=provenance,
    )
    summary_payload = {
        **source,
        "cells": [cell.compact() for cell in cells],
        "status_counts": {NO_NATIVE_ELIGIBILITY: 16},
        "route_shard": route_metadata,
    }
    relative_summary = Path(
        "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_full.json"
    )
    relative_manifest = Path(
        "artifacts/strategic_evidence_closure/checkpoint3_forced_owner_manifest.json"
    )
    roots = (tmp_path / "checkout-a", tmp_path / "moved" / "checkout-b")
    results = []
    for root in roots:
        summary_path = root / relative_summary
        manifest_path = root / relative_manifest
        summary_path.parent.mkdir(parents=True)
        contract_path = root / "benchmarks/strategic_evidence_closure_contract.json"
        contract_path.parent.mkdir(parents=True)
        contract_path.write_bytes(
            (ROOT / "benchmarks/strategic_evidence_closure_contract.json").read_bytes()
        )
        _write_summary_and_manifest(
            repository=root,
            summary_path=summary_path,
            manifest_path=manifest_path,
            summary_payload=summary_payload,
            route_metadata=route_metadata,
        )
        results.append(
            verify_task3_outputs(
                root,
                summary_path=relative_summary,
                manifest_path=relative_manifest,
                trace_shard_path=route_path,
            )
        )

    first_summary = (roots[0] / relative_summary).read_bytes()
    first_manifest = (roots[0] / relative_manifest).read_bytes()
    assert (roots[1] / relative_summary).read_bytes() == first_summary
    assert (roots[1] / relative_manifest).read_bytes() == first_manifest
    assert results[0] == results[1]
    manifest = json.loads(first_manifest)
    summary = json.loads(first_summary)
    assert manifest["summary"]["path"] == relative_summary.as_posix()
    assert "path" not in summary["route_shard"]
    assert "path" not in manifest["route_shard"]
