from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest

from uquant.data import DataContractError
from uquant.validation import promotion as promotion_module
from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
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


def _valid_spec() -> dict[str, Any]:
    reviewed = json.loads(
        (Path(__file__).parents[1] / "benchmarks" / "promotion_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(reviewed, dict)
    return reviewed


def _write_spec(path: Path, spec: dict[str, Any]) -> Path:
    path.write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
    return path


def _runtime(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": deepcopy(spec["provenance"]["data"]),
        "production": {
            "repository": "ychenracing/uquant",
            "commit": "f" * 40,
            "source_sha256": "1" * 64,
        },
        "environment": {
            "python_full_version": "3.12.13",
            "numpy_version": "2.5.1",
            "pandas_version": "3.0.5",
            "uv_version": "0.11.33",
            "uv_lock_sha256": "2" * 64,
        },
        "effective_config_sha256": "3" * 64,
    }


class _PassingEngine:
    calls: ClassVar[list[tuple[tuple[str, ...], str, str]]] = []

    def __init__(self, _: str | Path) -> None:
        pass

    def backtest(self, *, symbols: tuple[str, ...], start: str, end: str) -> dict[str, Any]:
        type(self).calls.append((symbols, start, end))
        sessions = pd.bdate_range(start, end)
        curve = [
            {
                "date": str(date.date()),
                "equity": 40_000_000.0 * (1.006**index),
            }
            for index, date in enumerate(sessions)
        ]
        return {
            "start": str(sessions[0].date()),
            "end": str(sessions[-1].date()),
            "final_wealth": 100.0,
            "cagr": 10.0,
            "max_drawdown": 0.01,
            "sharpe": 2.0,
            "calmar": 1000.0,
            "account_orders": 1,
            "annual_turnover": 1.0,
            "gross_turnover": 1.0,
            "equity_curve": curve,
            "effective_config_sha256": "3" * 64,
        }


def _install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    spec: dict[str, Any],
    *,
    engine: type[_PassingEngine] = _PassingEngine,
) -> dict[str, Any]:
    runtime = _runtime(spec)
    monkeypatch.setattr(promotion_module, "_runtime_provenance", lambda _: runtime)
    monkeypatch.setattr(promotion_module, "ProductionEngine", engine)
    return runtime


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


def test_frozen_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    _frozen_fixture(tmp_path)
    manifest = tmp_path / "DATA_MANIFEST.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"snapshot_id": "fixture"',
            '"snapshot_id": "forged", "snapshot_id": "fixture"',
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DataContractError, match="duplicate key"):
        verify_data_manifest(tmp_path)


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("DATA_MANIFEST.json", "missing or corrupt"),
        ("SHA256SUMS", "cannot read frozen checksum"),
    ],
)
def test_frozen_manifest_attributes_invalid_utf8_to_the_data_contract(
    filename: str,
    message: str,
    tmp_path: Path,
) -> None:
    """Catches a raw codec exception escaping a frozen-data trust boundary."""

    _frozen_fixture(tmp_path)
    (tmp_path / filename).write_bytes(b"\xff")

    with pytest.raises(DataContractError, match=message):
        verify_data_manifest(tmp_path)


def test_frozen_manifest_rejects_symlinked_metadata_and_split_hash_truth(
    tmp_path: Path,
) -> None:
    """Exercises metadata identity and manifest/checksum disagreement."""

    _frozen_fixture(tmp_path)
    manifest = tmp_path / "DATA_MANIFEST.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["results"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataContractError, match="manifest checksum differs"):
        verify_data_manifest(tmp_path)

    victim = tmp_path / "manifest-victim.json"
    victim.write_text(json.dumps(payload), encoding="utf-8")
    manifest.unlink()
    manifest.symlink_to(victim)
    with pytest.raises(DataContractError, match="metadata must be regular"):
        verify_data_manifest(tmp_path)

    symlink_root = tmp_path / "symlink-data"
    symlink_root.mkdir()
    csv = _frozen_fixture(symlink_root)
    csv_victim = tmp_path / "csv-victim"
    csv_victim.write_bytes(csv.read_bytes())
    csv.unlink()
    csv.symlink_to(csv_victim)
    with pytest.raises(DataContractError, match="data must be a regular file"):
        verify_data_manifest(symlink_root)


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


def test_contract_requires_exact_six_ai_era_windows_and_all_pools() -> None:
    spec = _valid_spec()
    promotion_module._validate_spec(spec)

    missing_window = deepcopy(spec)
    missing_window["contract"]["windows"].pop("h1_2023")
    missing_window["validation_fingerprint"] = promotion_module._validation_fingerprint(
        missing_window
    )
    with pytest.raises(RuntimeError, match="official windows"):
        promotion_module._validate_spec(missing_window)

    missing_pool = deepcopy(spec)
    missing_pool["pools"].pop("e")
    missing_pool["validation_fingerprint"] = promotion_module._validation_fingerprint(
        missing_pool
    )
    with pytest.raises(RuntimeError, match="pools"):
        promotion_module._validate_spec(missing_pool)


def test_contract_rejects_any_pre_2023_economic_interval() -> None:
    spec = _valid_spec()
    spec["contract"]["protected_intervals"]["year_2023"]["start"] = "2022-12-30"
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)

    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        promotion_module._validate_spec(spec)


def test_policy_is_compiled_and_cannot_be_weakened() -> None:
    spec = _valid_spec()
    spec["policy"]["protected"]["year_2024"]["min_final_wealth"] = 1.0
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)

    with pytest.raises(RuntimeError, match="policy differs from compiled"):
        promotion_module._validate_spec(spec)


def test_pools_cannot_be_replaced_by_self_signing_the_baseline() -> None:
    spec = _valid_spec()
    spec["pools"]["a"][0] = "sh600000"
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)

    with pytest.raises(RuntimeError, match="compiled reviewed universe"):
        promotion_module._validate_spec(spec)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda spec: spec["provenance"]["data"].update(
            snapshot_id="replacement-snapshot"
        ),
        lambda spec: spec["provenance"]["reference"].update(commit="f" * 40),
    ],
)
def test_reviewed_provenance_cannot_be_replaced_by_self_signing_the_baseline(
    mutate: Any,
) -> None:
    spec = _valid_spec()
    mutate(spec)
    spec["validation_fingerprint"] = promotion_module._validation_fingerprint(spec)

    with pytest.raises(RuntimeError, match="compiled reviewed baseline"):
        promotion_module._validate_spec(spec)


def test_champion_is_mandatory_and_cannot_be_erased_or_self_replaced() -> None:
    empty = _valid_spec()
    empty["champion"] = {"production_commit": "", "cells": {}, "protected": {}}
    empty["validation_fingerprint"] = promotion_module._validation_fingerprint(empty)
    with pytest.raises(RuntimeError, match="champion commit"):
        promotion_module._validate_spec(empty)

    replaced = _valid_spec()
    replaced["champion"]["cells"]["a/h1_2023"]["final_wealth"] = 1.0
    replaced["validation_fingerprint"] = promotion_module._validation_fingerprint(replaced)
    with pytest.raises(RuntimeError, match="compiled reviewed evidence"):
        promotion_module._validate_spec(replaced)


def test_full_promotion_runs_every_official_and_protected_cell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)
    _PassingEngine.calls = []
    runtime = _install_runtime(monkeypatch, spec)

    report = run_promotion(data_dir="fixture", baseline=baseline, profile="full")

    expected = len(spec["pools"]) * (
        len(AI_ERA_WINDOWS) + len(promotion_module.PROTECTED_INTERVALS)
    )
    assert len(_PassingEngine.calls) == expected
    assert len(report["cells"]) == len(spec["pools"]) * len(AI_ERA_WINDOWS)
    assert len(report["protected"]) == len(spec["pools"]) * len(
        promotion_module.PROTECTED_INTERVALS
    )
    assert report["passed"]
    assert report["provenance"]["candidate"] == runtime
    binding = report["provenance"]["binding"]
    assert binding == {
        "production_commit": "f" * 40,
        "production_source_sha256": "1" * 64,
        "effective_config_sha256": "3" * 64,
        "data_snapshot_id": spec["provenance"]["data"]["snapshot_id"],
        "data_manifest_sha256": spec["provenance"]["data"]["manifest_sha256"],
        "python_full_version": "3.12.13",
        "numpy_version": "2.5.1",
        "pandas_version": "3.0.5",
        "uv_version": "0.11.33",
        "uv_lock_sha256": "2" * 64,
        "generated_at": report["provenance"]["generated_at"],
    }


def test_any_official_cell_or_acute_failure_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _valid_spec()
    pool_a = tuple(spec["pools"]["a"])

    class FailingEngine(_PassingEngine):
        def backtest(self, *, symbols: tuple[str, ...], start: str, end: str) -> dict[str, Any]:
            result = super().backtest(symbols=symbols, start=start, end=end)
            if start == AI_ERA_WINDOWS["h1_2023"][0] and symbols == pool_a:
                result["final_wealth"] = 1.0
            if start == AI_ERA_WINDOWS["bull_crash_2025_2026"][0]:
                acute_start, acute_end = AI_ERA_ACUTE_WINDOWS["bull_crash_2025_2026"]
                for point in result["equity_curve"]:
                    if acute_start < point["date"] <= acute_end:
                        point["equity"] *= 0.80
            return result

    baseline = _write_spec(tmp_path / "baseline.json", spec)
    _install_runtime(monkeypatch, spec, engine=FailingEngine)

    report = run_promotion(data_dir="fixture", baseline=baseline)

    assert not report["passed"]
    assert any("a/h1_2023" in failure for failure in report["failures"])
    assert any("acute_return" in failure for failure in report["failures"])


def test_runtime_provenance_binds_head_data_config_and_locked_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "python_full_version": "3.12.13",
        "numpy_version": "2.5.1",
        "pandas_version": "3.0.5",
        "uv_version": "0.11.33",
        "uv_lock_sha256": "c" * 64,
    }
    monkeypatch.setattr(promotion_module, "_production_commit", lambda _: "e" * 40)
    monkeypatch.setattr(promotion_module, "_production_source_fingerprint", lambda _: "a" * 64)
    monkeypatch.setattr(
        promotion_module,
        "_production_source_fingerprint_at_commit",
        lambda *_: "a" * 64,
    )
    monkeypatch.setattr(promotion_module, "verify_data_manifest", lambda _: {"snapshot_id": "x"})
    monkeypatch.setattr(promotion_module, "runtime_environment_provenance", lambda _: environment)
    monkeypatch.setattr(promotion_module, "config_fingerprint", lambda: "d" * 64)

    assert promotion_module._runtime_provenance("fixture") == {
        "data": {"snapshot_id": "x"},
        "production": {
            "repository": "ychenracing/uquant",
            "commit": "e" * 40,
            "source_sha256": "a" * 64,
        },
        "environment": environment,
        "effective_config_sha256": "d" * 64,
    }


def test_promotion_candidate_commit_rejects_dirty_production_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        promotion_module,
        "_git_stdout",
        lambda *_args, **_kwargs: " M uquant/risk.py\n",
    )

    with pytest.raises(RuntimeError, match="requires committed production source"):
        promotion_module._production_commit(tmp_path)


def test_promotion_candidate_commit_checks_reference_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_status_arguments: list[str] = []

    def git_stdout(_root: Path, arguments: list[str], *, label: str) -> str:
        del label
        if arguments[0] == "status":
            observed_status_arguments.extend(arguments)
            if "benchmarks/reference_registry.json" in arguments:
                return " M benchmarks/reference_registry.json\n"
            return ""
        return "e" * 40

    monkeypatch.setattr(promotion_module, "_git_stdout", git_stdout)

    with pytest.raises(RuntimeError, match="requires committed production source"):
        promotion_module._production_commit(tmp_path)
    assert "benchmarks/reference_registry.json" in observed_status_arguments


def test_promotion_candidate_commit_checks_config_governance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_status_arguments: list[str] = []

    def git_stdout(_root: Path, arguments: list[str], *, label: str) -> str:
        del label
        if arguments[0] == "status":
            observed_status_arguments.extend(arguments)
            if "benchmarks/config_parameter_governance.json" in arguments:
                return " M benchmarks/config_parameter_governance.json\n"
            return ""
        return "e" * 40

    monkeypatch.setattr(promotion_module, "_git_stdout", git_stdout)

    with pytest.raises(RuntimeError, match="requires committed production source"):
        promotion_module._production_commit(tmp_path)
    assert "benchmarks/config_parameter_governance.json" in observed_status_arguments


def test_promotion_candidate_commit_checks_uv_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed_status_arguments: list[str] = []

    def git_stdout(_root: Path, arguments: list[str], *, label: str) -> str:
        del label
        if arguments[0] == "status":
            observed_status_arguments.extend(arguments)
            if "uv.lock" in arguments:
                return " M uv.lock\n"
            return ""
        return "e" * 40

    monkeypatch.setattr(promotion_module, "_git_stdout", git_stdout)

    with pytest.raises(RuntimeError, match="requires committed production source"):
        promotion_module._production_commit(tmp_path)
    assert "uv.lock" in observed_status_arguments


def test_production_source_fingerprint_includes_control_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "uquant").mkdir()
    (tmp_path / "benchmarks").mkdir()
    (tmp_path / "pyproject.toml").write_text("project\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requirements\n", encoding="utf-8")
    lock = tmp_path / "uv.lock"
    lock.write_text("lock-version-1\n", encoding="utf-8")
    (tmp_path / "uquant" / "engine.py").write_text("engine\n", encoding="utf-8")
    registry = tmp_path / "benchmarks" / "reference_registry.json"
    registry.write_text('{"version":1}\n', encoding="utf-8")
    governance = tmp_path / "benchmarks" / "config_parameter_governance.json"
    governance.write_text('{"artifact_sha256":"1"}\n', encoding="utf-8")
    first = promotion_module._production_source_fingerprint(tmp_path)

    registry.write_text('{"version":2}\n', encoding="utf-8")

    assert promotion_module._production_source_fingerprint(tmp_path) != first

    second = promotion_module._production_source_fingerprint(tmp_path)
    lock.write_text("lock-version-2\n", encoding="utf-8")

    assert promotion_module._production_source_fingerprint(tmp_path) != second

    third = promotion_module._production_source_fingerprint(tmp_path)
    governance.write_text('{"artifact_sha256":"2"}\n', encoding="utf-8")

    assert promotion_module._production_source_fingerprint(tmp_path) != third


def test_committed_source_fingerprint_includes_control_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registry_content = '{"version":1}\n'
    governance_content = '{"artifact_sha256":"1"}\n'
    lock_content = "lock-version-1\n"

    def git_stdout(_root: Path, arguments: list[str], *, label: str) -> str:
        del label
        if arguments[0] == "ls-tree":
            return (
                "pyproject.toml\nrequirements.txt\nuv.lock\nuquant/engine.py\n"
                "benchmarks/reference_registry.json\n"
                "benchmarks/config_parameter_governance.json\n"
            )
        path = arguments[-1].split(":", maxsplit=1)[1]
        if path == "benchmarks/reference_registry.json":
            return registry_content
        if path == "benchmarks/config_parameter_governance.json":
            return governance_content
        if path == "uv.lock":
            return lock_content
        return f"{path}\n"

    monkeypatch.setattr(promotion_module, "_git_stdout", git_stdout)
    first = promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
    registry_content = '{"version":2}\n'

    assert (
        promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
        != first
    )

    second = promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
    lock_content = "lock-version-2\n"

    assert (
        promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
        != second
    )

    third = promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
    governance_content = '{"artifact_sha256":"2"}\n'

    assert (
        promotion_module._production_source_fingerprint_at_commit(tmp_path, "e" * 40)
        != third
    )


def test_runtime_provenance_rejects_source_that_differs_from_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(promotion_module, "_production_commit", lambda _: "e" * 40)
    monkeypatch.setattr(promotion_module, "_production_source_fingerprint", lambda _: "a" * 64)
    monkeypatch.setattr(
        promotion_module,
        "_production_source_fingerprint_at_commit",
        lambda *_: "b" * 64,
    )

    with pytest.raises(RuntimeError, match="source does not match its committed HEAD"):
        promotion_module._runtime_provenance("fixture")


def test_promotion_rejects_nonfinite_candidate_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class NonfiniteEngine(_PassingEngine):
        def backtest(self, *, symbols: tuple[str, ...], start: str, end: str) -> dict[str, Any]:
            result = super().backtest(symbols=symbols, start=start, end=end)
            result["final_wealth"] = float("nan")
            return result

    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)
    _install_runtime(monkeypatch, spec, engine=NonfiniteEngine)

    with pytest.raises(RuntimeError, match="must be finite"):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_promotion_rejects_runtime_data_mismatch_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)
    runtime = _runtime(spec)
    runtime["data"] = {**runtime["data"], "snapshot_id": "other"}
    monkeypatch.setattr(promotion_module, "_runtime_provenance", lambda _: runtime)
    monkeypatch.setattr(
        promotion_module,
        "ProductionEngine",
        lambda *_: pytest.fail("replay must not start for mismatched data"),
    )

    with pytest.raises(RuntimeError, match="data provenance does not match"):
        run_promotion(data_dir="fixture", baseline=baseline)


def test_promotion_rejects_source_or_data_mutation_during_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)
    before = _runtime(spec)
    after = deepcopy(before)
    after["production"]["source_sha256"] = "0" * 64
    snapshots = iter((before, after))
    monkeypatch.setattr(promotion_module, "_runtime_provenance", lambda _: next(snapshots))
    monkeypatch.setattr(promotion_module, "ProductionEngine", _PassingEngine)

    with pytest.raises(RuntimeError, match="changed during replay"):
        run_promotion(data_dir="fixture", baseline=baseline)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('{"schema_version":4,"schema_version":4}', "duplicate key"),
        ('{"schema_version":NaN}', "non-standard number"),
        ("[]", "JSON object"),
    ],
)
def test_promotion_rejects_ambiguous_or_malformed_json(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(payload, encoding="utf-8")
    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)
