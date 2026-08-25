from __future__ import annotations

import ast
import gzip
import importlib.util
import json
from pathlib import Path

from research.risk_differential_models import canonical_sha256, validate_capabilities

ROOT = Path(__file__).parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _load_negative_control_runner():
    path = ROOT / "scripts/run_risk_negative_controls.py"
    spec = importlib.util.spec_from_file_location("risk_negative_control_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_capability_inventory_maps_every_registered_trade_risk_source() -> None:
    source_registry = _load("benchmarks/risk_differential_source_registry.json")
    capability_registry = _load("benchmarks/risk_capability_registry.json")
    registered = set(source_registry["trade"]["risk_source_files"])
    mapped = {
        source
        for capability in capability_registry["capabilities"]
        for source in capability["trade_source"]
    }
    assert mapped == registered


def test_closure_preserves_negative_controls_and_production_boundary() -> None:
    closure = _load("artifacts/sentinel/risk_differential/closure.json")
    assert closure["production_behavior_changed"] is False
    assert closure["production_authority_changed"] is False
    assert closure["negative_controls"]["phase5_limited_gross_cap"] == "REJECTED"
    assert closure["negative_controls"]["phase7_exclusive_freeze"] == "REJECTED"
    assert closure["final_decision"] == "NO_PROMOTABLE_INCREMENTAL_RISK"
    assert closure["conclusion_code"] == "NO_INCREMENTAL_PROMOTABLE_RISK_CAPABILITY"
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
        "negative_controls_rerun.json",
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


def test_every_official_control_and_shadow_is_an_actual_engine_replay() -> None:
    raw = _load("artifacts/sentinel/risk_differential/counterfactual_raw.json")
    assert raw["provenance"]["runtime"]["python_hash_seed"] == "0"
    assert len(raw["provenance"]["counterfactual_runner_identity"]) == 64
    required = {
        "baseline_uquant",
        "base_only_control",
        "sentinel_freeze_only_control",
        "trade_entry_freeze_shadow",
        "trade_pyramid_freeze_shadow",
        "trade_gross_cap_shadow",
        "trade_layered_protection_shadow",
        "trade_cluster_trim_hybrid_shadow",
    }
    rows = [item for item in raw["cells"] if item["matrix_axis"] == "official_pool"]
    assert {item["policy_id"] for item in rows} == required
    assert all(item["execution_mode"] == "FULL_PRODUCTION_ENGINE_REPLAY" for item in rows)
    assert all(sum(item["policy_id"] == policy for item in rows) == 30 for policy in required)

    indexed = {(item["cell_id"], item["policy_id"]): item for item in rows}
    economic = {
        "final_wealth",
        "total_return",
        "max_drawdown",
        "acute_return",
        "account_orders",
        "gross_turnover",
        "annual_turnover",
        "decision_digest_sha256",
        "target_plan_sha256",
        "pending_order_plan_sha256",
        "fill_ledger_sha256",
        "order_ledger_sha256",
        "economic_account_sha256",
    }
    cells = {item["cell_id"] for item in rows}
    for cell in cells:
        baseline = indexed[(cell, "baseline_uquant")]
        explicit = indexed[(cell, "sentinel_freeze_only_control")]
        assert {key: baseline[key] for key in economic} == {
            key: explicit[key] for key in economic
        }
        hybrid = indexed[(cell, "trade_cluster_trim_hybrid_shadow")]
        assert hybrid["trigger_count"] == 0
        assert {key: baseline[key] for key in economic} == {
            key: hybrid[key] for key in economic
        }

    generalization = [item for item in raw["cells"] if item["matrix_axis"] == "generalization"]
    generalization_policies = {
        "baseline_uquant",
        "trade_gross_cap_shadow",
        "trade_layered_protection_shadow",
    }
    assert len(generalization) == 132 * len(generalization_policies)
    assert {item["policy_id"] for item in generalization} == generalization_policies
    assert all(item["execution_mode"] == "FULL_PRODUCTION_ENGINE_REPLAY" for item in generalization)


def test_negative_controls_are_detached_reruns_not_constants() -> None:
    controls = _load("artifacts/sentinel/risk_differential/negative_controls_rerun.json")
    gross_cap = controls["phase5_limited_gross_cap"]
    evidence_recovery = controls["phase7_exclusive_freeze"]
    assert gross_cap["detached_rerun"] is True
    assert gross_cap["status"] == "REJECTED"
    assert gross_cap["matches_archived_evidence"] is True
    assert evidence_recovery["detached_rerun"] is True
    assert evidence_recovery["status"] == "REJECTED"
    assert evidence_recovery["matches_archived_evidence"] is True
    assert evidence_recovery["actionable_buy_intents"] == 0
    assert evidence_recovery["exact_economic_equivalence"] is True


def test_negative_control_git_commands_use_an_absolute_executable() -> None:
    runner = _load_negative_control_runner()
    command = runner._git_command("cat-file", "-e", "HEAD^{commit}")
    assert Path(command[0]).is_absolute()
    assert command[1:] == ["cat-file", "-e", "HEAD^{commit}"]


def test_incomplete_forward_outcomes_are_right_censored() -> None:
    outcomes = _load("artifacts/sentinel/risk_differential/event_outcome_analysis.json")
    assert outcomes["right_censored_20d_event_count"] > 0
    assert outcomes["complete_20d_event_count"] + outcomes["right_censored_20d_event_count"] == outcomes[
        "event_count"
    ]
    assert all(item["realized_shock"] in {True, False} for item in outcomes["episodes"])


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
