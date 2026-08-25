# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
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
    canonical_sha256,
    load_ablation_registry,
    source_fingerprint,
    validate_ablation_registry,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT / "artifacts" / "phase2" / "ablations" / "results.json"
REFERENCE_SOURCE_CONTRACT_PATH = (
    ROOT / "artifacts" / "phase2" / "ablations" / "post_task8_source_contract.json"
)


def _reviewed_source_commit() -> str:
    contract = json.loads(REFERENCE_SOURCE_CONTRACT_PATH.read_text(encoding="utf-8"))
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
    source = ROOT / "scripts" / "run_generalization_ablation.py"
    spec = importlib.util.spec_from_file_location("generalization_ablation_runner", source)
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
    """Catches mutating historical identity or retaining a deleted carrier in fresh evidence."""
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


def test_source_identity_source_does_not_rewrite_reviewed_generalization_contract() -> None:
    registry = load_ablation_registry(MINIMAL_ABLATION_REGISTRY_PATH)
    observed_source_sha256 = source_fingerprint(ROOT)

    with pytest.raises(ValueError, match="production source differs"):
        ablation_registry_module._validate_reference_source_contract(
            registry,
            root=ROOT,
            observed_source_sha256=observed_source_sha256,
        )


def test_reference_source_allowance_is_content_addressed_and_rejects_mutation(
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

    performance = registry.contract("phase1_performance")
    generalization = registry.contract("ai_era_generalization")
    assert performance.record_count == performance.economic_count == 45
    assert performance.minimum_date == "2023-01-03"
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
    """Catches dropped materiality inputs or premature KEEP/DELETE classification."""
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
        "REFERENCE_SOURCE_CONTRACT_PATH",
        invalid,
    )
    with pytest.raises(ValueError, match="cannot load post-anchor source contract"):
        ablation_registry_module._validate_reference_source_contract(
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



from _generalization_carrier_worker_cases import (
    test_fixed_schedule_is_complete_deterministic_and_preserves_known_status,
    test_carrier_materializes_in_an_isolated_clean_content_addressed_checkout,
    test_baseline_materializes_as_exact_isolated_clean_source,
    test_baseline_checkout_rejects_a_clean_post_replay_commit_switch,
    test_partial_ablation_worktree_add_is_cleaned_up,
    test_hashed_first_divergence_is_required_and_stage_ordered,
    test_worker_cell_replays_real_production_with_raw_dimensions_and_trace,
    test_worker_cell_retains_exact_failure_date_and_partial_trace,
    test_worker_comparison_emits_per_cell_aggregate_and_first_divergence,
    test_worker_comparison_retains_variant_failure_without_hiding_it_from_coverage,
    test_validation_runner_proves_every_carrier_and_is_deterministic,
)

from _generalization_checkpoint_evidence_cases import (
    test_worker_payload_requires_exact_schedule_status_and_trace_coverage,
    test_atomic_checkpoint_is_content_addressed_and_rejects_stale_or_mutated,
    test_complete_evidence_requires_all_13_exact_one_carrier_checkpoints,
    test_raw_backed_checkpoint_recomputes_real_comparison_after_reseal,
    test_no_divergence_writes_authenticated_invalid_artifact,
)

from _generalization_trust_boundary_cases import (
    test_aggregate_authenticates_invalid_results_without_claiming_complete,
    test_frozen_replay_error_anchor_rejects_resealed_message_mutation,
    test_replay_command_materializes_exact_historical_evidence_commit,
    test_evidence_checkout_rejects_a_clean_post_replay_commit_switch,
    test_evidence_checkout_cleanup_does_not_relabel_a_replay_failure,
    test_manifest_anchor_rejects_fully_resealed_worker_attack,
    test_tracked_manifest_rejects_edit_and_self_resign,
    test_trusted_evidence_manifest_rejects_duplicate_keys,
    test_historical_and_post_deletion_manifests_are_distinct_trust_roots,
    test_invalid_writer_rejects_any_existing_root_artifact,
    test_invalid_reader_rejects_any_existing_root_artifact,
)
