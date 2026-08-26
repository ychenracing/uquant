from __future__ import annotations

import builtins
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from research.current_heads import (
    MATRIX_STATUSES,
    REQUIRED_METRICS,
    REQUIRED_SYSTEMS,
    canonical_sha256,
    load_comparison_contract,
    load_source_registry,
    python_source_sha256,
    validate_matrix_cell,
)
from research.current_heads import (
    main as current_heads_main,
)
from uquant.validation.ai_era import AI_ERA_ACUTE_WINDOWS, AI_ERA_WINDOWS
from uquant.validation.competitor import CANONICAL_EXECUTION_CONTRACT

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_current_heads_competitor_matrix.py"
SPEC = importlib.util.spec_from_file_location("current_heads_runner_under_test", RUNNER)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_adapter_initialization_failure_remains_a_replay_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    implementation = runner._implementation
    monkeypatch.setattr(
        implementation,
        "observable_symbols_in_window",
        lambda *_args, **_kwargs: ("sz300308",),
    )
    original_import = builtins.__import__

    class AdapterInitProbeError(RuntimeError):
        pass

    def fail_adapter_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "research.window_competitor_adapter":
            raise AdapterInitProbeError("promotion baseline unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fail_adapter_import)
    request = {
        "system": "trade",
        "axis": "official_pool",
        "name": "probe",
        "family": "official_pool",
        "window": "period",
        "start": "2025-01-02",
        "end": "2026-07-31",
        "acute_start": "2025-01-02",
        "acute_end": "2026-07-31",
        "symbols": ["sz300308"],
    }
    paths = {
        "data_root": str(tmp_path),
        "repository_root": str(ROOT),
        "qwen_root": "qwen",
        "aquant_root": "aquant",
        "trade_root": "trade",
        "trade_data_root": str(tmp_path),
    }

    result = implementation._execute_competitor_request((request, paths))

    assert result["status"] == "REPLAY_ERROR"
    assert result["error"] == {
        "class": "AdapterInitProbeError",
        "message": "promotion baseline unavailable",
    }


def test_current_heads_contract_freezes_every_shared_comparison_axis() -> None:
    contract = load_comparison_contract(ROOT / "benchmarks/current_heads_comparison_contract.json")

    assert tuple(contract["systems"]) == REQUIRED_SYSTEMS
    assert contract["market"] == "A-share AI supply chain"
    assert contract["execution_contract"] == {
        **CANONICAL_EXECUTION_CONTRACT.to_payload(),
        "stock_adjustment": "qfq",
        "index_adjustment": "raw",
        "position_direction": "cash_long_only",
        "star_board_rules": True,
        "price_limits": True,
        "capacity": True,
    }
    assert contract["windows"] == {
        name: {
            "start": bounds[0],
            "end": bounds[1],
            "acute_start": AI_ERA_ACUTE_WINDOWS[name][0],
            "acute_end": AI_ERA_ACUTE_WINDOWS[name][1],
        }
        for name, bounds in AI_ERA_WINDOWS.items()
    }
    assert tuple(contract["official_pools"]) == ("a", "b", "c", "d", "e")
    assert contract["generalization"]["records_per_window"] == 39
    assert contract["generalization"]["random_pool_sizes"] == [5, 9, 15, 20]
    assert contract["generalization"]["random_seed_indexes"] == [0, 1, 2, 3, 4]
    assert tuple(contract["metrics"]) == REQUIRED_METRICS
    assert tuple(contract["statuses"]) == MATRIX_STATUSES
    assert contract["expected_cells"] == {
        "official_pool": 120,
        "generalization": 936,
        "total": 1056,
    }


def test_contract_payload_hash_fails_closed_after_any_edit(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/current_heads_comparison_contract.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["execution_contract"]["one_way_slippage"] = 0.0
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="payload SHA-256 mismatch"):
        load_comparison_contract(changed)


def test_python_source_hash_is_stable_by_relative_path_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "b.py").write_text("B = 2\n", encoding="utf-8")
    (first / "a.py").write_text("A = 1\n", encoding="utf-8")
    (second / "a.py").write_text("A = 1\n", encoding="utf-8")
    (second / "b.py").write_text("B = 2\n", encoding="utf-8")

    assert python_source_sha256(first) == python_source_sha256(second)
    (second / "b.py").write_text("B = 3\n", encoding="utf-8")
    assert python_source_sha256(first) != python_source_sha256(second)


def test_source_registry_binds_all_four_remote_heads_and_adapter() -> None:
    registry = load_source_registry(
        ROOT / "benchmarks/current_heads_source_registry.json",
        adapter_path=ROOT / "scripts/run_current_heads_competitor_matrix.py",
        expected_heads={
            "uquant": "ea24f1837f8b7f2d91e73a5d3c70875f2ea98015",
            "trade": "2066fbf0f99be94142c5d0cb0b6c99d276c2472d",
            "qwenquant": "63e05fe7adc2eae67d78e2cfca6222f88e041d89",
            "aquant": "55009a628515a0d612034c132bc90d21cf720c25",
        },
    )

    assert tuple(registry["systems"]) == REQUIRED_SYSTEMS
    assert all(registry["repositories"][name]["read_only"] for name in REQUIRED_SYSTEMS)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("commit", "short", "commit must be a 40-character SHA"),
        ("tree_sha", "short", "tree_sha must be a 40-character SHA"),
        ("python_source_sha256", "short", "python_source_sha256 must be SHA-256"),
        ("lock_sha256", "short", "lock_sha256 must be SHA-256"),
    ),
)
def test_source_registry_rejects_missing_or_malformed_identity(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    source = ROOT / "benchmarks/current_heads_source_registry.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["repositories"]["trade"][field] = value
    body = {key: item for key, item in payload.items() if key != "payload_sha256"}
    payload["payload_sha256"] = canonical_sha256(body)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_source_registry(changed)


def test_source_registry_rejects_a_different_claimed_remote_head(tmp_path: Path) -> None:
    source = ROOT / "benchmarks/current_heads_source_registry.json"

    with pytest.raises(ValueError, match="trade remote HEAD mismatch"):
        load_source_registry(
            source,
            expected_heads={
                "uquant": "ea24f1837f8b7f2d91e73a5d3c70875f2ea98015",
                "trade": "0" * 40,
                "qwenquant": "63e05fe7adc2eae67d78e2cfca6222f88e041d89",
                "aquant": "55009a628515a0d612034c132bc90d21cf720c25",
            },
        )


def _market_csv(path: Path) -> Path:
    path.write_text(
        "date,open,high,low,close,volume,amount\n"
        "2023-01-02,9,10,8,9.5,100,950\n"
        "2023-01-03,10,11,9,10.5,100,1050\n"
        "2023-01-04,11,12,10,11.5,100,1150\n",
        encoding="utf-8",
    )
    return path


def test_bounded_staging_rejects_missing_columns_and_hides_future_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _market_csv(source / "sz300308.csv")

    result = runner.stage_bounded_market_data(source, target, through="2023-01-03")

    staged = (target / "sz300308.csv").read_text(encoding="utf-8")
    assert "2023-01-03" in staged
    assert "2023-01-04" not in staged
    assert result["files"] == 1
    assert len(result["sha256"]) == 64

    (source / "sz300308.csv").write_text(
        "date,open,high,low,close,volume\n2023-01-03,1,1,1,1,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="required columns"):
        runner.stage_bounded_market_data(source, tmp_path / "invalid", through="2023-01-03")


def test_point_in_time_visibility_never_invents_a_prelisting_row(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    _market_csv(source / "sz300308.csv")

    runner.stage_bounded_market_data(source, target, through="2023-01-04")

    assert runner.visible_symbols(target, ("sz300308",), as_of="2023-01-01") == ()
    assert runner.visible_symbols(target, ("sz300308",), as_of="2023-01-03") == (
        "sz300308",
    )


def _raw_worker_row() -> dict[str, object]:
    return {
        "system": "trade",
        "pool": "cell",
        "window": "h1_2023",
        "requested_symbols": ["sz300308"],
        "effective_symbols": ["sz300308"],
        "start": "2023-01-03",
        "end": "2023-01-04",
        "final_wealth": 1.1,
        "total_return": 0.1,
        "max_drawdown": 0.02,
        "account_orders": 1,
        "turnover": 0.1,
        "order_ledger": [],
        "equity_curve": [
            {"date": "2023-01-03", "equity": 2_000_000.0},
            {"date": "2023-01-04", "equity": 2_200_000.0},
        ],
        "fills": [
            {
                "fill_date": "2023-01-03",
                "signal_date": "2023-01-02",
                "symbol": "sz300308",
                "side": "BUY",
                "price": 10.0,
                "shares": 100,
                "reason": "production",
            }
        ],
        "risk_reductions": [],
        "risk_events": [],
        "replacements": [],
        "extra": {},
    }


def test_worker_row_normalization_rejects_empty_nan_and_future_evidence(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _market_csv(data / "sz300308.csv")
    request = runner.ReplayRequest(
        system="trade",
        axis="official_pool",
        name="a",
        family="official_pool",
        window="h1_2023",
        start="2023-01-03",
        end="2023-01-04",
        acute_start="2023-01-03",
        acute_end="2023-01-04",
        symbols=("sz300308",),
    )

    normalized = runner.normalize_replay_row(request, _raw_worker_row(), data_dir=data)
    assert tuple(normalized) == REQUIRED_METRICS
    assert normalized["final_wealth"] == pytest.approx(1.1)

    unprefixed = _raw_worker_row()
    unprefixed["fills"][0]["symbol"] = "300308"  # type: ignore[index]
    assert runner.normalize_replay_row(request, unprefixed, data_dir=data)[
        "top1_concentration"
    ] == pytest.approx(1.0)

    empty = _raw_worker_row()
    empty["equity_curve"] = []
    with pytest.raises(ValueError, match="equity curve is empty"):
        runner.normalize_replay_row(request, empty, data_dir=data)

    nan = _raw_worker_row()
    nan["final_wealth"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        runner.normalize_replay_row(request, nan, data_dir=data)

    future = _raw_worker_row()
    future["equity_curve"] = [
        *future["equity_curve"],  # type: ignore[misc]
        {"date": "2023-01-05", "equity": 2_300_000.0},
    ]
    with pytest.raises(ValueError, match="outside its requested window"):
        runner.normalize_replay_row(request, future, data_dir=data)


def test_status_cells_are_mutually_exclusive_and_errors_remain_explicit() -> None:
    request = runner.ReplayRequest(
        system="trade",
        axis="generalization",
        name="subindustry__small",
        family="subindustry",
        window="h1_2023",
        start="2023-01-03",
        end="2023-06-30",
        acute_start="2023-04-20",
        acute_end="2023-05-25",
        symbols=("sz300308",),
    )
    provenance = {
        "system_commit": "0" * 40,
        "data_sha256": "1" * 64,
        "config_sha256": "2" * 64,
        "runtime_sha256": "3" * 64,
        "evidence_sha256": "4" * 64,
    }

    replay_error = runner.build_matrix_cell(
        request,
        status="REPLAY_ERROR",
        metrics=None,
        error={"class": "RuntimeError", "message": "kept"},
        provenance=provenance,
    )
    insufficient = runner.build_matrix_cell(
        request,
        status="INSUFFICIENT_SAMPLE",
        metrics=None,
        error={"class": "InsufficientSample", "message": "one symbol"},
        provenance=provenance,
    )
    assert replay_error["error"]["message"] == "kept"
    assert insufficient["status"] == "INSUFFICIENT_SAMPLE"

    with pytest.raises(ValueError, match="SUCCESS requires metrics and no error"):
        runner.build_matrix_cell(
            request,
            status="SUCCESS",
            metrics=None,
            error={"class": "RuntimeError", "message": "bad"},
            provenance=provenance,
        )


def test_success_cell_accepts_json_sorted_complete_metric_keys() -> None:
    request = runner.ReplayRequest(
        system="aquant",
        axis="generalization",
        name="full",
        family="full",
        window="h1_2023",
        start="2023-01-03",
        end="2023-06-30",
        acute_start="2023-04-20",
        acute_end="2023-05-25",
        symbols=("sz300308", "sz300502"),
    )
    metrics = {field: 0 for field in sorted(REQUIRED_METRICS)}
    metrics["final_wealth"] = 1.0
    cell = runner.build_matrix_cell(
        request,
        status="SUCCESS",
        metrics=metrics,
        error=None,
        provenance={
            "system_commit": "0" * 40,
            "data_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "runtime_sha256": "3" * 64,
            "evidence_sha256": "4" * 64,
        },
    )

    assert set(cell["metrics"]) == set(REQUIRED_METRICS)


def test_aquant_cells_use_fresh_spawned_processes() -> None:
    assert runner.competitor_executor_policy("aquant") == ("spawn", 1)
    assert runner.competitor_executor_policy("qwenquant") == ("fork", None)
    assert runner.competitor_executor_policy("trade") == ("fork", None)


def test_matrix_readback_rejects_mutually_inconsistent_status_payloads() -> None:
    contract = load_comparison_contract(
        ROOT / "benchmarks/current_heads_comparison_contract.json"
    )
    registry = load_source_registry(
        ROOT / "benchmarks/current_heads_source_registry.json"
    )
    request = runner.ReplayRequest(
        system="trade",
        axis="official_pool",
        name="a",
        family="official_pool",
        window="h1_2023",
        start="2023-01-03",
        end="2023-06-30",
        acute_start="2023-04-20",
        acute_end="2023-05-25",
        symbols=tuple(contract["official_pools"]["a"]),
    )
    cell = runner.build_matrix_cell(
        request,
        status="REPLAY_ERROR",
        metrics=None,
        error={"class": "RuntimeError", "message": "preserved"},
        provenance={
            "system_commit": registry["repositories"]["trade"]["commit"],
            "data_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "runtime_sha256": "3" * 64,
            "evidence_sha256": "4" * 64,
        },
    )
    validate_matrix_cell(cell, contract=contract, registry=registry)

    cell["metrics"] = {field: 0.0 for field in REQUIRED_METRICS}
    with pytest.raises(ValueError, match="REPLAY_ERROR requires explicit error only"):
        validate_matrix_cell(cell, contract=contract, registry=registry)


def test_matrix_readback_rejects_wrong_official_pool_membership() -> None:
    contract = load_comparison_contract(
        ROOT / "benchmarks/current_heads_comparison_contract.json"
    )
    registry = load_source_registry(
        ROOT / "benchmarks/current_heads_source_registry.json"
    )
    request = runner.ReplayRequest(
        system="qwenquant",
        axis="official_pool",
        name="a",
        family="official_pool",
        window="h1_2023",
        start="2023-01-03",
        end="2023-06-30",
        acute_start="2023-04-20",
        acute_end="2023-05-25",
        symbols=("sz000001",),
    )
    cell = runner.build_matrix_cell(
        request,
        status="REPLAY_ERROR",
        metrics=None,
        error={"class": "RuntimeError", "message": "preserved"},
        provenance={
            "system_commit": registry["repositories"]["qwenquant"]["commit"],
            "data_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "runtime_sha256": "3" * 64,
            "evidence_sha256": "4" * 64,
        },
    )

    with pytest.raises(ValueError, match="official pool membership differs"):
        validate_matrix_cell(cell, contract=contract, registry=registry)


def test_matrix_readback_keeps_empty_effective_insufficient_sample() -> None:
    contract = load_comparison_contract(
        ROOT / "benchmarks/current_heads_comparison_contract.json"
    )
    registry = load_source_registry(
        ROOT / "benchmarks/current_heads_source_registry.json"
    )
    request = runner.ReplayRequest(
        system="uquant",
        axis="generalization",
        name="subindustry__passives",
        family="subindustry",
        window="h1_2023",
        start="2023-01-03",
        end="2023-06-30",
        acute_start="2023-04-20",
        acute_end="2023-05-25",
        symbols=("sz000636",),
    )
    cell = runner.build_matrix_cell(
        request,
        status="INSUFFICIENT_SAMPLE",
        metrics=None,
        error={"class": "InsufficientSample", "message": "one symbol"},
        effective_symbols=(),
        provenance={
            "system_commit": registry["repositories"]["uquant"]["commit"],
            "data_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "runtime_sha256": "3" * 64,
            "evidence_sha256": "4" * 64,
        },
    )

    validate_matrix_cell(cell, contract=contract, registry=registry)


def test_matrix_validator_cli_reads_committed_matrix() -> None:
    assert current_heads_main([]) == 0


def test_competitor_cli_exposes_only_domain_named_generalization_inputs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as prepare_exit:
        runner._implementation.main(["prepare", "--help"])
    assert prepare_exit.value.code == 0
    prepare_help = capsys.readouterr().out
    assert "--generalization-baseline" in prepare_help
    assert "--phase2" not in prepare_help

    with pytest.raises(SystemExit) as assemble_exit:
        runner._implementation.main(["assemble", "--help"])
    assert assemble_exit.value.code == 0
    assemble_help = capsys.readouterr().out
    assert "--generalization-summary" in assemble_help
    assert "--generalization-matrix" in assemble_help
    assert "--phase2-compact" not in assemble_help
    assert "--phase2-raw" not in assemble_help
