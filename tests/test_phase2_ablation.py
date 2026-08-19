from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from research import ablation_registry as ablation_registry_module
from research.ablation import (
    AblationCell,
    AblationMetrics,
    DecisionPoint,
    aggregate_dimensions,
    compare_cells,
    first_decision_divergence,
    validate_complete_coverage,
)
from research.ablation_registry import (
    DEFAULT_ABLATION_REGISTRY_PATH,
    MINIMAL_ABLATION_REGISTRY_PATH,
    REQUIRED_SUBSYSTEMS,
    ContractCell,
    build_contract_schedule,
    canonical_sha256,
    isolated_baseline_checkout,
    isolated_carrier_checkout,
    load_ablation_registry,
    source_fingerprint,
    validate_ablation_registry,
    verify_carrier_checkout,
)
from uquant.engine import ProductionEngine

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "artifacts" / "phase2" / "ablations" / "results.json"
POST_TASK8_SOURCE_CONTRACT_PATH = (
    ROOT / "artifacts" / "phase2" / "ablations" / "post_task8_source_contract.json"
)


def _reviewed_source_commit() -> str:
    contract = json.loads(POST_TASK8_SOURCE_CONTRACT_PATH.read_text(encoding="utf-8"))
    reviewed = contract["reviewed"]
    assert isinstance(reviewed, dict)
    commit = reviewed["commit"]
    assert isinstance(commit, str)
    return commit


def _reviewed_source_checkout(destination: Path) -> Path:
    commit = _reviewed_source_commit()
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(destination)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(destination), "checkout", "--quiet", "--detach", commit],
        check=True,
    )
    return destination


def _current_runner_with_reviewed_production_checkout(destination: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(destination)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "restore",
            f"--source={_reviewed_source_commit()}",
            "--staged",
            "--worktree",
            "--",
            "uquant",
            "pyproject.toml",
            "requirements.txt",
            "uv.lock",
            "benchmarks/reference_registry.json",
            "benchmarks/config_parameter_governance.json",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=UQuant Tests",
            "-c",
            "user.email=tests@uquant.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "Test current runner against reviewed production",
        ],
        check=True,
    )
    return destination


def _runner_module():
    source = ROOT / "scripts" / "run_phase2_ablation.py"
    spec = importlib.util.spec_from_file_location("phase2_ablation_runner", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_config_binding_is_read_from_the_evidence_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches authenticating historical evidence with the caller's live config."""
    runner = _runner_module()
    observed: list[tuple[Path, dict[str, bool]]] = []

    def probe(root: Path, changes: dict[str, bool]) -> dict[str, str]:
        observed.append((root, changes))
        return {"effective_config_sha256": "a" * 64}

    monkeypatch.setattr(runner, "_probe_checkout", probe)

    assert runner._baseline_config_sha256(tmp_path) == "a" * 64
    assert observed == [(tmp_path, {})]


def test_registry_covers_every_mandated_active_subsystem_once() -> None:
    """Catches omitted, duplicated, bundled, or silently inactive mechanisms."""
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)

    runnable = {experiment.subsystem for experiment in registry.experiments}
    excluded = {item.subsystem for item in registry.exclusions}

    assert runnable | excluded == set(REQUIRED_SUBSYSTEMS)
    assert not runnable & excluded
    assert len(runnable) == len(registry.experiments) == 13
    assert excluded == {
        "hierarchical_industry_shrinkage",
        "group_balanced_reference",
    }
    assert all(item.reason == "inactive_in_frozen_config" for item in registry.exclusions)


def test_results_classify_all_subsystems_from_authenticated_evidence() -> None:
    """Catches missing dimensions, relabeled invalid runs, or an unsupported deletion."""
    payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    rows = payload["subsystems"]
    by_name = {row["subsystem"]: row for row in rows}
    historical = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)

    assert payload["schema_version"] == 1
    assert set(by_name) == {item.subsystem for item in historical.experiments}
    assert len(rows) == 13
    assert {name: row["decision"] for name, row in by_name.items()} == {
        "sector_guard": "KEEP",
        "chronic_overlay": "KEEP",
        "transition_overlay": "DELETE",
        "capital_budget_ladder": "KEEP",
        "challenger_scout": "INCONCLUSIVE",
        "conviction_weighting": "INCONCLUSIVE",
        "recovery_conviction_weighting": "KEEP",
        "tactical_rebound_probe": "KEEP",
        "strategic_trailing": "KEEP",
        "restoration_special_handling": "KEEP",
        "add_tranche": "KEEP",
        "replacement_rotation": "KEEP",
        "dynamic_risk_anchors": "KEEP",
    }
    assert payload["decision_counts"] == {"DELETE": 1, "INCONCLUSIVE": 2, "KEEP": 10}
    assert payload["metric_directions"] == {
        "account_orders": "lower_is_better",
        "acute_return": "higher_is_better",
        "annual_turnover": "lower_is_better",
        "final_wealth": "higher_is_better",
        "gross_turnover": "lower_is_better",
        "max_drawdown": "lower_is_better",
        "pnl_hhi": "lower_is_better",
        "top1_concentration": "lower_is_better",
        "top3_concentration": "lower_is_better",
    }
    for row in rows:
        assert set(row["evidence_epochs"]) <= {"pre_deletion", "post_deletion"}
        for epoch in row["evidence_epochs"].values():
            for contract in epoch["contracts"].values():
                assert set(contract["dimensions"]) == set(payload["metric_directions"])
    for subsystem in ("challenger_scout", "conviction_weighting"):
        assert by_name[subsystem]["decision"] == "INCONCLUSIVE"
        assert all(
            epoch["artifact_kind"] == "invalid_experiment"
            and epoch["invalid_reason"] == "no_behavior_divergence"
            for epoch in by_name[subsystem]["evidence_epochs"].values()
        )
    transition = by_name["transition_overlay"]
    assert set(transition["evidence_epochs"]) == {"pre_deletion"}
    assert transition["all_common_valid_metric_deltas_zero"] is True
    assert transition["status_transition_count"] == 0


def test_post_deletion_registry_is_derived_without_relabeling_historical_evidence() -> None:
    """Catches mutating Task-7 identity or retaining the deleted carrier in fresh evidence."""
    historical = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    minimal = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)

    assert historical.registry_id == "phase2-independent-subsystem-ablation-v1"
    assert historical.base_commit == "7f80436373b6da03536e15ff1908c010bfb92eb3"
    assert len(historical.experiments) == 13
    assert historical.deleted_subsystems == ()
    assert minimal.registry_id == "phase2-post-transition-deletion-ablation-v1"
    assert minimal.base_commit == "e5e0fa903c9a9b26701063ae01f352af3e246a7d"
    assert len(minimal.experiments) == 12
    assert minimal.deleted_subsystems == ("transition_overlay",)
    assert "transition_overlay" not in {item.subsystem for item in minimal.experiments}
    assert tuple(item.experiment_id for item in minimal.experiments) == tuple(
        item.experiment_id
        for item in historical.experiments
        if item.subsystem != "transition_overlay"
    )
    assert minimal.fixed_contracts == historical.fixed_contracts
    assert minimal.invariants == historical.invariants
    assert minimal.exclusions == historical.exclusions
    with pytest.raises(ValueError, match="production source differs"):
        validate_ablation_registry(historical, source_root=ROOT)


def test_phase4_source_does_not_rewrite_reviewed_phase2_contract() -> None:
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    observed_source_sha256 = source_fingerprint(ROOT)

    with pytest.raises(ValueError, match="production source differs"):
        ablation_registry_module._validate_post_task8_source(
            registry,
            root=ROOT,
            observed_source_sha256=observed_source_sha256,
        )


def test_post_task8_source_allowance_is_content_addressed_and_rejects_mutation(
    tmp_path: Path,
) -> None:
    """Catches a later operational path allowlist accepting unreviewed byte changes."""
    checkout = _reviewed_source_checkout(tmp_path / "reviewed-source")
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)

    validate_ablation_registry(registry, source_root=checkout)
    config = checkout / "uquant" / "config.py"
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="production source differs"):
        validate_ablation_registry(registry, source_root=checkout)


def test_sentinel_shadow_overlay_rejects_resealed_source_mutation(tmp_path: Path) -> None:
    checkout = tmp_path / "sentinel-source"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(checkout)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "checkout",
            "--quiet",
            "--detach",
            "4be0ad2e8b2f44bad03042c05ddded0bc1c7a3aa",
        ],
        check=True,
    )
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)

    validate_ablation_registry(registry, source_root=checkout)
    sentinel = checkout / "uquant" / "risk_sentinel" / "service.py"
    sentinel.write_text(sentinel.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Sentinel Shadow observation overlay bytes"):
        validate_ablation_registry(registry, source_root=checkout)


def test_post_deletion_coverage_does_not_count_deleted_or_historical_carriers() -> None:
    """Catches cross-accepting the deleted transition carrier or claiming 13 fresh runs."""
    runner = _runner_module()
    historical = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    minimal = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    valid = {
        item.experiment_id: {
            "experiment_id": item.experiment_id,
            "kind": "experiment",
        }
        for item in minimal.experiments
    }

    coverage = runner._evidence_coverage(minimal, valid=valid, invalid={})

    assert coverage["complete"] is True
    assert coverage["coverage_complete"] is True
    assert coverage["required_experiment_count"] == 12
    assert coverage["missing_experiment_ids"] == []
    assert coverage["deleted_subsystems"] == ["transition_overlay"]
    historical_coverage = runner._evidence_coverage(historical, valid={}, invalid={})
    assert "deleted_subsystems" not in historical_coverage
    with pytest.raises(ValueError, match="unregistered experiment"):
        runner._evidence_coverage(
            minimal,
            valid={
                **valid,
                "without_transition_overlay": {
                    "experiment_id": "without_transition_overlay",
                    "kind": "experiment",
                },
            },
            invalid={},
        )


def test_registry_carriers_are_unique_one_at_a_time_and_content_addressed() -> None:
    """Catches shared carriers, multi-subsystem deltas, and unsealed patch/config edits."""
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    identities = [experiment.carrier.sha256 for experiment in registry.experiments]

    assert len(identities) == len(set(identities))
    for experiment in registry.experiments:
        carrier = experiment.carrier
        assert carrier.subsystem == experiment.subsystem
        if carrier.kind == "config":
            assert len(carrier.changes) == 1
            assert set(dict(carrier.changes).values()) == {False}
            assert carrier.patch == ""
            assert carrier.touched_paths == ()
            assert carrier.sha256 == canonical_sha256({"changes": dict(carrier.changes)})
        else:
            assert carrier.kind == "patch"
            assert carrier.changes == ()
            assert carrier.patch.startswith("diff --git a/")
            assert len(carrier.touched_paths) == 1
            assert carrier.sha256 == canonical_sha256({"patch": carrier.patch})


def test_registry_preserves_market_safety_and_frozen_contracts() -> None:
    """Catches ablations of execution/accounting/PIT controls or changed seeds/windows."""
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)

    invariant = registry.invariants
    assert {
        "T+1",
        "price_limits",
        "suspension",
        "lot_sizing",
        "fees_slippage",
        "data_causality",
        "cash_constraints",
        "PIT",
        "fail_closed_validation",
    } <= set(invariant.rules)
    assert {
        "uquant/account.py",
        "uquant/broker.py",
        "uquant/data.py",
        "uquant/execution.py",
        "uquant/validation",
    } <= set(invariant.protected_paths)
    assert not any(
        field in invariant.protected_config_fields
        for experiment in registry.experiments
        for field, _ in experiment.carrier.changes
    )
    assert not any(
        any(path == protected or path.startswith(f"{protected}/") for protected in invariant.protected_paths)
        for experiment in registry.experiments
        for path in experiment.carrier.touched_paths
    )

    phase1 = registry.contract("phase1_performance")
    generalization = registry.contract("ai_era_generalization")
    assert phase1.record_count == phase1.economic_count == 45
    assert phase1.minimum_date == "2023-01-03"
    assert generalization.record_count == 234
    assert generalization.economic_count == 192
    assert generalization.valid_count == 191
    assert generalization.replay_error_count == 1
    assert generalization.insufficient_count == 42
    assert generalization.lookback_sessions == 120
    assert generalization.random_base_seed == 20260810
    assert generalization.random_seed_indexes == (0, 1, 2, 3, 4)
    assert generalization.random_pool_sizes == (5, 9, 15, 20)
    assert next((window.start, window.end) for window in generalization.windows) == (
        "2023-01-03",
        "2023-06-30",
    )
    assert tuple((window.start, window.end) for window in generalization.windows)[-1] == (
        "2023-01-03",
        "2026-08-05",
    )


def _metrics(
    *,
    wealth: float,
    drawdown: float,
    orders: int,
    acute: float | None,
    turnover: float,
    top1: float,
    top3: float,
    hhi: float,
) -> AblationMetrics:
    return AblationMetrics(
        final_wealth=wealth,
        max_drawdown=drawdown,
        account_orders=orders,
        acute_return=acute,
        gross_turnover=turnover,
        annual_turnover=turnover * 2.0,
        top1_concentration=top1,
        top3_concentration=top3,
        pnl_hhi=hhi,
    )


def _single_cell_worker(
    runner,
    *,
    binding_sha256: str,
    experiment_id: str,
    carrier_sha256: str,
    stage_hash: str,
    wealth: float = 1.0,
) -> tuple[tuple[ContractCell, ...], dict[str, object]]:
    schedule = (
        ContractCell(
            contract="phase1_performance",
            cell_id="a/h1_2023",
            status="VALID",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2023-01-04",
        ),
    )
    provenance = {
        "checkout": {"carrier_sha256": carrier_sha256},
        "effective_config_sha256": "d" * 64,
        "data": {"snapshot_id": "fixed"},
        "runtime": {"python_full_version": "3.12.13"},
        "uv_lock_sha256": "e" * 64,
        "replay_command_sha256": "f" * 64,
    }
    metrics = _metrics(
        wealth=wealth,
        drawdown=0.1,
        orders=2,
        acute=None,
        turnover=0.5,
        top1=0.4,
        top3=0.6,
        hhi=0.2,
    ).to_dict()
    return schedule, {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": binding_sha256,
        "experiment_id": experiment_id,
        "cells": [
            {
                "contract": "phase1_performance",
                "cell_id": "a/h1_2023",
                "frozen_status": "VALID",
                "status": "VALID",
                "economic": True,
                "metrics": metrics,
                "replay_error": None,
                "raw_result_sha256": "c" * 64,
            }
        ],
        "traces": {
            "phase1_performance/a/h1_2023": [
                {
                    "date": "2023-01-03",
                    "stages": {name: stage_hash for name in runner._CAUSAL_STAGES},
                }
            ]
        },
        "provenance": provenance,
    }


def test_comparison_emits_every_raw_materiality_dimension_without_classification() -> None:
    """Catches dropped Task-8 inputs or premature KEEP/DELETE classification."""
    baseline = AblationCell(
        contract="phase1_performance",
        cell_id="a/h1_2023",
        status="VALID",
        metrics=_metrics(
            wealth=2.0,
            drawdown=0.20,
            orders=10,
            acute=-0.05,
            turnover=1.0,
            top1=0.7,
            top3=0.9,
            hhi=0.5,
        ),
    )
    variant = AblationCell(
        contract="phase1_performance",
        cell_id="a/h1_2023",
        status="VALID",
        metrics=_metrics(
            wealth=1.9,
            drawdown=0.18,
            orders=8,
            acute=-0.03,
            turnover=0.8,
            top1=0.6,
            top3=0.8,
            hhi=0.4,
        ),
    )

    delta = compare_cells(baseline, variant)

    assert delta.final_wealth == pytest.approx(-0.1)
    assert delta.max_drawdown == pytest.approx(-0.02)
    assert delta.account_orders == -2
    assert delta.acute_return == pytest.approx(0.02)
    assert delta.gross_turnover == pytest.approx(-0.2)
    assert delta.annual_turnover == pytest.approx(-0.4)
    assert delta.top1_concentration == pytest.approx(-0.1)
    assert delta.top3_concentration == pytest.approx(-0.1)
    assert delta.pnl_hhi == pytest.approx(-0.1)
    assert "classification" not in delta.to_dict()
    assert "decision" not in delta.to_dict()


def test_aggregate_dimensions_preserve_tail_generalization_inputs() -> None:
    """Catches mean-only summaries that hide tail wealth, drawdown, or concentration."""
    cells = tuple(
        AblationCell(
            contract="ai_era_generalization",
            cell_id=f"h1_2023/random__05__000{index}",
            status="VALID",
            metrics=_metrics(
                wealth=wealth,
                drawdown=drawdown,
                orders=orders,
                acute=None,
                turnover=turnover,
                top1=top1,
                top3=min(1.0, top1 + 0.2),
                hhi=top1 / 2.0,
            ),
        )
        for index, (wealth, drawdown, orders, turnover, top1) in enumerate(
            (
                (0.8, 0.30, 20, 2.0, 0.8),
                (1.0, 0.20, 10, 1.0, 0.6),
                (1.2, 0.10, 5, 0.5, 0.4),
            )
        )
    )

    aggregate = aggregate_dimensions(cells)

    assert aggregate["p10_final_wealth"] == pytest.approx(0.84)
    assert aggregate["p90_max_drawdown"] == pytest.approx(0.28)
    assert aggregate["p90_account_orders"] == pytest.approx(18.0)
    assert aggregate["p90_gross_turnover"] == pytest.approx(1.8)
    assert aggregate["worst_top1_concentration"] == pytest.approx(0.8)
    assert aggregate["worst_top3_concentration"] == pytest.approx(1.0)
    assert aggregate["worst_pnl_hhi"] == pytest.approx(0.4)


def test_required_first_divergence_rejects_identical_or_misaligned_runs() -> None:
    """Catches no-op carriers and incomparable decision calendars."""
    left = (
        DecisionPoint("2023-01-03", (("orders", []), ("risk", "NORMAL"))),
        DecisionPoint("2023-01-04", (("orders", []), ("risk", "NORMAL"))),
    )
    right = (
        left[0],
        DecisionPoint(
            "2023-01-04",
            (("orders", [{"side": "SELL", "symbol": "sz300308"}]), ("risk", "NORMAL")),
        ),
    )

    divergence = first_decision_divergence(left, right, require=True)
    assert divergence is not None
    assert divergence.date == "2023-01-04"
    assert divergence.changed_fields == ("orders",)

    with pytest.raises(ValueError, match="no behavior divergence"):
        first_decision_divergence(left, left, require=True)
    with pytest.raises(ValueError, match="aligned dates"):
        first_decision_divergence(left, right[:1], require=True)


def test_complete_coverage_rejects_missing_duplicate_and_status_rewrite() -> None:
    """Catches partial runs and hiding the frozen known replay/sample statuses."""
    expected = (
        ("h1/full", "VALID"),
        ("h1/random", "REPLAY_ERROR"),
        ("h1/small", "INSUFFICIENT_SAMPLE"),
    )
    valid_metrics = _metrics(
        wealth=1.0,
        drawdown=0.0,
        orders=0,
        acute=None,
        turnover=0.0,
        top1=0.0,
        top3=0.0,
        hhi=0.0,
    )
    observed = tuple(
        AblationCell("g", name, status, valid_metrics if status == "VALID" else None)
        for name, status in expected
    )
    validate_complete_coverage(expected, observed)

    with pytest.raises(ValueError, match="coverage"):
        validate_complete_coverage(expected, observed[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        validate_complete_coverage(expected, (*observed, observed[0]))
    changed = list(observed)
    changed[1] = AblationCell(
        "g",
        "h1/random",
        "VALID",
        _metrics(
            wealth=1.0,
            drawdown=0.0,
            orders=0,
            acute=None,
            turnover=0.0,
            top1=0.0,
            top3=0.0,
            hhi=0.0,
        ),
    )
    with pytest.raises(ValueError, match="status"):
        validate_complete_coverage(expected, changed)


def test_registry_and_source_hashes_are_deterministic_and_mutation_sensitive(
    tmp_path: Path,
) -> None:
    """Catches nondeterministic serialization and stale/resealed production source."""
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    payload = json.loads(DEFAULT_ABLATION_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert registry.payload_sha256 == canonical_sha256(payload)

    (tmp_path / "uquant").mkdir()
    source = tmp_path / "uquant" / "example.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    original = source_fingerprint(tmp_path, ("uquant/example.py",))
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert source_fingerprint(tmp_path, ("uquant/example.py",)) != original

    mutated = copy.deepcopy(payload)
    mutated["fixed_contracts"][0]["sha256"] = "0" * 64
    changed = tmp_path / "registry.json"
    changed.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(ValueError, match="fixed contract hash"):
        load_ablation_registry(changed)

    ambiguous = DEFAULT_ABLATION_REGISTRY_PATH.read_text(encoding="utf-8").replace(
        '"schema_version": 1,',
        '"schema_version": 1,\n  "schema_version": 1,',
        1,
    )
    changed.write_text(ambiguous, encoding="utf-8")
    with pytest.raises(ValueError, match=r"duplicate.*key"):
        load_ablation_registry(changed)

    resealed = copy.deepcopy(payload)
    patch_carrier = next(
        item["carrier"] for item in resealed["experiments"] if item["carrier"]["type"] == "patch"
    )
    patch_carrier["patch"] = patch_carrier["patch"].replace(
        "False and not account.positions",
        "(False) and not account.positions",
    )
    patch_carrier["sha256"] = canonical_sha256({"patch": patch_carrier["patch"]})
    changed.write_text(json.dumps(resealed), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed carrier"):
        load_ablation_registry(changed)


def test_trusted_ablation_json_readers_normalize_invalid_utf8(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches codec errors escaping the ablation domain trust boundary."""

    invalid = tmp_path / "invalid.json"
    encoded = b"\xff"
    invalid.write_bytes(encoded)

    with pytest.raises(ValueError, match="cannot load ablation registry"):
        load_ablation_registry(invalid)

    minimal = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    monkeypatch.setattr(
        ablation_registry_module,
        "POST_TASK8_SOURCE_CONTRACT_PATH",
        invalid,
    )
    with pytest.raises(ValueError, match="cannot load post-Task8 source contract"):
        ablation_registry_module._validate_post_task8_source(
            minimal,
            root=ROOT,
            observed_source_sha256=minimal.source_sha256,
        )

    runner = _runner_module()
    with pytest.raises(ValueError, match="evidence manifest is unreadable"):
        runner._load_trusted_evidence_manifest(invalid)
    with pytest.raises(ValueError, match="test artifact is unreadable"):
        runner._load_json_mapping(invalid, label="test artifact")
    assert runner._checkpoint_payload_schema(invalid) is None

    digest = hashlib.sha256(encoded).hexdigest()
    raw = tmp_path / "raw" / f"{digest}.worker.json"
    raw.parent.mkdir()
    raw.write_bytes(encoded)
    reference = {
        "path": f"raw/{digest}.worker.json",
        "payload_sha256": digest,
        "file_sha256": digest,
    }
    with pytest.raises(ValueError, match="raw worker artifact is unreadable"):
        runner._read_worker_artifact(tmp_path, reference)

    with pytest.raises(ValueError, match="experiment artifact is unreadable"):
        runner._validate_evidence_manifest_entry(
            tmp_path,
            {
                "artifact": {"path": "invalid.json"},
                "raw": {"path": "raw/unused.json"},
            },
        )


def test_fixed_schedule_is_complete_deterministic_and_preserves_known_status() -> None:
    """Catches a runner that shards, replaces, or omits a fixed contract record."""
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)

    first = build_contract_schedule(registry, source_root=ROOT)
    second = build_contract_schedule(registry, source_root=ROOT)

    assert first == second
    assert len(first) == 45 + 234
    assert sum(item.economic for item in first) == 45 + 192
    assert sum(item.status == "VALID" for item in first) == 45 + 191
    assert sum(item.status == "REPLAY_ERROR" for item in first) == 1
    assert sum(item.status == "INSUFFICIENT_SAMPLE" for item in first) == 42
    assert len({(item.contract, item.cell_id) for item in first}) == len(first)
    assert all(item.start >= "2023-01-01" for item in first)
    assert all(item.symbols == tuple(sorted(set(item.symbols))) for item in first)
    random = next(
        item for item in first if item.contract == "ai_era_generalization" and item.seed_index is not None
    )
    assert random.seed_index in {0, 1, 2, 3, 4}
    assert random.pool_size in {5, 9, 15, 20}
    assert random.derived_seed is not None


@pytest.mark.parametrize("subsystem", ("sector_guard", "tactical_rebound_probe"))
def test_carrier_materializes_in_an_isolated_clean_content_addressed_checkout(
    subsystem: str,
    tmp_path: Path,
) -> None:
    """Catches in-place execution, dirty patch trees, or unverified carrier state."""
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    experiment = next(item for item in registry.experiments if item.subsystem == subsystem)
    source_root = _reviewed_source_checkout(tmp_path / "reviewed-source")
    destination = tmp_path / subsystem

    with isolated_carrier_checkout(
        registry,
        experiment,
        source_root=source_root,
        destination=destination,
    ) as checkout:
        assert checkout.root == destination.resolve()
        assert checkout.root != ROOT.resolve()
        assert checkout.base_commit == registry.base_commit
        assert checkout.carrier_sha256 == experiment.carrier.sha256
        assert checkout.config_changes == experiment.carrier.changes
        assert (
            subprocess.run(
                ["git", "-C", str(checkout.root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            == ""
        )
        verify_carrier_checkout(registry, experiment, checkout)
        if experiment.carrier.kind == "patch":
            assert checkout.experiment_commit != checkout.base_commit
            target = checkout.root / experiment.carrier.touched_paths[0]
            original = target.read_text(encoding="utf-8")
            target.write_text(original + "\n", encoding="utf-8")
            with pytest.raises(ValueError, match="changed after materialization"):
                verify_carrier_checkout(registry, experiment, checkout)
            target.write_text(original, encoding="utf-8")
        else:
            assert checkout.experiment_commit == checkout.base_commit
            assert checkout.source_sha256 == registry.source_sha256

    assert not destination.exists()


def test_baseline_materializes_as_exact_isolated_clean_source(
    tmp_path: Path,
) -> None:
    """Catches baseline execution from a dirty task worktree or a moving HEAD."""
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    source_root = _reviewed_source_checkout(tmp_path / "reviewed-source")
    destination = tmp_path / "baseline"

    with isolated_baseline_checkout(
        registry,
        source_root=source_root,
        destination=destination,
    ) as checkout:
        assert checkout.base_commit == registry.base_commit
        assert checkout.experiment_commit == registry.base_commit
        assert checkout.source_sha256 == registry.source_sha256
        assert checkout.config_changes == ()
        assert (
            subprocess.run(
                ["git", "-C", str(checkout.root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            == ""
        )

    assert not destination.exists()


def test_baseline_checkout_rejects_a_clean_post_replay_commit_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches replay attribution surviving a clean reset to another commit."""

    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    destination = tmp_path / "baseline"
    current = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current != registry.base_commit
    monkeypatch.setattr(
        "research.ablation_registry.validate_ablation_registry",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "research.ablation_registry.source_fingerprint",
        lambda _root: registry.source_sha256,
    )

    with (
        pytest.raises(ValueError, match="changed during replay"),
        isolated_baseline_checkout(
            registry,
            source_root=ROOT,
            destination=destination,
        ) as checkout,
    ):
        subprocess.run(
            ["git", "-C", str(checkout.root), "reset", "--hard", current],
            check=True,
            capture_output=True,
            text=True,
        )


def test_partial_ablation_worktree_add_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a partial Git add bypassing isolated-checkout recovery."""

    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    destination = tmp_path / "partial"
    removals: list[Path] = []
    monkeypatch.setattr(
        ablation_registry_module,
        "validate_ablation_registry",
        lambda *_args, **_kwargs: None,
    )

    def git(root: Path, arguments: tuple[str, ...], **_kwargs: object) -> str:
        if arguments[:2] == ("worktree", "add"):
            destination.mkdir(parents=True)
            raise RuntimeError("injected partial add")
        if arguments[:2] == ("worktree", "remove"):
            removals.append(Path(arguments[-1]))
            shutil.rmtree(destination)
            return ""
        raise AssertionError((root, arguments))

    monkeypatch.setattr(ablation_registry_module, "_git", git)

    with (
        pytest.raises(RuntimeError, match="partial add"),
        isolated_baseline_checkout(
            registry,
            source_root=ROOT,
            destination=destination,
        ),
    ):
        pytest.fail("partial checkout must not yield")

    assert removals == [destination.resolve()]


def test_hashed_first_divergence_is_required_and_stage_ordered() -> None:
    """Catches no-op experiments and a downstream stage reported before its cause."""
    runner = _runner_module()
    baseline = {
        "phase1_performance/a/h1_2023": [
            {
                "date": "2023-01-03",
                "stages": {
                    "reference_context": "a" * 64,
                    "leaders": "b" * 64,
                    "risk": "c" * 64,
                    "opportunity": "d" * 64,
                    "targets": "e" * 64,
                    "orders": "f" * 64,
                    "fills": "0" * 64,
                },
            }
        ]
    }
    variant = copy.deepcopy(baseline)
    variant["phase1_performance/a/h1_2023"][0]["stages"]["risk"] = "1" * 64
    variant["phase1_performance/a/h1_2023"][0]["stages"]["orders"] = "2" * 64

    divergence = runner._first_hashed_divergence(baseline, variant, require=True)

    assert divergence == {
        "cell_id": "phase1_performance/a/h1_2023",
        "date": "2023-01-03",
        "changed_fields": ["risk", "orders"],
        "first_stage": "risk",
        "baseline_stage_sha256": baseline["phase1_performance/a/h1_2023"][0]["stages"],
        "variant_stage_sha256": variant["phase1_performance/a/h1_2023"][0]["stages"],
    }
    with pytest.raises(ValueError, match="no behavior divergence"):
        runner._first_hashed_divergence(baseline, baseline, require=True)

    early = copy.deepcopy(baseline)
    early["phase1_performance/a/h1_2023"][0]["date"] = "2023-01-02"
    early_variant = copy.deepcopy(early)
    early_variant["phase1_performance/a/h1_2023"][0]["stages"]["risk"] = "3" * 64
    late = copy.deepcopy(baseline)
    late["phase1_performance/a/h1_2023"][0]["date"] = "2024-01-02"
    late_variant = copy.deepcopy(late)
    late_variant["phase1_performance/a/h1_2023"][0]["stages"]["risk"] = "4" * 64
    across_cells = {
        "late": late["phase1_performance/a/h1_2023"],
        "early": early["phase1_performance/a/h1_2023"],
    }
    across_variants = {
        "late": late_variant["phase1_performance/a/h1_2023"],
        "early": early_variant["phase1_performance/a/h1_2023"],
    }
    assert (
        runner._first_hashed_divergence(
            across_cells,
            across_variants,
            require=True,
        )["date"]
        == "2023-01-02"
    )


def test_worker_cell_replays_real_production_with_raw_dimensions_and_trace(
    data_dir: Path,
) -> None:
    """Catches synthetic metrics, missing concentration, or a mock decision trace."""
    runner = _runner_module()

    result = runner._replay_cell(
        ProductionEngine(data_dir),
        symbols=("sz300308", "sz300394", "sz300502"),
        start="2026-06-25",
        end="2026-07-03",
        acute_start="2026-06-25",
        acute_end="2026-07-03",
    )

    assert set(result) == {"metrics", "trace", "replay_error", "raw_result_sha256"}
    assert result["replay_error"] is None
    assert set(result["metrics"]) == {
        "final_wealth",
        "max_drawdown",
        "account_orders",
        "acute_return",
        "gross_turnover",
        "annual_turnover",
        "top1_concentration",
        "top3_concentration",
        "pnl_hhi",
    }
    assert result["metrics"]["final_wealth"] > 0
    assert 0 <= result["metrics"]["max_drawdown"] <= 1
    assert result["trace"]
    assert result["trace"][0]["date"] >= "2023-01-01"
    assert set(result["trace"][0]["stages"]) == {
        "reference_context",
        "leaders",
        "risk",
        "opportunity",
        "targets",
        "orders",
        "fills",
    }
    assert all(len(value) == 64 for value in result["trace"][0]["stages"].values())
    assert len(result["raw_result_sha256"]) == 64


def test_worker_cell_retains_exact_failure_date_and_partial_trace() -> None:
    """Catches lost production exceptions or guessed cell-level failure dates."""
    runner = _runner_module()

    class ExecutionProbe:
        def execute_open(self, *, date, account, panel) -> None:
            return None

    class FailingEngine:
        def __init__(self) -> None:
            self.execution = ExecutionProbe()

        def decide(self, *, symbols, as_of, account):
            raise RuntimeError("exact production failure")

        def backtest(self, *, symbols, start, end):
            from uquant.types import AccountState

            account = AccountState.empty(2_000_000.0)
            self.execution.execute_open(date="2025-08-25", account=account, panel={})
            self.decide(symbols=symbols, as_of="2025-08-25", account=account)

    result = runner._replay_cell(
        FailingEngine(),
        symbols=("sh688041",),
        start="2025-08-24",
        end="2025-08-26",
    )

    assert result == {
        "metrics": None,
        "trace": [],
        "replay_error": {
            "type": "RuntimeError",
            "message": "exact production failure",
            "date": "2025-08-25",
        },
        "raw_result_sha256": None,
    }


def test_worker_comparison_emits_per_cell_aggregate_and_first_divergence() -> None:
    """Catches a result schema that loses Task-8 inputs or labels conclusions early."""
    runner = _runner_module()
    baseline_metrics = _metrics(
        wealth=2.0,
        drawdown=0.2,
        orders=10,
        acute=-0.1,
        turnover=1.0,
        top1=0.7,
        top3=0.9,
        hhi=0.5,
    ).to_dict()
    variant_metrics = _metrics(
        wealth=1.8,
        drawdown=0.18,
        orders=8,
        acute=-0.05,
        turnover=0.8,
        top1=0.6,
        top3=0.8,
        hhi=0.4,
    ).to_dict()
    stages = {name: "a" * 64 for name in runner._CAUSAL_STAGES}
    changed_stages = dict(stages)
    changed_stages["targets"] = "b" * 64
    baseline = {
        "cells": [
            {
                "contract": "phase1_performance",
                "cell_id": "a/h1_2023",
                "status": "VALID",
                "metrics": baseline_metrics,
                "raw_result_sha256": "c" * 64,
            }
        ],
        "traces": {"phase1_performance/a/h1_2023": [{"date": "2023-01-03", "stages": stages}]},
        "aggregates": {},
        "provenance": {"effective_config_sha256": "d" * 64},
    }
    variant = copy.deepcopy(baseline)
    variant["cells"][0]["metrics"] = variant_metrics
    variant["cells"][0]["raw_result_sha256"] = "e" * 64
    variant["traces"]["phase1_performance/a/h1_2023"][0]["stages"] = changed_stages
    variant["provenance"]["effective_config_sha256"] = "f" * 64

    compared = runner._compare_worker_payloads(baseline, variant)

    assert compared["first_divergence"]["first_stage"] == "targets"
    assert compared["cells"][0]["delta"]["final_wealth"] == pytest.approx(-0.2)
    assert compared["cells"][0]["delta"]["max_drawdown"] == pytest.approx(-0.02)
    assert compared["cells"][0]["delta"]["account_orders"] == -2
    assert compared["cells"][0]["delta"]["acute_return"] == pytest.approx(0.05)
    assert compared["aggregates"]["phase1_performance"]["baseline"]["median_final_wealth"] == pytest.approx(
        2.0
    )
    assert compared["aggregates"]["phase1_performance"]["variant"]["median_final_wealth"] == pytest.approx(
        1.8
    )
    assert "classification" not in json.dumps(compared)
    assert "decision" not in json.dumps(compared)


def test_worker_comparison_retains_variant_failure_without_hiding_it_from_coverage() -> None:
    """Catches abort-on-error, error-tail omission, or failed-cell metric fabrication."""
    runner = _runner_module()
    metrics = _metrics(
        wealth=2.0,
        drawdown=0.2,
        orders=10,
        acute=-0.1,
        turnover=1.0,
        top1=0.7,
        top3=0.9,
        hhi=0.5,
    ).to_dict()
    stages = {name: "a" * 64 for name in runner._CAUSAL_STAGES}
    changed_stages = dict(stages)
    changed_stages["risk"] = "b" * 64
    error = {
        "type": "RuntimeError",
        "message": "incompatible attribution",
        "date": "2025-08-25",
        "contract": "phase1_performance",
        "cell_id": "a/h1_2023",
        "binding_sha256": "c" * 64,
        "carrier_sha256": "d" * 64,
        "provenance_sha256": "e" * 64,
    }
    baseline = {
        "cells": [
            {
                "contract": "phase1_performance",
                "cell_id": "a/h1_2023",
                "frozen_status": "VALID",
                "status": "VALID",
                "metrics": metrics,
                "replay_error": None,
                "raw_result_sha256": "f" * 64,
            },
            {
                "contract": "phase1_performance",
                "cell_id": "a/h2_2023",
                "frozen_status": "VALID",
                "status": "VALID",
                "metrics": metrics,
                "replay_error": None,
                "raw_result_sha256": "1" * 64,
            },
        ],
        "traces": {
            "phase1_performance/a/h1_2023": [
                {"date": "2025-08-24", "stages": stages},
                {"date": "2025-08-25", "stages": stages},
            ],
            "phase1_performance/a/h2_2023": [{"date": "2025-08-24", "stages": stages}],
        },
        "provenance": {"effective_config_sha256": "2" * 64},
    }
    variant = copy.deepcopy(baseline)
    variant["cells"][0].update(
        status="REPLAY_ERROR",
        metrics=None,
        replay_error=error,
        raw_result_sha256=None,
    )
    variant["traces"]["phase1_performance/a/h1_2023"] = [{"date": "2025-08-24", "stages": changed_stages}]

    compared = runner._compare_worker_payloads(baseline, variant)

    failed = compared["cells"][0]
    assert failed["baseline_status"] == "VALID"
    assert failed["variant_status"] == "REPLAY_ERROR"
    assert failed["status_transition"] == "VALID->REPLAY_ERROR"
    assert failed["baseline_metrics"] == metrics
    assert failed["variant_metrics"] is None
    assert failed["delta"] is None
    assert failed["variant_replay_error"] == error
    assert compared["execution_pass"] is False
    assert compared["first_divergence"]["date"] == "2025-08-24"
    aggregate = compared["aggregates"]["phase1_performance"]
    assert aggregate["baseline"]["economic_cells"] == 1
    assert aggregate["variant"]["economic_cells"] == 1
    assert aggregate["coverage"] == {
        "record_count": 2,
        "economic_count": 2,
        "common_valid_count": 1,
        "baseline_status_counts": {"VALID": 2},
        "variant_status_counts": {"REPLAY_ERROR": 1, "VALID": 1},
        "status_transition_counts": {"VALID->REPLAY_ERROR": 1},
    }


def test_validation_runner_proves_every_carrier_and_is_deterministic(tmp_path: Path) -> None:
    """Catches undocumented carriers, runtime drift, and nondeterministic rerun evidence."""
    source_root = _current_runner_with_reviewed_production_checkout(
        tmp_path / "reviewed-source"
    )
    script = source_root / "scripts" / "run_phase2_ablation.py"
    registry_path = source_root / MINIMAL_ABLATION_REGISTRY_PATH.relative_to(ROOT)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    command = [
        sys.executable,
        str(script),
        "validate",
        "--source-root",
        str(source_root),
        "--registry",
        str(registry_path),
    ]
    first_run = subprocess.run(
        [*command, "--output", str(first)],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    second_run = subprocess.run(
        [*command, "--output", str(second)],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first_run.returncode == second_run.returncode == 0, first_run.stderr
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["mode"] == "carrier-validation"
    assert payload["passed"] is True
    assert "classification" not in payload
    assert "decision" not in payload
    assert payload["registry_sha256"] == load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH).payload_sha256
    assert payload["source"]["base_commit"] == "e5e0fa903c9a9b26701063ae01f352af3e246a7d"
    assert payload["deleted_subsystems"] == ["transition_overlay"]
    assert payload["contracts"] == {
        "phase1_performance": {
            "economic_count": 45,
            "record_count": 45,
            "status_counts": {"VALID": 45},
        },
        "ai_era_generalization": {
            "economic_count": 192,
            "record_count": 234,
            "status_counts": {
                "INSUFFICIENT_SAMPLE": 42,
                "REPLAY_ERROR": 1,
                "VALID": 191,
            },
        },
    }
    assert len(payload["experiments"]) == 12
    assert all(item["checkout"]["clean"] for item in payload["experiments"])
    assert all(len(item["effective_config_sha256"]) == 64 for item in payload["experiments"])
    assert all(
        item["replay_command"][-2:] == ["--experiment", item["experiment_id"]]
        for item in payload["experiments"]
    )
    assert payload["provenance"]["data"]["snapshot_id"] == ("20260809T094222Z-causal-tech-index-rebase")
    assert len(payload["provenance"]["runner_sha256"]) == 64
    assert len(payload["provenance"]["uv_lock_sha256"]) == 64
    assert payload["provenance"]["runtime"]["python_full_version"]
    assert payload["provenance"]["runtime"]["numpy_version"]
    assert payload["provenance"]["runtime"]["pandas_version"]


def test_worker_payload_requires_exact_schedule_status_and_trace_coverage() -> None:
    """Catches partial workers, frozen-status rewrites, or missing causal evidence."""
    runner = _runner_module()
    schedule = (
        ContractCell(
            contract="phase1_performance",
            cell_id="a/h1_2023",
            status="VALID",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2023-01-04",
        ),
        ContractCell(
            contract="ai_era_generalization",
            cell_id="h1_2023/random__05__0000",
            status="REPLAY_ERROR",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2023-06-30",
        ),
        ContractCell(
            contract="ai_era_generalization",
            cell_id="h1_2023/remove_all_leaders",
            status="INSUFFICIENT_SAMPLE",
            economic=False,
            symbols=(),
            start="2023-01-03",
            end="2023-06-30",
        ),
    )
    metrics = _metrics(
        wealth=1.0,
        drawdown=0.1,
        orders=2,
        acute=None,
        turnover=0.5,
        top1=0.4,
        top3=0.6,
        hhi=0.2,
    ).to_dict()
    stages = {name: "a" * 64 for name in runner._CAUSAL_STAGES}
    provenance = {"effective_config_sha256": "d" * 64}
    provenance_sha256 = runner._sha256_mapping(provenance)
    error = {
        "type": "RuntimeError",
        "message": "known",
        "date": "2023-01-03",
        "contract": "ai_era_generalization",
        "cell_id": "h1_2023/random__05__0000",
        "binding_sha256": "b" * 64,
        "carrier_sha256": runner._BASELINE_CARRIER_SHA256,
        "provenance_sha256": provenance_sha256,
    }
    payload = {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": "b" * 64,
        "experiment_id": "baseline",
        "cells": [
            {
                "contract": "phase1_performance",
                "cell_id": "a/h1_2023",
                "frozen_status": "VALID",
                "status": "VALID",
                "economic": True,
                "metrics": metrics,
                "replay_error": None,
                "raw_result_sha256": "c" * 64,
            },
            {
                "contract": "ai_era_generalization",
                "cell_id": "h1_2023/random__05__0000",
                "frozen_status": "REPLAY_ERROR",
                "status": "REPLAY_ERROR",
                "economic": True,
                "metrics": None,
                "replay_error": error,
                "raw_result_sha256": None,
            },
            {
                "contract": "ai_era_generalization",
                "cell_id": "h1_2023/remove_all_leaders",
                "frozen_status": "INSUFFICIENT_SAMPLE",
                "status": "INSUFFICIENT_SAMPLE",
                "economic": False,
                "metrics": None,
                "replay_error": None,
                "raw_result_sha256": None,
            },
        ],
        "traces": {
            "phase1_performance/a/h1_2023": [{"date": "2023-01-03", "stages": stages}],
            "ai_era_generalization/h1_2023/random__05__0000": [],
        },
        "provenance": provenance,
    }

    runner._validate_worker_payload(
        payload,
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="baseline",
    )
    runner._validate_worker_payload(
        json.loads(runner._canonical_bytes(payload)),
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="baseline",
    )
    rounded_concentration = copy.deepcopy(payload)
    rounded_concentration["cells"][0]["metrics"]["top3_concentration"] = 1.0000000000000002
    runner._validate_worker_payload(
        rounded_concentration,
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="baseline",
    )

    partial = copy.deepcopy(payload)
    partial["cells"].pop()
    with pytest.raises(ValueError, match="coverage"):
        runner._validate_worker_payload(
            partial,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
        )
    rewritten = copy.deepcopy(payload)
    rewritten["cells"][1]["status"] = "VALID"
    rewritten["cells"][1]["metrics"] = metrics
    rewritten["cells"][1]["replay_error"] = None
    rewritten["cells"][1]["raw_result_sha256"] = "e" * 64
    with pytest.raises(ValueError, match="status"):
        runner._validate_worker_payload(
            rewritten,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
        )
    variant = copy.deepcopy(payload)
    variant["experiment_id"] = "without_capital_budget_ladder"
    variant["cells"][0].update(
        status="REPLAY_ERROR",
        metrics=None,
        replay_error={
            **error,
            "contract": "phase1_performance",
            "cell_id": "a/h1_2023",
            "carrier_sha256": "f" * 64,
        },
        raw_result_sha256=None,
    )
    variant["cells"][1]["replay_error"]["carrier_sha256"] = "f" * 64
    variant["traces"]["phase1_performance/a/h1_2023"] = []
    runner._validate_worker_payload(
        variant,
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="without_capital_budget_ladder",
        carrier_sha256="f" * 64,
    )
    missing_error_field = copy.deepcopy(variant)
    del missing_error_field["cells"][0]["replay_error"]["date"]
    with pytest.raises(ValueError, match="replay error evidence"):
        runner._validate_worker_payload(
            missing_error_field,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="without_capital_budget_ladder",
            carrier_sha256="f" * 64,
        )
    rewritten_frozen_status = copy.deepcopy(variant)
    rewritten_frozen_status["cells"][0]["frozen_status"] = "REPLAY_ERROR"
    with pytest.raises(ValueError, match="frozen contract"):
        runner._validate_worker_payload(
            rewritten_frozen_status,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="without_capital_budget_ladder",
            carrier_sha256="f" * 64,
        )
    self_signed_error = copy.deepcopy(variant)
    self_signed_error["cells"][0]["replay_error"]["carrier_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="replay error provenance"):
        runner._validate_worker_payload(
            self_signed_error,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="without_capital_budget_ladder",
            carrier_sha256="f" * 64,
        )
    missing_trace = copy.deepcopy(payload)
    missing_trace["traces"] = {}
    with pytest.raises(ValueError, match="trace coverage"):
        runner._validate_worker_payload(
            missing_trace,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
        )
    invalid_metrics = copy.deepcopy(payload)
    invalid_metrics["cells"][0]["metrics"]["top1_concentration"] = 0.9
    invalid_metrics["cells"][0]["metrics"]["top3_concentration"] = 0.8
    with pytest.raises(ValueError, match="concentration"):
        runner._validate_worker_payload(
            invalid_metrics,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
        )
    invalid_upper_bound = copy.deepcopy(payload)
    invalid_upper_bound["cells"][0]["metrics"]["top3_concentration"] = 1.000001
    with pytest.raises(ValueError, match="concentration"):
        runner._validate_worker_payload(
            invalid_upper_bound,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
        )


def test_atomic_checkpoint_is_content_addressed_and_rejects_stale_or_mutated(
    tmp_path: Path,
) -> None:
    """Catches torn, edited, or cross-run checkpoint reuse."""
    runner = _runner_module()
    checkpoint = tmp_path / "checkpoint.json"
    payload = {
        "schema_version": 1,
        "kind": "baseline",
        "binding_sha256": "a" * 64,
        "value": 1,
    }

    digest = runner._write_checkpoint(checkpoint, payload)

    assert len(digest) == 64
    assert (
        runner._read_checkpoint(
            checkpoint,
            binding_sha256="a" * 64,
            kind="baseline",
        )
        == payload
    )
    canonical = checkpoint.read_text(encoding="utf-8")
    checkpoint.write_text(
        canonical.replace(
            '"schema_version":1,',
            '"schema_version":1,"schema_version":1,',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="canonical"):
        runner._read_checkpoint(
            checkpoint,
            binding_sha256="a" * 64,
            kind="baseline",
        )

    runner._write_checkpoint(checkpoint, payload)
    stale = json.loads(checkpoint.read_text(encoding="utf-8"))
    stale["payload"]["binding_sha256"] = "b" * 64
    checkpoint.write_bytes(runner._canonical_bytes(stale) + b"\n")
    with pytest.raises(ValueError, match="content hash"):
        runner._read_checkpoint(
            checkpoint,
            binding_sha256="a" * 64,
            kind="baseline",
        )

    runner._write_checkpoint(checkpoint, payload)
    with pytest.raises(ValueError, match="stale"):
        runner._read_checkpoint(
            checkpoint,
            binding_sha256="b" * 64,
            kind="baseline",
        )


def test_final_evidence_requires_all_13_exact_one_carrier_checkpoints() -> None:
    """Catches partial aggregation and cross-experiment variant reuse."""
    runner = _runner_module()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    binding = "a" * 64
    schedule = (
        ContractCell(
            contract="phase1_performance",
            cell_id="a/h1_2023",
            status="VALID",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2023-01-04",
        ),
    )
    metrics = _metrics(
        wealth=1.0,
        drawdown=0.1,
        orders=2,
        acute=None,
        turnover=0.5,
        top1=0.4,
        top3=0.6,
        hhi=0.2,
    ).to_dict()
    checkpoints = {
        experiment.experiment_id: {
            "schema_version": 1,
            "kind": "experiment",
            "binding_sha256": binding,
            "experiment_id": experiment.experiment_id,
            "subsystem": experiment.subsystem,
            "carrier_sha256": experiment.carrier.sha256,
            "worker_payload_sha256": f"{index + 1:064x}",
            "execution_pass": True,
            "comparison": {
                "first_divergence": {
                    "cell_id": "phase1_performance/a/h1_2023",
                    "date": "2023-01-03",
                    "first_stage": "risk",
                },
                "cells": [
                    {
                        "contract": "phase1_performance",
                        "cell_id": "a/h1_2023",
                        "frozen_status": "VALID",
                        "baseline_status": "VALID",
                        "variant_status": "VALID",
                        "status_transition": None,
                        "baseline_metrics": metrics,
                        "variant_metrics": metrics,
                        "delta": {
                            "final_wealth": 0.1,
                            "max_drawdown": 0.0,
                            "account_orders": 0,
                            "acute_return": None,
                            "gross_turnover": 0.0,
                            "annual_turnover": 0.0,
                            "top1_concentration": 0.0,
                            "top3_concentration": 0.0,
                            "pnl_hhi": 0.0,
                        },
                        "baseline_replay_error": None,
                        "variant_replay_error": None,
                        "baseline_raw_result_sha256": "b" * 64,
                        "variant_raw_result_sha256": "c" * 64,
                    }
                ],
                "aggregates": {
                    "phase1_performance": {
                        "baseline": {"economic_cells": 1},
                        "variant": {"economic_cells": 1},
                        "delta": {"economic_cells": 0},
                        "coverage": {
                            "record_count": 1,
                            "economic_count": 1,
                            "common_valid_count": 1,
                            "baseline_status_counts": {"VALID": 1},
                            "variant_status_counts": {"VALID": 1},
                            "status_transition_counts": {},
                        },
                    }
                },
                "execution_pass": True,
                "baseline_provenance": {"source": "baseline"},
                "variant_provenance": {"source": experiment.experiment_id},
            },
            "replay_command": ["python", "run_phase2_ablation.py"],
        }
        for index, experiment in enumerate(registry.experiments)
    }

    ordered = runner._validate_experiment_checkpoints(
        registry,
        checkpoints,
        binding_sha256=binding,
        schedule=schedule,
    )

    assert [item["experiment_id"] for item in ordered] == [
        experiment.experiment_id for experiment in registry.experiments
    ]
    partial = dict(checkpoints)
    partial.pop(registry.experiments[-1].experiment_id)
    with pytest.raises(ValueError, match="13/13"):
        runner._validate_experiment_checkpoints(
            registry,
            partial,
            binding_sha256=binding,
            schedule=schedule,
        )
    reused = copy.deepcopy(checkpoints)
    first, second = registry.experiments[:2]
    reused[second.experiment_id]["carrier_sha256"] = first.carrier.sha256
    with pytest.raises(ValueError, match="carrier"):
        runner._validate_experiment_checkpoints(
            registry,
            reused,
            binding_sha256=binding,
            schedule=schedule,
        )
    missing_cell = copy.deepcopy(checkpoints)
    missing_cell[first.experiment_id]["comparison"]["cells"] = []
    with pytest.raises(ValueError, match="cell coverage"):
        runner._validate_experiment_checkpoints(
            registry,
            missing_cell,
            binding_sha256=binding,
            schedule=schedule,
        )
    missing_dimension = copy.deepcopy(checkpoints)
    del missing_dimension[first.experiment_id]["comparison"]["cells"][0]["delta"]["pnl_hhi"]
    with pytest.raises(ValueError, match="delta dimensions"):
        runner._validate_experiment_checkpoints(
            registry,
            missing_dimension,
            binding_sha256=binding,
            schedule=schedule,
        )


def test_raw_backed_checkpoint_recomputes_real_comparison_after_reseal(tmp_path: Path) -> None:
    """Catches a self-signed checkpoint whose claimed deltas differ from its raw workers."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[0]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    _, variant = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id=experiment.experiment_id,
        carrier_sha256=experiment.carrier.sha256,
        stage_hash="2" * 64,
        wealth=1.1,
    )
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    experiment_path = runner._write_experiment_result(
        checkpoint_dir=tmp_path,
        experiment=experiment,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        variant_worker=variant,
        replay_command=replay_command,
        expected_variant_provenance=variant["provenance"],
        frozen_replay_errors={},
    )

    envelope = json.loads(experiment_path.read_text(encoding="utf-8"))
    authentic_envelope = copy.deepcopy(envelope)
    envelope["payload"]["comparison"]["cells"][0]["delta"]["final_wealth"] = 999.0
    envelope["payload_sha256"] = runner._sha256_mapping(envelope["payload"])
    experiment_path.write_bytes(runner._canonical_bytes(envelope) + b"\n")

    with pytest.raises(ValueError, match="recomputed comparison"):
        runner._read_experiment_result(
            experiment_path,
            checkpoint_dir=tmp_path,
            experiment=experiment,
            binding_sha256=binding_sha256,
            schedule=schedule,
            baseline_checkpoint=baseline_checkpoint,
            baseline_worker=baseline_worker,
            expected_replay_command=replay_command,
            expected_baseline_provenance=baseline["provenance"],
            expected_variant_provenance=variant["provenance"],
            frozen_replay_errors={},
        )

    forged_variant = copy.deepcopy(variant)
    forged_variant["provenance"]["runtime"]["python_full_version"] = "3.99.0"
    forged_reference = runner._write_worker_artifact(tmp_path, forged_variant)
    forged_envelope = copy.deepcopy(authentic_envelope)
    forged_envelope["payload"]["variant_worker_artifact"] = forged_reference
    forged_envelope["payload"]["comparison"]["variant_provenance"] = forged_variant["provenance"]
    forged_envelope["payload_sha256"] = runner._sha256_mapping(forged_envelope["payload"])
    experiment_path.write_bytes(runner._canonical_bytes(forged_envelope) + b"\n")
    with pytest.raises(ValueError, match="checkout/config/data/runtime"):
        runner._read_experiment_result(
            experiment_path,
            checkpoint_dir=tmp_path,
            experiment=experiment,
            binding_sha256=binding_sha256,
            schedule=schedule,
            baseline_checkpoint=baseline_checkpoint,
            baseline_worker=baseline_worker,
            expected_replay_command=replay_command,
            expected_baseline_provenance=baseline["provenance"],
            expected_variant_provenance=variant["provenance"],
            frozen_replay_errors={},
        )


def test_no_divergence_writes_authenticated_invalid_artifact(tmp_path: Path) -> None:
    """Catches dependence on an external watcher for complete no-divergence workers."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[4]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    variant = copy.deepcopy(baseline)
    variant["experiment_id"] = experiment.experiment_id
    variant["provenance"] = {
        **baseline["provenance"],
        "checkout": {"carrier_sha256": experiment.carrier.sha256},
    }
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )

    result_path = runner._write_experiment_result(
        checkpoint_dir=tmp_path,
        experiment=experiment,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        variant_worker=variant,
        replay_command=replay_command,
        expected_variant_provenance=variant["provenance"],
        frozen_replay_errors={},
    )

    assert result_path == tmp_path / "invalid" / f"{experiment.experiment_id}.json"
    assert not (tmp_path / f"{experiment.experiment_id}.json").exists()
    invalid = runner._read_experiment_result(
        result_path,
        checkpoint_dir=tmp_path,
        experiment=experiment,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        expected_replay_command=replay_command,
        expected_baseline_provenance=baseline["provenance"],
        expected_variant_provenance=variant["provenance"],
        frozen_replay_errors={},
    )
    assert invalid["kind"] == "invalid_experiment"
    assert invalid["reason"] == "no_behavior_divergence"
    assert invalid["comparison"]["first_divergence"] is None
    assert invalid["coverage_complete"] is True


def test_aggregate_authenticates_invalid_results_without_claiming_complete() -> None:
    """Catches hiding invalid experiments from coverage or counting them as valid results."""
    runner = _runner_module()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    valid = {
        item.experiment_id: {
            "experiment_id": item.experiment_id,
            "kind": "experiment",
            "variant_worker_artifact": {"payload_sha256": f"{index + 1:064x}"},
        }
        for index, item in enumerate(registry.experiments[:11])
    }
    invalid = {
        item.experiment_id: {
            "experiment_id": item.experiment_id,
            "kind": "invalid_experiment",
            "reason": "no_behavior_divergence",
            "coverage_complete": True,
            "variant_worker_artifact": {"payload_sha256": f"{index + 12:064x}"},
        }
        for index, item in enumerate(registry.experiments[11:])
    }

    summary = runner._evidence_coverage(registry, valid=valid, invalid=invalid)

    assert summary["coverage_complete"] is True
    assert summary["complete"] is False
    assert summary["valid_experiment_count"] == 11
    assert summary["invalid_experiment_count"] == 2
    assert summary["missing_experiment_ids"] == []
    assert set(summary["invalid_experiments"]) == set(invalid)


def test_frozen_replay_error_anchor_rejects_resealed_message_mutation() -> None:
    """Catches preserving only the frozen REPLAY_ERROR status while rewriting its exception."""
    runner = _runner_module()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    anchors = runner._frozen_replay_error_anchors(registry, source_root=ROOT)
    identity = ("ai_era_generalization", "continuous_ai_era/random__20__0000")
    anchor = anchors[identity]
    schedule = (
        ContractCell(
            contract=identity[0],
            cell_id=identity[1],
            status="REPLAY_ERROR",
            economic=True,
            symbols=("sz300308",),
            start="2023-01-03",
            end="2026-08-05",
        ),
    )
    provenance = {"effective_config_sha256": "d" * 64}
    payload = {
        "schema_version": 1,
        "mode": "contract-replay",
        "binding_sha256": "b" * 64,
        "experiment_id": "baseline",
        "cells": [
            {
                "contract": identity[0],
                "cell_id": identity[1],
                "frozen_status": "REPLAY_ERROR",
                "status": "REPLAY_ERROR",
                "economic": True,
                "metrics": None,
                "replay_error": {
                    **anchor,
                    "contract": identity[0],
                    "cell_id": identity[1],
                    "binding_sha256": "b" * 64,
                    "carrier_sha256": runner._BASELINE_CARRIER_SHA256,
                    "provenance_sha256": runner._sha256_mapping(provenance),
                },
                "raw_result_sha256": None,
            }
        ],
        "traces": {f"{identity[0]}/{identity[1]}": []},
        "provenance": provenance,
    }
    runner._validate_worker_payload(
        payload,
        schedule=schedule,
        binding_sha256="b" * 64,
        experiment_id="baseline",
        frozen_replay_errors=anchors,
    )
    rewritten = copy.deepcopy(payload)
    rewritten["cells"][0]["replay_error"]["message"] += " rewritten"
    with pytest.raises(ValueError, match="frozen replay error anchor"):
        runner._validate_worker_payload(
            rewritten,
            schedule=schedule,
            binding_sha256="b" * 64,
            experiment_id="baseline",
            frozen_replay_errors=anchors,
        )


def test_replay_command_materializes_exact_historical_evidence_commit(tmp_path: Path) -> None:
    """Catches later report-only HEADs replaying from the wrong source checkout."""
    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    command = runner._replay_command(
        repository_root=ROOT,
        evidence_commit=evidence_commit,
        registry_relative=Path("artifacts/phase2/ablations/registry.json"),
        data_dir=ROOT / "data" / "frozen",
        experiment_id="without_sector_guard",
        checkpoint_dir=tmp_path,
        output=tmp_path / "progress.json",
    )
    assert command[2] == "replay"
    assert command[command.index("--evidence-commit") + 1] == evidence_commit
    with runner._isolated_evidence_checkout(ROOT, evidence_commit) as checkout:
        assert runner._git_output(checkout, "rev-parse", "HEAD") == evidence_commit
        assert runner._git_output(checkout, "status", "--porcelain", "--untracked-files=all") == ""
    wrong_commit = list(command)
    wrong_commit[wrong_commit.index("--evidence-commit") + 1] = "0" * 40
    with pytest.raises(ValueError, match="evidence commit"):
        runner._validate_replay_command(wrong_commit, expected=command)


def test_evidence_checkout_rejects_a_clean_post_replay_commit_switch(
    tmp_path: Path,
) -> None:
    """Catches historical evidence attributed after a clean checkout reset."""

    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    current = runner._git_output(ROOT, "rev-parse", "HEAD")
    assert current != evidence_commit

    with (
        pytest.raises(ValueError, match="changed during replay"),
        runner._isolated_evidence_checkout(ROOT, evidence_commit) as checkout,
    ):
        subprocess.run(
            ["git", "-C", str(checkout), "reset", "--hard", current],
            check=True,
            capture_output=True,
            text=True,
        )


def test_evidence_checkout_cleanup_does_not_relabel_a_replay_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches cleanup spawn failures masking or relabeling the caller's error."""

    runner = _runner_module()
    evidence_commit = "9592fcca3860d1901a7009d799d29d20959d1699"
    original_run = runner.subprocess.run

    with (
        pytest.raises(OSError, match="caller replay failure"),
        runner._isolated_evidence_checkout(ROOT, evidence_commit),
    ):
        def fail_remove(command: list[str], **kwargs: object) -> object:
            if "remove" in command:
                raise OSError("cleanup spawn failure")
            return original_run(command, **kwargs)

        monkeypatch.setattr(runner.subprocess, "run", fail_remove)
        raise OSError("caller replay failure")


@pytest.mark.parametrize("mutation", ["economics", "trace"])
def test_manifest_anchor_rejects_fully_resealed_worker_attack(
    tmp_path: Path,
    mutation: str,
) -> None:
    """Catches a raw worker, comparison, and checkpoint that are re-signed together."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[0]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    _, variant = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id=experiment.experiment_id,
        carrier_sha256=experiment.carrier.sha256,
        stage_hash="2" * 64,
        wealth=1.1,
    )
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    experiment_path = runner._write_experiment_result(
        checkpoint_dir=tmp_path,
        experiment=experiment,
        binding_sha256=binding_sha256,
        schedule=schedule,
        baseline_checkpoint=baseline_checkpoint,
        baseline_worker=baseline_worker,
        variant_worker=variant,
        replay_command=replay_command,
        expected_variant_provenance=variant["provenance"],
        frozen_replay_errors={},
    )
    authentic_envelope = json.loads(experiment_path.read_text(encoding="utf-8"))
    authentic_payload = authentic_envelope["payload"]
    authentic_raw_path = tmp_path / authentic_payload["variant_worker_artifact"]["path"]
    trusted_entry = {
        "experiment_id": experiment.experiment_id,
        "artifact": {
            "path": experiment_path.name,
            "kind": "experiment",
            "file_sha256": hashlib.sha256(experiment_path.read_bytes()).hexdigest(),
            "payload_sha256": authentic_envelope["payload_sha256"],
        },
        "raw": {
            "path": authentic_raw_path.relative_to(tmp_path).as_posix(),
            "file_sha256": hashlib.sha256(authentic_raw_path.read_bytes()).hexdigest(),
            "canonical_worker_sha256": hashlib.sha256(runner._canonical_bytes(variant)).hexdigest(),
        },
    }

    forged = copy.deepcopy(variant)
    if mutation == "economics":
        forged["cells"][0]["metrics"]["final_wealth"] += 123.0
        assert forged["cells"][0]["raw_result_sha256"] == variant["cells"][0]["raw_result_sha256"]
    else:
        forged["traces"]["phase1_performance/a/h1_2023"][0]["stages"]["risk"] = "3" * 64
    forged_reference = runner._write_worker_artifact(tmp_path, forged)
    forged_payload = copy.deepcopy(authentic_payload)
    forged_payload["variant_worker_artifact"] = forged_reference
    forged_payload["comparison"] = runner._compare_worker_payloads(
        baseline_worker,
        forged,
        require_divergence=False,
    )
    forged_payload["execution_pass"] = forged_payload["comparison"]["execution_pass"]
    runner._write_checkpoint(experiment_path, forged_payload)

    with pytest.raises(ValueError, match=r"evidence manifest .* hash differs"):
        runner._validate_evidence_manifest_entry(tmp_path, trusted_entry)


def test_tracked_manifest_rejects_edit_and_self_resign(tmp_path: Path) -> None:
    """Catches trusting a manifest hash recomputed from the edited manifest itself."""
    runner = _runner_module()
    manifest = runner._load_trusted_evidence_manifest()
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    compiled = runner._compile_evidence_manifest(
        manifest,
        registry=registry,
        evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
        binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    assert [row["experiment_id"] for row in compiled["entries"]] == [
        "baseline",
        *(item.experiment_id for item in registry.experiments),
    ]

    envelope = json.loads(runner._EVIDENCE_MANIFEST_PATH.read_text(encoding="utf-8"))
    envelope["payload"]["entries"][1]["raw"]["file_sha256"] = "0" * 64
    envelope["payload_sha256"] = hashlib.sha256(runner._canonical_bytes(envelope["payload"])).hexdigest()
    rewritten = tmp_path / "evidence_manifest.json"
    rewritten.write_bytes(runner._canonical_bytes(envelope))

    with pytest.raises(ValueError, match="trusted digest"):
        runner._load_trusted_evidence_manifest(rewritten)


def test_trusted_evidence_manifest_rejects_duplicate_keys(tmp_path: Path) -> None:
    """Catches last-key-wins JSON preserving a compiled canonical mapping digest."""

    runner = _runner_module()
    encoded = runner._EVIDENCE_MANIFEST_PATH.read_text(encoding="utf-8")
    duplicated = encoded.replace(
        '"schema_version": 1',
        '"schema_version": 999,\n  "schema_version": 1',
        1,
    )
    assert duplicated != encoded
    path = tmp_path / "duplicate-evidence-manifest.json"
    path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate key"):
        runner._load_trusted_evidence_manifest(path)


def test_historical_and_post_deletion_manifests_are_distinct_trust_roots() -> None:
    """Catches selecting or cross-accepting evidence from the other registry epoch."""
    runner = _runner_module()
    historical_path, historical_digest = runner._evidence_manifest_anchor(
        Path("artifacts/phase2/ablations/registry.json")
    )
    minimal_path, minimal_digest = runner._evidence_manifest_anchor(
        Path("artifacts/phase2/ablations/minimal_registry.json")
    )

    assert historical_path == runner._EVIDENCE_MANIFEST_PATH
    assert historical_digest == runner._EVIDENCE_MANIFEST_CANONICAL_SHA256
    assert minimal_path == runner._MINIMAL_EVIDENCE_MANIFEST_PATH
    assert minimal_digest == runner._MINIMAL_EVIDENCE_MANIFEST_CANONICAL_SHA256
    assert historical_path != minimal_path
    assert historical_digest != minimal_digest

    historical = runner._load_trusted_evidence_manifest(
        historical_path,
        trusted_digest=historical_digest,
    )
    minimal = runner._load_trusted_evidence_manifest(
        minimal_path,
        trusted_digest=minimal_digest,
    )
    historical_registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    minimal_registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    runner._compile_evidence_manifest(
        historical,
        registry=historical_registry,
        evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
        binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    runner._compile_evidence_manifest(
        minimal,
        registry=minimal_registry,
        evidence_commit="aa4b313e000002adae27b32f91b5a84425c78987",
        binding_sha256="bc5c82c7f3b3a0ba28f6965f90160aca4aa78d12fe6c375c20b4207cf653fb74",
        schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
    )
    with pytest.raises(ValueError, match="manifest binding differs"):
        runner._compile_evidence_manifest(
            minimal,
            registry=historical_registry,
            evidence_commit="9592fcca3860d1901a7009d799d29d20959d1699",
            binding_sha256="a009bf0e97499bc4bb40fc42e9e7e6999ea9f727492ab2ab4f86f2fc2ce34daf",
            schedule_sha256="0b68ec13f311563a473785989474d719dc892b0eeef887154fadea04cb25e70a",
        )


@pytest.mark.parametrize("root_kind", ["schema1", "malformed"])
def test_invalid_writer_rejects_any_existing_root_artifact(
    tmp_path: Path,
    root_kind: str,
) -> None:
    """Catches an invalid result coexisting with a legacy or malformed root artifact."""
    runner = _runner_module()
    experiment = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).experiments[4]
    binding_sha256 = "a" * 64
    replay_command = ["python", "runner.py", "replay", "--evidence-commit", "9" * 40]
    schedule, baseline = _single_cell_worker(
        runner,
        binding_sha256=binding_sha256,
        experiment_id="baseline",
        carrier_sha256=runner._BASELINE_CARRIER_SHA256,
        stage_hash="1" * 64,
    )
    variant = copy.deepcopy(baseline)
    variant["experiment_id"] = experiment.experiment_id
    variant["provenance"] = {
        **baseline["provenance"],
        "checkout": {"carrier_sha256": experiment.carrier.sha256},
    }
    baseline_path = runner._write_baseline_result(
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        worker=baseline,
        replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    baseline_checkpoint, baseline_worker = runner._read_baseline_result(
        baseline_path,
        checkpoint_dir=tmp_path,
        binding_sha256=binding_sha256,
        schedule=schedule,
        expected_replay_command=replay_command,
        expected_provenance=baseline["provenance"],
        frozen_replay_errors={},
    )
    root_path = tmp_path / f"{experiment.experiment_id}.json"
    if root_kind == "schema1":
        runner._write_checkpoint(
            root_path,
            {
                "schema_version": 1,
                "kind": "experiment",
                "binding_sha256": binding_sha256,
            },
        )
    else:
        root_path.write_bytes(b"{malformed")

    with pytest.raises(ValueError, match="both standard and invalid artifacts"):
        runner._write_experiment_result(
            checkpoint_dir=tmp_path,
            experiment=experiment,
            binding_sha256=binding_sha256,
            schedule=schedule,
            baseline_checkpoint=baseline_checkpoint,
            baseline_worker=baseline_worker,
            variant_worker=variant,
            replay_command=replay_command,
            expected_variant_provenance=variant["provenance"],
            frozen_replay_errors={},
        )


@pytest.mark.parametrize("root_kind", ["schema1", "malformed"])
def test_invalid_reader_rejects_any_existing_root_artifact(
    tmp_path: Path,
    root_kind: str,
) -> None:
    """Catches readback ignoring a legacy or malformed root artifact beside invalid."""
    runner = _runner_module()
    experiment_id = "without_challenger_scout"
    invalid_path = tmp_path / "invalid" / f"{experiment_id}.json"
    invalid_path.parent.mkdir()
    invalid_path.write_text("invalid artifact placeholder", encoding="utf-8")
    root_path = tmp_path / f"{experiment_id}.json"
    if root_kind == "schema1":
        runner._write_checkpoint(
            root_path,
            {
                "schema_version": 1,
                "kind": "experiment",
                "binding_sha256": "a" * 64,
            },
        )
    else:
        root_path.write_bytes(b"{malformed")

    with pytest.raises(ValueError, match="both standard and invalid artifacts"):
        runner._select_experiment_result_path(tmp_path, experiment_id)
