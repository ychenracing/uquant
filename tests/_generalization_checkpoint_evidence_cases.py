from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_generalization_ablation import (
    _metrics,
    _runner_module,
    _single_cell_worker,
)

from research.ablation_registry import (
    DEFAULT_ABLATION_REGISTRY_PATH,
    ContractCell,
    load_ablation_registry,
)


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

def test_complete_evidence_requires_all_13_exact_one_carrier_checkpoints() -> None:
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
            "replay_command": ["python", "run_generalization_ablation.py"],
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
