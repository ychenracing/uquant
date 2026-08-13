from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from uquant.validation.ai_era import AI_ERA_WINDOWS
from uquant.validation.competitor import (
    CANONICAL_EXECUTION_CONTRACT,
    CANONICAL_WINDOWS,
    LOCKED_COMPETITOR_PROVENANCE,
    REQUIRED_COMPETITORS,
    REQUIRED_POOLS,
    REQUIRED_WINDOWS,
    CompetitorMetrics,
    MatrixWindow,
    best_of_three,
    data_provenance_from_directory,
    evaluate_competitor_gate,
    load_competitor_matrix,
    run_competitor_gate,
)


def test_competitor_gate_reuses_the_six_official_ai_era_windows() -> None:
    assert tuple(AI_ERA_WINDOWS) == REQUIRED_WINDOWS
    assert {window.name: (window.start, window.end) for window in CANONICAL_WINDOWS} == AI_ERA_WINDOWS


def test_pre_2023_dates_are_rejected_as_score_or_replay() -> None:
    with pytest.raises(RuntimeError, match="cannot start before 2023-01-01"):
        MatrixWindow("legacy_score", "2022-01-04", "2022-12-30")

    with pytest.raises(TypeError, match="unexpected keyword argument 'replay_start'"):
        MatrixWindow(
            "h1_2023",
            "2023-01-03",
            "2023-06-30",
            replay_start="2022-12-01",
        )


def _data_dir(root: Path) -> Path:
    root.mkdir()
    (root / "sh600000.csv").write_text(
        "date,open,high,low,close,volume\n2021-01-04,10,11,9,10.5,1000\n",
        encoding="utf-8",
    )
    (root / "SHA256SUMS").write_text("reviewed fixture checksums\n", encoding="utf-8")
    (root / "DATA_MANIFEST.json").write_text(
        json.dumps(
            {
                "snapshot_id": "competitor-fixture-v1",
                "adjustment": "fixture qfq stocks and raw indices",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return root


def _reference_payload(data_dir: Path) -> dict[str, Any]:
    competitor_values = {
        "aquant": {
            "final_wealth": 1.8,
            "max_drawdown": 0.10,
            "account_orders": 20,
        },
        "qwenquant": {
            "final_wealth": 2.0,
            "max_drawdown": 0.20,
            "account_orders": 10,
        },
        "trade": {
            "final_wealth": 1.2,
            "max_drawdown": 0.15,
            "account_orders": 30,
        },
    }
    return {
        "schema_version": 1,
        "frozen_at_utc": "2026-08-10T00:00:00Z",
        "policy": {
            "wealth_floor_ratio": 0.95,
            "drawdown_tolerance": 0.02,
            "absolute_max_drawdown": 0.50,
            "order_tolerance": 1,
            "order_ceiling_ratio": 1.05,
        },
        "execution_contract": CANONICAL_EXECUTION_CONTRACT.to_payload(),
        "data_provenance": data_provenance_from_directory(data_dir).to_payload(),
        "repositories": {name: provenance.to_payload() for name, provenance in LOCKED_COMPETITOR_PROVENANCE},
        "pools": {pool: ["sh600000"] for pool in REQUIRED_POOLS},
        "windows": {window.name: window.to_payload() for window in CANONICAL_WINDOWS},
        "results": {
            f"{pool}/{window}/{competitor}": dict(competitor_values[competitor])
            for pool in REQUIRED_POOLS
            for window in REQUIRED_WINDOWS
            for competitor in REQUIRED_COMPETITORS
        },
    }


def _write_reference(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def test_full_matrix_runs_every_window_and_reports_best_of_three(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    reference_path = _write_reference(
        tmp_path / "competitor.json",
        _reference_payload(data_dir),
    )
    calls: list[tuple[str, str, str]] = []

    def runner(pool: str, symbols: tuple[str, ...], window: Any) -> dict[str, Any]:
        calls.append((pool, symbols[0], window.name))
        return {
            "final_wealth": 1.91,
            "max_drawdown": 0.12,
            "account_orders": 11,
        }

    report = run_competitor_gate(
        data_dir=data_dir,
        reference_path=reference_path,
        runner=runner,
    )

    assert report["passed"]
    assert report["summary"] == {
        "cells": 30,
        "windows": 6,
        "pools": 5,
        "competitors": 3,
    }
    assert calls == [(pool, "sh600000", name) for pool in REQUIRED_POOLS for name in REQUIRED_WINDOWS]
    assert len(load_competitor_matrix(reference_path).results) == 90
    bull = report["results"]["a/bull_crash_2025_2026"]
    assert bull["best_of_three"] == {
        "final_wealth": {"competitor": "qwenquant", "value": 2.0},
        "max_drawdown": {"competitor": "aquant", "value": 0.10},
        "account_orders": {"competitor": "qwenquant", "value": 10},
    }
    assert bull["thresholds"] == pytest.approx(
        {
            "final_wealth_floor": 1.9,
            "max_drawdown_ceiling": 0.12,
            "account_orders_ceiling": 11,
        }
    )


def test_wealth_drawdown_and_order_thresholds_fail_independently(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    reference = load_competitor_matrix(
        _write_reference(tmp_path / "competitor.json", _reference_payload(data_dir))
    )
    candidates = {
        f"{pool}/{window}": {
            "final_wealth": 1.89,
            "max_drawdown": 0.121,
            "account_orders": 12,
        }
        for pool in REQUIRED_POOLS
        for window in REQUIRED_WINDOWS
    }

    report = evaluate_competitor_gate(reference, candidates)

    assert not report["passed"]
    assert len(report["failures"]) == len(REQUIRED_POOLS) * len(REQUIRED_WINDOWS) * 3
    assert any("final_wealth" in item for item in report["failures"])
    assert any("max_drawdown" in item for item in report["failures"])
    assert any("account_orders" in item for item in report["failures"])


def test_reference_missing_cell_fails_before_any_replay(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    payload = _reference_payload(data_dir)
    payload["results"].pop("a/continuous_ai_era/trade")
    reference_path = _write_reference(tmp_path / "competitor.json", payload)
    calls: list[str] = []

    def runner(_: str, __: tuple[str, ...], window: Any) -> dict[str, Any]:
        calls.append(window.name)
        return {"final_wealth": 2.0, "max_drawdown": 0.1, "account_orders": 1}

    with pytest.raises(RuntimeError, match=r"missing result cells.*continuous_ai_era/trade"):
        run_competitor_gate(
            data_dir=data_dir,
            reference_path=reference_path,
            runner=runner,
        )
    assert calls == []


def test_candidate_missing_cell_is_an_explicit_contract_failure(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    reference = load_competitor_matrix(
        _write_reference(tmp_path / "competitor.json", _reference_payload(data_dir))
    )
    candidates = {
        f"{pool}/{window}": CompetitorMetrics(2.0, 0.1, 1)
        for pool in REQUIRED_POOLS
        for window in REQUIRED_WINDOWS[:-1]
    }

    with pytest.raises(RuntimeError, match="candidate competitor gate is missing cells"):
        evaluate_competitor_gate(reference, candidates)


def test_execution_contract_mismatch_is_rejected(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    payload = _reference_payload(data_dir)
    payload["execution_contract"]["execution"] = "same_close"
    reference_path = _write_reference(tmp_path / "competitor.json", payload)

    with pytest.raises(RuntimeError, match="execution-contract mismatch"):
        load_competitor_matrix(reference_path)

    good_reference = load_competitor_matrix(_write_reference(reference_path, _reference_payload(data_dir)))
    candidates = {
        f"{pool}/{window}": CompetitorMetrics(2.0, 0.1, 1)
        for pool in REQUIRED_POOLS
        for window in REQUIRED_WINDOWS
    }
    mismatch = replace(CANONICAL_EXECUTION_CONTRACT, execution="same_close")
    with pytest.raises(RuntimeError, match="candidate execution-contract mismatch"):
        evaluate_competitor_gate(
            good_reference,
            candidates,
            execution_contract=mismatch,
        )


@pytest.mark.parametrize(  # type: ignore[untyped-decorator, unused-ignore]
    ("mutation", "message"),
    [
        (
            lambda payload: payload["repositories"]["qwenquant"].update(commit="bad"),
            "commit provenance is malformed",
        ),
        (
            lambda payload: payload["repositories"]["trade"].update(adapter_source_sha256="bad"),
            "adapter provenance is malformed",
        ),
        (
            lambda payload: payload["repositories"]["aquant"].update(commit="0" * 40),
            "commit/adapter provenance mismatch",
        ),
        (
            lambda payload: payload["data_provenance"].update(manifest_sha256="bad"),
            "data provenance hash is malformed",
        ),
    ],
)
def test_malformed_commit_adapter_and_data_provenance_fail_closed(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    data_dir = _data_dir(tmp_path / "data")
    payload = _reference_payload(data_dir)
    mutation(payload)
    reference_path = _write_reference(tmp_path / "competitor.json", payload)

    with pytest.raises(RuntimeError, match=message):
        load_competitor_matrix(reference_path)


def test_local_data_mismatch_is_rejected_before_replay(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    reference_path = _write_reference(tmp_path / "competitor.json", _reference_payload(data_dir))
    (data_dir / "sh600000.csv").write_text("changed bytes\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="data provenance mismatch"):
        run_competitor_gate(
            data_dir=data_dir,
            reference_path=reference_path,
            runner=lambda *_: {
                "final_wealth": 2.0,
                "max_drawdown": 0.1,
                "account_orders": 1,
            },
        )


def test_required_window_and_metric_shape_are_strict(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    payload = _reference_payload(data_dir)
    payload["windows"].pop("h2_2023")
    reference_path = _write_reference(tmp_path / "competitor.json", payload)
    with pytest.raises(RuntimeError, match=r"missing required windows.*h2_2023"):
        load_competitor_matrix(reference_path)

    payload = _reference_payload(data_dir)
    payload["windows"]["bear_2022"] = {
        "start": "2022-01-04",
        "end": "2022-12-30",
    }
    _write_reference(reference_path, payload)
    with pytest.raises(RuntimeError, match=r"unexpected windows.*bear_2022"):
        load_competitor_matrix(reference_path)

    payload = _reference_payload(data_dir)
    payload["results"]["a/h2_2023/trade"]["account_orders"] = 1.5
    _write_reference(reference_path, payload)
    with pytest.raises(RuntimeError, match="order count is malformed"):
        load_competitor_matrix(reference_path)


def test_production_replays_score_only_the_official_ai_era_intervals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path / "data")
    payload = _reference_payload(data_dir)
    for result in payload["results"].values():
        result.update(final_wealth=0.90, max_drawdown=0.20, account_orders=2)
    reference_path = _write_reference(tmp_path / "competitor.json", payload)
    calls: list[tuple[str, str]] = []

    class FakeEngine:
        def __init__(self, observed_data_dir: str | Path) -> None:
            assert Path(observed_data_dir) == data_dir

        def backtest(
            self,
            *,
            symbols: tuple[str, ...],
            start: str,
            end: str,
        ) -> dict[str, Any]:
            assert symbols == ("sh600000",)
            calls.append((start, end))
            return {
                "final_wealth": 1.0,
                "max_drawdown": 0.10,
                "account_orders": 1,
            }

    monkeypatch.setattr("uquant.validation.competitor.ProductionEngine", FakeEngine)
    report = run_competitor_gate(
        data_dir=data_dir,
        reference_path=reference_path,
    )

    assert report["passed"]
    assert calls == [interval for _ in REQUIRED_POOLS for interval in AI_ERA_WINDOWS.values()]
    assert report["results"]["a/bull_crash_2025_2026"]["candidate"] == pytest.approx(
        {"final_wealth": 1.0, "max_drawdown": 0.10, "account_orders": 1}
    )


def test_best_of_three_requires_exactly_the_locked_competitors() -> None:
    values = {
        "aquant": CompetitorMetrics(1.0, 0.1, 2),
        "qwenquant": CompetitorMetrics(1.1, 0.2, 1),
    }
    with pytest.raises(ValueError, match="requires exactly"):
        best_of_three(values)


def test_duplicate_json_keys_are_not_silently_overwritten(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path / "data")
    serialized = json.dumps(_reference_payload(data_dir), sort_keys=True)
    duplicated = serialized.replace(
        '"schema_version": 1,',
        '"schema_version": 1, "schema_version": 1,',
        1,
    )
    reference_path = tmp_path / "competitor.json"
    reference_path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(RuntimeError, match="duplicate key: schema_version"):
        load_competitor_matrix(reference_path)
