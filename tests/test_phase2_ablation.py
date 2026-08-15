from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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


def _runner_module():
    source = ROOT / "scripts" / "run_phase2_ablation.py"
    spec = importlib.util.spec_from_file_location("phase2_ablation_runner", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    validate_ablation_registry(registry, source_root=ROOT)

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


def test_fixed_schedule_is_complete_deterministic_and_preserves_known_status() -> None:
    """Catches a runner that shards, replaces, or omits a fixed contract record."""
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)

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
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    experiment = next(item for item in registry.experiments if item.subsystem == subsystem)
    destination = tmp_path / subsystem

    with isolated_carrier_checkout(
        registry,
        experiment,
        source_root=ROOT,
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
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with pytest.raises(ValueError, match="changed after materialization"):
                verify_carrier_checkout(registry, experiment, checkout)
        else:
            assert checkout.experiment_commit == checkout.base_commit
            assert checkout.source_sha256 == registry.source_sha256

    assert not destination.exists()


def test_baseline_materializes_as_exact_isolated_clean_source(
    tmp_path: Path,
) -> None:
    """Catches baseline execution from a dirty task worktree or a moving HEAD."""
    registry = load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH)
    destination = tmp_path / "baseline"

    with isolated_baseline_checkout(
        registry,
        source_root=ROOT,
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
    script = ROOT / "scripts" / "run_phase2_ablation.py"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    command = [
        sys.executable,
        str(script),
        "validate",
        "--source-root",
        str(ROOT),
        "--registry",
        str(DEFAULT_ABLATION_REGISTRY_PATH),
    ]
    first_run = subprocess.run(
        [*command, "--output", str(first)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    second_run = subprocess.run(
        [*command, "--output", str(second)],
        cwd=ROOT,
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
    assert payload["registry_sha256"] == load_ablation_registry(DEFAULT_ABLATION_REGISTRY_PATH).payload_sha256
    assert payload["source"]["base_commit"] == "7f80436373b6da03536e15ff1908c010bfb92eb3"
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
    assert len(payload["experiments"]) == 13
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
    stale = json.loads(checkpoint.read_text(encoding="utf-8"))
    stale["payload"]["binding_sha256"] = "b" * 64
    checkpoint.write_text(json.dumps(stale), encoding="utf-8")
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
