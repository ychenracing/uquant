from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_generalization_ablation import (
    ROOT,
    _current_runner_with_reviewed_production_checkout,
    _metrics,
    _reviewed_source_checkout,
    _runner_module,
)

from research import ablation_registry as ablation_registry_module
from research.ablation_registry import (
    MINIMAL_ABLATION_REGISTRY_PATH,
    build_contract_schedule,
    isolated_baseline_checkout,
    isolated_carrier_checkout,
    load_ablation_registry,
    verify_carrier_checkout,
)
from uquant.engine import ProductionEngine


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
    """Catches a result schema that loses materiality inputs or labels conclusions early."""
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
    script = source_root / "scripts" / "run_generalization_ablation.py"
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
