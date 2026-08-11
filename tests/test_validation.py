from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from uquant.data import DataContractError
from uquant.validation import promotion as promotion_module
from uquant.validation.cli import main as validation_main
from uquant.validation.manifest import verify_data_manifest
from uquant.validation.promotion import run_promotion


def _frozen_fixture(root: Path) -> Path:
    csv = root / "sh600000.csv"
    csv.write_text(
        "date,open,high,low,close,volume\n2026-01-05,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    (root / "SHA256SUMS").write_text(f"{digest}  {csv.name}\n", encoding="utf-8")
    (root / "DATA_MANIFEST.json").write_text(
        json.dumps(
            {
                "snapshot_id": "fixture",
                "results": [{"symbol": "sh600000", "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )
    return csv


def _seal_promotion_spec(spec: dict[str, Any]) -> None:
    """Create deterministic fake provenance without a production baseline writer."""
    spec["schema_version"] = 3
    spec["provenance"] = {
        "data": {
            "snapshot_id": "fixture",
            "files_verified": 1,
            "manifest_sha256": "a" * 64,
            "checksums_sha256": "b" * 64,
        },
        "dataset": {
            "matrix_sha256": promotion_module._dataset_fingerprint(spec),
            "pool_count": len(spec["pools"]),
            "scenario_count": len(spec["scenarios"]),
            "profile_count": len(spec["profiles"]),
            "reference_cell_count": len(spec["references"]),
        },
        "execution": {
            **promotion_module._EXECUTION_CONTRACT,
            "initial_cash": 2_000_000.0,
        },
        "reference": {
            "repository": "fixture/uquant",
            "reference_path": "benchmarks/promotion_baseline.json",
            "reference_commit": "c" * 40,
            "source_sha256": "d" * 64,
            "observations_sha256": promotion_module._observations_fingerprint(spec["references"]),
        },
    }
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)


def _install_promotion_runtime(
    monkeypatch: pytest.MonkeyPatch,
    spec: dict[str, Any],
) -> dict[str, Any]:
    runtime = {
        "data": dict(spec["provenance"]["data"]),
        "production": {
            "repository": "fixture/uquant",
            "commit": "e" * 40,
            "source_sha256": "f" * 64,
        },
    }
    monkeypatch.setattr(promotion_module, "_runtime_provenance", lambda _: runtime)
    monkeypatch.setattr(promotion_module, "_verify_reviewed_reference", lambda *_: None)
    return runtime


def _minimal_promotion_spec() -> dict[str, Any]:
    spec: dict[str, Any] = {
        "policy": {
            "schema_version": 2,
            "wealth_floor_ratio": 0.99,
            "drawdown_tolerance": 0.01,
            "absolute_max_drawdown": 0.35,
            "order_tolerance": 1,
            "order_ceiling_ratio": 1.05,
            "turnover_ceiling_ratio": 1.05,
            "turnover_tolerance": 0.25,
            "continuous_median_max_drawdown": 0.28,
            "continuous_worst_max_drawdown": 0.35,
            "choppy_2024_max_drawdown": 0.18,
        },
        "pools": {"one": ["sh600000"]},
        "scenarios": {
            "continuous": {"start": "2026-01-01", "end": "2026-01-02"},
            "choppy_2024": {"start": "2026-01-01", "end": "2026-01-02"},
        },
        "profiles": {
            "quick": [
                {"pool": "one", "scenario": "continuous"},
                {"pool": "one", "scenario": "choppy_2024"},
            ]
        },
        "references": {
            "one/continuous": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 1,
                "annual_turnover": 1.0,
            },
            "one/choppy_2024": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 1,
                "annual_turnover": 1.0,
            },
        },
    }
    _seal_promotion_spec(spec)
    return spec


def test_frozen_manifest_verifies_inventory_and_bytes(tmp_path: Path) -> None:
    csv = _frozen_fixture(tmp_path)
    report = verify_data_manifest(tmp_path)
    assert report["snapshot_id"] == "fixture"
    assert report["files_verified"] == 1

    csv.write_text(csv.read_text(encoding="utf-8") + "corrupt", encoding="utf-8")
    with pytest.raises(DataContractError, match="checksum mismatch"):
        verify_data_manifest(tmp_path)


def test_frozen_manifest_rejects_untracked_csv(tmp_path: Path) -> None:
    _frozen_fixture(tmp_path)
    (tmp_path / "sz000001.csv").write_text("untracked\n", encoding="utf-8")
    with pytest.raises(DataContractError, match="inventories differ"):
        verify_data_manifest(tmp_path)


def test_frozen_manifest_rejects_unsafe_symbol(tmp_path: Path) -> None:
    csv = _frozen_fixture(tmp_path)
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    (tmp_path / "DATA_MANIFEST.json").write_text(
        json.dumps(
            {
                "snapshot_id": "fixture",
                "results": [{"symbol": "../sh600000", "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="invalid result"):
        verify_data_manifest(tmp_path)


def test_repository_frozen_manifest_and_validation_cli(data_dir: Path, capsys: Any) -> None:
    report = verify_data_manifest(data_dir)
    assert report["files_verified"] >= 30
    assert validation_main(["data-manifest", "--data-dir", str(data_dir)]) == 0
    assert "files_verified" in capsys.readouterr().out


def test_promotion_matrix_reports_pass_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEngine:
        def __init__(self, data_dir: str | Path) -> None:
            assert str(data_dir) == "fixture"

        def backtest(self, **_: Any) -> dict[str, Any]:
            return {
                "final_wealth": 1.05,
                "max_drawdown": 0.10,
                "account_orders": 3,
                "annual_turnover": 1.0,
                "equity_curve": [
                    {"date": "2026-01-01", "equity": 100.0},
                    {"date": "2026-01-02", "equity": 96.0},
                ],
            }

    monkeypatch.setattr("uquant.validation.promotion.ProductionEngine", FakeEngine)
    spec = {
        "policy": {
            "schema_version": 2,
            "wealth_floor_ratio": 0.97,
            "drawdown_tolerance": 0.02,
            "absolute_max_drawdown": 0.35,
            "order_tolerance": 3,
            "order_ceiling_ratio": 1.05,
            "turnover_ceiling_ratio": 1.25,
            "turnover_tolerance": 0.5,
            "continuous_median_max_drawdown": 0.28,
            "continuous_worst_max_drawdown": 0.35,
            "choppy_2024_max_drawdown": 0.18,
        },
        "pools": {"one": ["sh600000"]},
        "scenarios": {
            "shock": {
                "start": "2026-01-01",
                "end": "2026-01-02",
                "urgent_start": "2026-01-01",
                "urgent_end": "2026-01-02",
            },
            "continuous": {"start": "2026-01-01", "end": "2026-01-02"},
            "choppy_2024": {"start": "2026-01-01", "end": "2026-01-02"},
        },
        "profiles": {
            "quick": [
                {"pool": "one", "scenario": "shock"},
                {"pool": "one", "scenario": "continuous"},
                {"pool": "one", "scenario": "choppy_2024"},
            ],
            "full": [
                {"pool": "one", "scenario": "shock"},
                {"pool": "one", "scenario": "continuous"},
                {"pool": "one", "scenario": "choppy_2024"},
            ],
        },
        "references": {
            "one/shock": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 2,
                "annual_turnover": 1.0,
                "urgent_return_floor": -0.05,
            },
            "one/continuous": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 2,
                "annual_turnover": 1.0,
            },
            "one/choppy_2024": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 2,
                "annual_turnover": 1.0,
            },
        },
    }
    _seal_promotion_spec(spec)
    runtime = _install_promotion_runtime(monkeypatch, spec)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")

    passed = run_promotion(data_dir="fixture", baseline=baseline)
    assert passed["passed"]
    assert passed["schema_version"] == 3
    assert passed["provenance"]["candidate"] == runtime
    assert passed["aggregate_gates"]["continuous"]["passed"]
    assert passed["aggregate_gates"]["choppy_2024"]["passed"]
    spec["references"]["one/shock"]["final_wealth"] = 2.0
    _seal_promotion_spec(spec)
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    failed = run_promotion(data_dir="fixture", baseline=baseline)
    assert not failed["passed"]
    assert "final_wealth" in failed["failures"][0]


def test_promotion_enforces_continuous_and_choppy_aggregate_drawdown_gates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEngine:
        def __init__(self, _: str | Path) -> None:
            pass

        def backtest(self, *, start: str, **_: Any) -> dict[str, Any]:
            return {
                "final_wealth": 1.0,
                "max_drawdown": 0.36 if start == "2020-01-01" else 0.19,
                "account_orders": 1,
                "annual_turnover": 1.0,
                "equity_curve": [],
            }

    monkeypatch.setattr("uquant.validation.promotion.ProductionEngine", FakeEngine)
    policy = {
        "schema_version": 2,
        "wealth_floor_ratio": 0.99,
        "drawdown_tolerance": 0.0,
        "absolute_max_drawdown": 0.50,
        "order_tolerance": 0,
        "order_ceiling_ratio": 1.0,
        "turnover_ceiling_ratio": 1.0,
        "turnover_tolerance": 0.0,
        "continuous_median_max_drawdown": 0.28,
        "continuous_worst_max_drawdown": 0.35,
        "choppy_2024_max_drawdown": 0.18,
    }
    metric = {
        "final_wealth": 1.0,
        "account_orders": 1,
        "annual_turnover": 1.0,
    }
    spec = {
        "policy": policy,
        "pools": {"one": ["sh600000"]},
        "scenarios": {
            "continuous": {"start": "2020-01-01", "end": "2026-01-02"},
            "choppy_2024": {"start": "2024-01-01", "end": "2024-12-31"},
        },
        "profiles": {
            "quick": [
                {"pool": "one", "scenario": "continuous"},
                {"pool": "one", "scenario": "choppy_2024"},
            ]
        },
        "references": {
            "one/continuous": {**metric, "max_drawdown": 0.36},
            "one/choppy_2024": {**metric, "max_drawdown": 0.19},
        },
    }
    _seal_promotion_spec(spec)
    _install_promotion_runtime(monkeypatch, spec)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")

    report = run_promotion(data_dir="fixture", baseline=baseline)

    assert not report["passed"]
    assert not report["aggregate_gates"]["continuous"]["passed"]
    assert not report["aggregate_gates"]["choppy_2024"]["passed"]
    assert any("median_max_drawdown" in item for item in report["failures"])
    assert any("worst_max_drawdown" in item for item in report["failures"])
    assert any("aggregate/choppy_2024" in item for item in report["failures"])


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({"policy": {}}, "missing sections"),
        (
            {
                "schema_version": 3,
                "validation_fingerprint": "a" * 64,
                "provenance": {},
                "policy": [],
                "pools": {},
                "scenarios": {},
                "profiles": {},
                "references": {},
            },
            "section must be an object",
        ),
    ],
)
def test_promotion_rejects_malformed_baseline(
    tmp_path: Path,
    payload: Any,
    message: str,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec["profiles"].update(quick="not-a-list"), "non-empty list"),
        (lambda spec: spec["pools"]["one"].append("sh600000"), "unique symbols"),
        (
            lambda spec: spec["scenarios"]["shock"].pop("urgent_end"),
            "incomplete urgent interval",
        ),
        (
            lambda spec: spec["references"]["one/shock"].pop("annual_turnover"),
            "missing metrics",
        ),
    ],
)
def test_promotion_rejects_invalid_nested_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    class FakeEngine:
        def __init__(self, _: str | Path) -> None:
            pass

    monkeypatch.setattr("uquant.validation.promotion.ProductionEngine", FakeEngine)
    spec = {
        "policy": {
            "schema_version": 2,
            "wealth_floor_ratio": 0.99,
            "drawdown_tolerance": 0.01,
            "absolute_max_drawdown": 0.40,
            "order_tolerance": 2,
            "order_ceiling_ratio": 1.05,
            "turnover_ceiling_ratio": 1.05,
            "turnover_tolerance": 0.25,
            "continuous_median_max_drawdown": 0.28,
            "continuous_worst_max_drawdown": 0.35,
            "choppy_2024_max_drawdown": 0.18,
        },
        "pools": {"one": ["sh600000"]},
        "scenarios": {
            "shock": {
                "start": "2026-01-01",
                "end": "2026-01-02",
                "urgent_start": "2026-01-01",
                "urgent_end": "2026-01-02",
            }
        },
        "profiles": {
            "quick": [{"pool": "one", "scenario": "shock"}],
            "full": [{"pool": "one", "scenario": "shock"}],
        },
        "references": {
            "one/shock": {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 2,
                "annual_turnover": 1.0,
            }
        },
    }
    _seal_promotion_spec(spec)
    mutation(spec)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_repository_promotion_baseline_has_verified_schema_v3_provenance(
    data_dir: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    _, spec = promotion_module._load_spec(repository_root / "benchmarks" / "promotion_baseline.json")

    assert spec["schema_version"] == 3
    assert spec["provenance"]["data"] == verify_data_manifest(data_dir)
    promotion_module._verify_reviewed_reference(
        repository_root,
        spec,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda spec: spec["policy"].update(absolute_max_drawdown=0.42),
            "policy is weaker",
        ),
        (
            lambda spec: spec["policy"].update(continuous_worst_max_drawdown=0.36),
            "aggregate policy is weaker",
        ),
        (
            lambda spec: spec["pools"]["e"].remove("sh603688"),
            "pools differ",
        ),
        (
            lambda spec: spec["scenarios"]["continuous"].update(start="2019-01-02"),
            "scenarios differ",
        ),
        (
            lambda spec: spec["profiles"]["quick"].pop(),
            "profiles differ",
        ),
        (
            lambda spec: spec["provenance"]["data"].update(checksums_sha256="0" * 64),
            "data contract differs",
        ),
        (
            lambda spec: spec["provenance"]["execution"].update(initial_cash=1_000_000.0),
            "execution cash differs",
        ),
        (
            lambda spec: spec["references"]["e/through_july"].update(
                urgent_return_floor=-0.07
            ),
            "urgent floor is weaker",
        ),
    ],
)
def test_promotion_reviewed_contract_rejects_self_fingerprinted_weakening(
    mutation: Any,
    message: str,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    _, spec = promotion_module._load_spec(
        repository_root / "benchmarks" / "promotion_baseline.json"
    )
    mutation(spec)
    spec["provenance"]["dataset"] = {
        "matrix_sha256": promotion_module._dataset_fingerprint(spec),
        "pool_count": len(spec["pools"]),
        "scenario_count": len(spec["scenarios"]),
        "profile_count": len(spec["profiles"]),
        "reference_cell_count": len(spec["references"]),
    }
    spec["provenance"]["reference"]["observations_sha256"] = (
        promotion_module._observations_fingerprint(spec["references"])
    )
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)

    with pytest.raises(RuntimeError, match=message):
        promotion_module._verify_reviewed_reference(repository_root, spec)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda spec: spec.update(schema_version=2), "unsupported promotion baseline schema"),
        (
            lambda spec: spec["provenance"]["data"].pop("manifest_sha256"),
            "provenance.data is missing fields",
        ),
        (
            lambda spec: spec["provenance"]["execution"].update(decision="same_close"),
            "execution contract mismatch",
        ),
        (
            lambda spec: spec["provenance"]["dataset"].update(pool_count=2),
            "dataset does not match",
        ),
        (
            lambda spec: spec["references"]["one/continuous"].update(final_wealth=1.01),
            "reviewed observations fingerprint is stale",
        ),
        (
            lambda spec: spec["policy"].update(wealth_floor_ratio=0.98),
            "validation fingerprint is stale",
        ),
    ],
)
def test_promotion_schema_v3_rejects_stale_or_incomplete_envelopes_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    spec = _minimal_promotion_spec()
    mutation(spec)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(
        promotion_module,
        "ProductionEngine",
        lambda *_: pytest.fail("replay must not start for invalid provenance"),
    )

    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_promotion_rejects_runtime_data_mismatch_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _minimal_promotion_spec()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    runtime = _install_promotion_runtime(monkeypatch, spec)
    runtime["data"] = {**runtime["data"], "snapshot_id": "other"}
    monkeypatch.setattr(
        promotion_module,
        "ProductionEngine",
        lambda *_: pytest.fail("replay must not start for mismatched data"),
    )

    with pytest.raises(RuntimeError, match="data provenance does not match"):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_promotion_candidate_commit_rejects_dirty_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        promotion_module,
        "_git_stdout",
        lambda *_args, **_kwargs: " M uquant/risk.py\n",
    )

    with pytest.raises(RuntimeError, match="requires committed source"):
        promotion_module._production_commit(tmp_path)


def test_promotion_runtime_provenance_requires_source_to_match_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(promotion_module, "_production_commit", lambda _: "e" * 40)
    monkeypatch.setattr(promotion_module, "_production_source_fingerprint", lambda _: "a" * 64)
    monkeypatch.setattr(
        promotion_module,
        "_production_source_fingerprint_at_commit",
        lambda *_: "b" * 64,
    )

    with pytest.raises(RuntimeError, match="does not match its committed source"):
        promotion_module._runtime_provenance("fixture")


def test_promotion_rejects_nonfinite_candidate_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEngine:
        def __init__(self, _: str | Path) -> None:
            pass

        def backtest(self, **_: Any) -> dict[str, Any]:
            return {
                "final_wealth": float("nan"),
                "max_drawdown": 0.10,
                "account_orders": 1,
                "annual_turnover": 1.0,
                "equity_curve": [],
            }

    spec = _minimal_promotion_spec()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    _install_promotion_runtime(monkeypatch, spec)
    monkeypatch.setattr(promotion_module, "ProductionEngine", FakeEngine)

    with pytest.raises(RuntimeError, match="value must be finite"):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_promotion_rejects_source_or_data_mutation_during_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeEngine:
        def __init__(self, _: str | Path) -> None:
            pass

        def backtest(self, **_: Any) -> dict[str, Any]:
            return {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 1,
                "annual_turnover": 1.0,
                "equity_curve": [],
            }

    spec = _minimal_promotion_spec()
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    before = {
        "data": dict(spec["provenance"]["data"]),
        "production": {
            "repository": "fixture/uquant",
            "commit": "e" * 40,
            "source_sha256": "f" * 64,
        },
    }
    after = {
        **before,
        "production": {**before["production"], "source_sha256": "0" * 64},
    }
    snapshots = iter((before, after))
    monkeypatch.setattr(promotion_module, "_runtime_provenance", lambda _: next(snapshots))
    monkeypatch.setattr(promotion_module, "_verify_reviewed_reference", lambda *_: None)
    monkeypatch.setattr(promotion_module, "ProductionEngine", FakeEngine)

    with pytest.raises(RuntimeError, match="source or data changed during validation"):
        run_promotion(data_dir="fixture", baseline=baseline)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"schema_version":3,"schema_version":3}', "duplicate key"),
        ('{"schema_version":NaN}', "non-standard number"),
    ],
)
def test_promotion_rejects_ambiguous_json(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)
