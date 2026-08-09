from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from uquant.data import DataContractError
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
            "wealth_floor_ratio": 0.97,
            "drawdown_tolerance": 0.02,
            "absolute_max_drawdown": 0.35,
            "order_tolerance": 3,
            "turnover_ceiling_ratio": 1.25,
            "turnover_tolerance": 0.5,
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
                "urgent_return_floor": -0.05,
            }
        },
    }
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")

    passed = run_promotion(data_dir="fixture", baseline=baseline)
    assert passed["passed"]
    spec["references"]["one/shock"]["final_wealth"] = 2.0
    baseline.write_text(json.dumps(spec), encoding="utf-8")
    failed = run_promotion(data_dir="fixture", baseline=baseline)
    assert not failed["passed"]
    assert "final_wealth" in failed["failures"][0]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "JSON object"),
        ({"policy": {}}, "missing sections"),
        (
            {
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
            "wealth_floor_ratio": 0.99,
            "drawdown_tolerance": 0.01,
            "absolute_max_drawdown": 0.40,
            "order_tolerance": 2,
            "turnover_ceiling_ratio": 1.05,
            "turnover_tolerance": 0.25,
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
    mutation(spec)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        run_promotion(data_dir="fixture", baseline=baseline)
