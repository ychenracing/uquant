from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import uquant.engine as engine_module
import uquant.leader as leader_module
from research.candidate_runner import (
    CandidateRunner,
    CellTrace,
    DecisionTrace,
    _CausalReplayDataStore,
)
from research.risk_differential_models import canonical_bytes
from research.risk_replay_runtime import (
    ReplayCell,
    _uquant_actionability,
    materialize_causal_data_view,
    run_trade_cell,
    run_uquant_cell,
)
from uquant.leader import REFERENCE_UNIVERSE

ROOT = Path(__file__).parents[1]
_SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "risk_differential_runner_under_test",
    ROOT / "scripts/run_risk_differential.py",
)
assert _SCRIPT_SPEC is not None and _SCRIPT_SPEC.loader is not None
_SCRIPT = importlib.util.module_from_spec(_SCRIPT_SPEC)
_SCRIPT_SPEC.loader.exec_module(_SCRIPT)


def _cell(*, end: str = "2026-08-05") -> ReplayCell:
    return ReplayCell(
        cell_id="official_pool/window/a",
        axis="official_pool",
        window="window",
        universe="a",
        family="official",
        symbols=("sh600000",),
        start="2026-08-04",
        end=end,
    )


def _decision(
    date: str = "2026-08-05",
    *,
    targets: tuple[tuple[str, float, str, str], ...] = (),
    fills: tuple[tuple[str, str, str, int, float, str], ...] = (),
    equity: float = 2_000_000.0,
) -> DecisionTrace:
    evidence = {
        "base_family_active": {},
        "sentinel_causal_coverage_status": "NOT_READY",
    }
    return DecisionTrace(
        date=date,
        opportunity="WATCH",
        risk="NORMAL",
        transition_damage=0.0,
        family_votes=(),
        sector_guard_active=False,
        capital_budget_level=0,
        leaders=(),
        strategic_tag="",
        targets=targets,
        orders=(),
        fills=fills,
        equity=equity,
        risk_evidence=tuple((key, json.dumps(value)) for key, value in evidence.items()),
    )


def _write_prices(path: Path, rows: tuple[tuple[str, float], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "date": date,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1.0,
                "amount": 1.0,
            }
            for date, close in rows
        ]
    )
    frame.to_csv(path, index=False)


@dataclass(frozen=True)
class _Policy:
    regime_symbols: tuple[str, ...] = ("000300",)


def test_uquant_engine_receives_only_rows_through_cell_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prices(
        tmp_path / "sh600000.csv",
        (("2026-08-04", 10.0), ("2026-08-05", 11.0), ("2026-08-06", 999.0)),
    )

    class InspectingRunner:
        def __init__(self, data_dir: Path) -> None:
            visible = pd.read_csv(Path(data_dir) / "sh600000.csv")
            assert visible["date"].max() == "2026-08-05"

        def trace_cell(self, **_: object) -> CellTrace:
            return CellTrace("a", "window", (_decision(),))

    monkeypatch.setattr("research.risk_replay_runtime.CandidateRunner", InspectingRunner)
    run_uquant_cell(_cell(), tmp_path)


def test_materialized_causal_market_files_are_read_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_prices(source / "sh600000.csv", (("2026-08-05", 11.0),))
    materialize_causal_data_view(source, target, as_of="2026-08-05")
    assert (target / "sh600000.csv").stat().st_mode & 0o222 == 0


def test_materialized_causal_view_omits_symbols_with_no_visible_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _write_prices(source / "sh600000.csv", (("2026-08-06", 11.0),))
    _write_prices(source / "sh600001.csv", (("2026-08-05", 12.0),))
    materialize_causal_data_view(source, target, as_of="2026-08-05")
    assert not (target / "sh600000.csv").exists()
    assert (target / "sh600001.csv").is_file()


def test_candidate_runner_loads_only_causally_visible_reference_symbols(tmp_path: Path) -> None:
    visible, missing = REFERENCE_UNIVERSE[:2]
    (tmp_path / f"{visible}.csv").write_text("date,close\n2026-08-05,1\n", encoding="utf-8")
    symbols = CandidateRunner(tmp_path)._causal_load_symbols(("sh600000",))
    assert visible in symbols
    assert missing not in symbols


def test_candidate_runner_builds_explicit_visible_only_replay_universe(tmp_path: Path) -> None:
    visible = REFERENCE_UNIVERSE[0]
    (tmp_path / f"{visible}.csv").write_text("date,close\n2026-08-05,1\n", encoding="utf-8")
    runner = CandidateRunner(tmp_path)
    universe = runner.replay_universe(("sh600000",))
    assert universe.tradable_symbols == ("sh600000",)
    assert universe.reference_symbols == (visible,)
    assert universe.index_symbols == engine_module.INDEX_SYMBOLS


def test_candidate_runner_never_rebinds_process_reference_universes(tmp_path: Path) -> None:
    original_engine = engine_module.REFERENCE_UNIVERSE
    original_leader = leader_module.REFERENCE_UNIVERSE
    CandidateRunner(tmp_path).replay_universe(("sh600000",))
    assert engine_module.REFERENCE_UNIVERSE is original_engine
    assert leader_module.REFERENCE_UNIVERSE is original_leader


def test_causal_replay_manifest_omits_missing_and_future_only_symbols(tmp_path: Path) -> None:
    _write_prices(tmp_path / "sh600000.csv", (("2026-08-05", 11.0),))
    _write_prices(tmp_path / "sh600001.csv", (("2026-08-06", 12.0),))
    manifest = _CausalReplayDataStore(tmp_path).manifest(
        ("sh600000", "sh600001", "sh600002"), as_of="2026-08-05"
    )
    assert manifest.symbols == ("sh600000",)


def test_candidate_runner_routes_workspace_manifests_through_causal_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    class InspectingStore(_CausalReplayDataStore):
        def manifest(self, symbols: object, **kwargs: object):  # type: ignore[no-untyped-def,override]
            result = super().manifest(symbols, **kwargs)  # type: ignore[arg-type]
            calls.append(result.symbols)
            return result

    monkeypatch.setattr("research.candidate_runner._CausalReplayDataStore", InspectingStore)
    CandidateRunner(ROOT / "data" / "frozen").trace_cell(
        symbols=("sz300308",),
        start="2026-07-01",
        end="2026-07-02",
    )
    assert calls


def test_real_uquant_engine_prefix_is_byte_identical_after_appending_future_row(
    tmp_path: Path,
) -> None:
    extended = tmp_path / "extended"
    shutil.copytree(ROOT / "data/frozen", extended)
    source = extended / "sz300308.csv"
    with source.open("a", encoding="utf-8", newline="") as stream:
        stream.write("2099-01-04,999,999,999,999,1,1\n")
    cell = ReplayCell(
        cell_id="causal-prefix",
        axis="official_pool",
        window="causal-prefix",
        universe="a",
        family="official",
        symbols=("sz300308",),
        start="2026-07-01",
        end="2026-07-03",
    )
    before = run_uquant_cell(cell, ROOT / "data/frozen")
    after = run_uquant_cell(cell, extended)
    assert canonical_bytes(before) == canonical_bytes(after)


def test_trade_engine_receives_only_rows_through_cell_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _write_prices(
        data / "600000.csv",
        (("2026-08-04", 10.0), ("2026-08-05", 11.0), ("2026-08-06", 999.0)),
    )
    _write_prices(data / "000300.csv", (("2026-08-04", 10.0), ("2026-08-05", 11.0)))

    class Engine:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def run(self, *_: object, **kwargs: object) -> dict[str, object]:
            visible = pd.read_csv(Path(str(kwargs["data_dir"])) / "600000.csv")
            assert visible["date"].max() == "2026-08-05"
            return {
                "risk_governance_series": [],
                "equity_curve": {"assets": np.array([], dtype=float)},
                "warmup_health": {
                    "warmup_status": "READY",
                    "indicator_ready_ratio": 1.0,
                    "reference_basket_ready_ratio": 1.0,
                },
            }

    route = SimpleNamespace(
        qf=SimpleNamespace(PortfolioPolicy=_Policy),
        ProductionReplayEngine=Engine,
    )
    monkeypatch.setattr("research.risk_replay_runtime.importlib.import_module", lambda _: route)
    run_trade_cell(_cell(), tmp_path, data)


def test_trade_terminal_warmup_is_not_back_projected_and_missing_admission_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    _write_prices(data / "600000.csv", (("2026-08-05", 11.0),))
    _write_prices(data / "000300.csv", (("2026-08-05", 11.0),))

    class Engine:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def run(self, *_: object, **__: object) -> dict[str, object]:
            return {
                "risk_governance_series": [
                    {"date": "2026-08-05", "risk_level": 0, "risk_confidence": 0.7}
                ],
                "equity_curve": {"assets": np.array([2_000_000.0])},
                "warmup_health": {
                    "warmup_status": "READY",
                    "indicator_ready_ratio": 1.0,
                    "reference_basket_ready_ratio": 1.0,
                },
            }

    route = SimpleNamespace(
        qf=SimpleNamespace(PortfolioPolicy=_Policy),
        ProductionReplayEngine=Engine,
    )
    monkeypatch.setattr("research.risk_replay_runtime.importlib.import_module", lambda _: route)
    row = run_trade_cell(_cell(), tmp_path, data)["trade"][0]
    # The terminal READY label cannot make the activation date READY: the only
    # regime file starts after the cell activation date, so the causal startup
    # contract must fail closed.
    assert row["status"] == "NOT_READY"
    assert row["block_new_entries"] is None
    assert row["block_pyramiding"] is None


def test_existing_gross_exposure_uses_marked_holdings_not_target_plan(tmp_path: Path) -> None:
    _write_prices(tmp_path / "sh600000.csv", (("2026-08-05", 15.0),))
    trace = _decision(
        targets=(("sh600000", 0.20, "TACTICAL", "target"),),
        fills=(("2026-08-05", "BUY", "sh600000", 100, 10.0, "fill"),),
        equity=2_000.0,
    )
    facts = _uquant_actionability((trace,), tmp_path)
    assert facts["2026-08-05"]["gross"] == pytest.approx(0.75)


def test_normalized_uquant_facts_bind_decision_date_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prices(tmp_path / "sh600000.csv", (("2026-08-05", 15.0),))
    decision = _decision()

    class Runner:
        def __init__(self, _: Path) -> None:
            pass

        def trace_cell(self, **_: object) -> CellTrace:
            return CellTrace("a", "window", (decision,))

    monkeypatch.setattr("research.risk_replay_runtime.CandidateRunner", Runner)
    result = run_uquant_cell(_cell(), tmp_path)
    identity = result["base"][0]["decision_identity"]
    assert identity["date"] == decision.date
    assert len(identity["decision_digest_sha256"]) == 64
    assert result["sentinel"][0]["decision_identity"] == identity


def test_cell_cache_rejects_old_identity_and_payload_mutation(tmp_path: Path) -> None:
    cell = _cell()
    path = tmp_path / "cache.json"
    payload = {
        "cell": asdict(cell),
        "uquant": {"dates": ["2026-08-05"]},
        "trade": {"dates": ["2026-08-05"]},
        "runtime_identity": {"python_hash_seed": "0"},
        "cache_identity": "current",
    }
    payload["result_sha256"] = _SCRIPT._replay_result_sha256(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _SCRIPT._load_replay_cache(path, cell, "current") is not None
    assert _SCRIPT._load_replay_cache(path, cell, "old") is None
    payload["uquant"]["dates"].append("2026-08-06")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _SCRIPT._load_replay_cache(path, cell, "current") is None


def test_cell_identity_tracks_causal_prefix_and_both_engine_sources(tmp_path: Path) -> None:
    _write_prices(
        tmp_path / "sh600000.csv",
        (("2026-08-04", 10.0), ("2026-08-05", 11.0), ("2026-08-06", 12.0)),
    )
    sources = {
        "uquant": {"commit": "u1", "python_source_sha256": "us"},
        "trade": {
            "commit": "t1",
            "python_source_sha256": "ts",
            "risk_source_sha256": "tr",
        },
    }
    first = _SCRIPT._cell_cache_identity(
        _cell(), tmp_path, source_registry=sources, adapter_sha256="adapter"
    )
    _write_prices(
        tmp_path / "sh600000.csv",
        (
            ("2026-08-04", 10.0),
            ("2026-08-05", 11.0),
            ("2026-08-06", 999.0),
        ),
    )
    assert _SCRIPT._cell_cache_identity(
        _cell(), tmp_path, source_registry=sources, adapter_sha256="adapter"
    ) == first
    _write_prices(
        tmp_path / "sh600000.csv",
        (("2026-08-04", 10.0), ("2026-08-05", 99.0), ("2026-08-06", 999.0)),
    )
    assert _SCRIPT._cell_cache_identity(
        _cell(), tmp_path, source_registry=sources, adapter_sha256="adapter"
    ) != first
    changed_sources = {**sources, "trade": {**sources["trade"], "commit": "t2"}}
    assert _SCRIPT._cell_cache_identity(
        _cell(), tmp_path, source_registry=changed_sources, adapter_sha256="adapter"
    ) != first


def test_contract_axes_are_closed_and_unique() -> None:
    axes = _SCRIPT.RISK_DIFFERENTIAL_AXES
    assert _SCRIPT.validate_contract_axes(axes) == axes
    with pytest.raises(ValueError, match="unique"):
        _SCRIPT.validate_contract_axes((*axes, axes[0]))
    with pytest.raises(ValueError, match="unknown"):
        _SCRIPT.validate_contract_axes((*axes[:-1], "invented_axis"))


def test_standard_warning_sets_distinguish_agreement_from_all_silent() -> None:
    assert _SCRIPT._standard_warning_sets(0, 0, 0) == ("ALL_AGREE", "ALL_SILENT")
    assert _SCRIPT._standard_warning_sets(2, 2, 2) == ("ALL_AGREE",)
    assert _SCRIPT._standard_warning_sets(1, 0, 0) == ("TRADE_ONLY",)
    assert _SCRIPT._standard_warning_sets(1, 0, 1) == ("TRADE_AND_SENTINEL_ONLY",)
    assert _SCRIPT._standard_warning_sets(None, 0, 0) == ()


def test_capability_inventory_explicitly_records_early_sector_risk() -> None:
    records = {item.capability_id: item for item in _SCRIPT.capability_inventory()}
    record = records["risk.early_sector_risk"]
    assert "quantfusion/engine/sector_risk.py" in record.trade_source
    assert record.exact_transfer_possible is False


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "requirements.txt").write_text("pandas==2.0\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


def test_checkout_identity_is_derived_and_source_movement_fails_closed(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    first = _SCRIPT._derive_checkout_identity(
        checkout,
        lock_files=("requirements.txt",),
        risk_files=("module.py",),
    )
    assert first["commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert first["python_source_sha256"] == _SCRIPT._hash_checkout_python_sources(checkout)
    (checkout / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    moved = _SCRIPT._derive_checkout_identity(
        checkout,
        lock_files=("requirements.txt",),
        risk_files=("module.py",),
    )
    with pytest.raises(RuntimeError, match="moved"):
        _SCRIPT._require_unchanged_checkout(first, moved, label="trade")


def test_preregistration_rechecks_sources_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    benchmark = root / "benchmarks"
    benchmark.mkdir(parents=True)
    (benchmark / "current_heads_comparison_contract.json").write_text(
        json.dumps({"payload_sha256": "m" * 64, "official_pools": {}, "windows": {}}),
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline"
    trade = tmp_path / "trade"
    baseline.mkdir()
    trade.mkdir()
    uquant = {
        "commit": _SCRIPT.STARTING_MAIN,
        "python_source_sha256": "u" * 64,
        "lock_sha256": "l" * 64,
    }
    challenger = {
        "commit": _SCRIPT.TRADE_COMMIT,
        "python_source_sha256": "t" * 64,
        "lock_sha256": "k" * 64,
        "risk_source_sha256": "r" * 64,
    }
    calls = {baseline: 0, trade: 0}

    def moving_identity(path: Path, **_: object) -> dict[str, str]:
        calls[path] += 1
        identity = dict(uquant if path == baseline else challenger)
        if path == baseline and calls[path] == 2:
            identity["python_source_sha256"] = "x" * 64
        return identity

    monkeypatch.setattr(_SCRIPT, "_derive_checkout_identity", moving_identity)
    with pytest.raises(RuntimeError, match="uquant source checkout moved"):
        _SCRIPT.preregister(
            root,
            baseline_root=baseline,
            trade_root=trade,
            frozen_at_utc="2026-08-21T00:00:00Z",
        )
    assert not (benchmark / "risk_differential_contract.json").exists()
