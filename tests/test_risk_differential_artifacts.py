from __future__ import annotations

import ast
import gzip
import json
from pathlib import Path

from research.risk_differential_models import canonical_sha256, validate_capabilities

ROOT = Path(__file__).parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_preregistered_contract_and_registry_are_canonically_sealed() -> None:
    for path in (
        "benchmarks/risk_differential_contract.json",
        "benchmarks/risk_differential_source_registry.json",
        "benchmarks/risk_capability_registry.json",
        "benchmarks/risk_differential_holdout_identity.json",
    ):
        payload = _load(path)
        assert payload["payload_sha256"] == canonical_sha256(payload)


def test_capability_inventory_has_no_unknown_or_production_promotion() -> None:
    records = validate_capabilities(_load("benchmarks/risk_capability_registry.json")["capabilities"])
    assert records
    assert all(item.mapping_status != "UNKNOWN" for item in records)
    assert not any(item.production_promotion_allowed_this_phase for item in records)


def test_closure_preserves_negative_controls_and_production_boundary() -> None:
    closure = _load("artifacts/sentinel/risk_differential/closure.json")
    assert closure["production_behavior_changed"] is False
    assert closure["production_authority_changed"] is False
    assert closure["negative_controls"]["phase5_limited_gross_cap"] == "REJECTED"
    assert closure["negative_controls"]["phase7_exclusive_freeze"] == "REJECTED"
    assert closure["final_decision"] == "NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY"
    assert closure["trade_material_incremental_capabilities"] == []


def test_complete_matrix_binds_deterministic_daily_trace() -> None:
    matrix = _load("artifacts/sentinel/risk_differential/risk_differential_matrix.json")
    compressed = (ROOT / "artifacts/sentinel/risk_differential/risk_differential_daily.json.gz").read_bytes()
    daily = json.loads(gzip.decompress(compressed))
    assert matrix["summary"]["status"] == "COMPLETE"
    assert matrix["summary"]["cells"] == 264
    assert matrix["summary"]["daily_trace_pairs"] == 162
    assert daily["payload_sha256"] == canonical_sha256(daily)
    assert len(daily["cells"]) == 162
    assert matrix["provenance"]["runtime"]["python_hash_seed"] == "0"


def test_pinned_trade_challenger_trace_is_complete_and_source_bound() -> None:
    compressed = (
        ROOT / "artifacts/sentinel/risk_differential/trade_challenger_trace.json.gz"
    ).read_bytes()
    trace = json.loads(gzip.decompress(compressed))
    registry = _load("benchmarks/risk_differential_source_registry.json")
    assert trace["payload_sha256"] == canonical_sha256(trace)
    assert trace["trade_commit"] == registry["trade"]["commit"]
    assert trace["trade_source_sha256"] == registry["trade"]["risk_source_sha256"]
    assert len(trace["cells"]) == 162


def test_all_terminal_artifacts_are_canonically_sealed() -> None:
    for name in (
        "capability_inventory.json",
        "risk_differential_matrix.json",
        "exclusive_events.json",
        "counterfactual_raw.json",
        "counterfactual_summary.json",
        "event_outcome_analysis.json",
        "promotion_analysis.json",
        "closure.json",
    ):
        payload = _load(f"artifacts/sentinel/risk_differential/{name}")
        assert payload["payload_sha256"] == canonical_sha256(payload)


def test_production_imports_are_isolated_from_research_and_trade() -> None:
    forbidden = {"research", "trade", "quantfusion", "regime_adaptive"}
    for path in sorted((ROOT / "uquant").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported.isdisjoint(forbidden), path


def test_replay_cannot_backfill_from_a_current_account_snapshot() -> None:
    sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "research/risk_replay_runtime.py",
            "scripts/run_risk_differential.py",
        )
    )
    assert "account.json" not in sources
    assert "load_account" not in sources


def test_production_economic_equivalence_is_exact_across_frozen_matrix() -> None:
    proof = _load(
        "artifacts/sentinel/risk_differential/production_economic_equivalence.json"
    )
    assert proof["passed"] is True
    assert proof["cases"] == 45
    assert proof["baseline_trace_sha256"] == proof["candidate_trace_sha256"]
    assert all(proof["exact_dimensions"].values())
