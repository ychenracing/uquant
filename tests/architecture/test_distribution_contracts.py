from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pandas as pd
import pytest

from uquant.account import account_from_dict
from uquant.attribution import validate_attribution_against_engine_result
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.validation.absolute_generalization._acceptance_evidence import current_candidate_contract
from uquant.validation.absolute_generalization.champion_physical import (
    validate_champion_physical_links,
    validate_champion_session_streams,
)
from uquant.validation.absolute_generalization.metrics import assert_unique_execution_rows
from uquant.validation.control_plane import validate_engine_control_plane
from uquant.validation.generalization.metrics import symbol_pnl_from_result

from ._analysis import (
    PUBLIC_API_PATH,
    ROOT,
    canonical_sha256,
    tracked_file_inventory,
)
from ._analysis_authorities import HISTORICAL_PUBLIC_API_PATH

CURRENT_SURFACE_BASE = "105695aacd3d1c7e62705f64188da88d202db4cd"

def test_same_named_module_and_package_never_compete_for_import_authority() -> None:
    conflicts = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "uquant").rglob("*.py")
        if path.name != "__init__.py" and path.with_suffix("").is_dir()
    )
    assert conflicts == []


def test_baseline_tracked_file_authority_is_complete_and_immutable(
    baseline_inventory: dict[str, object],
) -> None:
    from ._baseline import BASELINE_COMMIT

    baseline = baseline_inventory["baseline"]
    authority = baseline_inventory["tracked_file_authority"]
    assert isinstance(baseline, Mapping)
    assert isinstance(authority, Mapping)
    assert baseline["commit"] == BASELINE_COMMIT
    assert authority == tracked_file_inventory(ROOT, BASELINE_COMMIT)
    assert authority["canonical_sha256"] == canonical_sha256(authority["entries"])


def test_baseline_source_surface_and_public_contract_have_closed_integrity_hashes(
    baseline_inventory: dict[str, object],
) -> None:
    from ._analysis import git_python_sources, production_source_surface
    from ._baseline import BASELINE_COMMIT

    baseline = baseline_inventory["baseline"]
    public_contract = baseline_inventory["public_api_contract"]
    assert isinstance(baseline, Mapping)
    assert isinstance(public_contract, Mapping)
    source_surface = baseline["production_source_surface"]
    assert isinstance(source_surface, Mapping)
    entries = source_surface["entries"]
    assert isinstance(entries, list)
    assert source_surface["canonical_sha256"] == canonical_sha256(entries)
    assert source_surface["tree_sha256"] == canonical_sha256(
        [(entry["path"], entry["sha256"]) for entry in entries]
    )
    assert all(
        isinstance(entry, Mapping)
        and str(entry["path"]).startswith("uquant/")
        and str(entry["path"]).endswith(".py")
        for entry in entries
    )
    frozen_public_api = subprocess.check_output(
        [
            "git",
            "show",
            f"{CURRENT_SURFACE_BASE}:{HISTORICAL_PUBLIC_API_PATH.relative_to(ROOT).as_posix()}",
        ],
        cwd=ROOT,
    )
    assert public_contract["sha256"] == hashlib.sha256(frozen_public_api).hexdigest()
    current_public_api = json.loads(PUBLIC_API_PATH.read_text(encoding="utf-8"))
    assert current_public_api["contract_sha256"] == canonical_sha256(
        current_public_api["contract"]
    )
    assert source_surface == production_source_surface(
        git_python_sources(ROOT, BASELINE_COMMIT)
    )


def test_initial_debt_is_recomputed_from_the_immutable_git_tree(
    baseline_inventory: dict[str, object],
) -> None:
    from ._analysis import architecture_snapshot, git_python_sources, measured_debt
    from ._baseline import BASELINE_COMMIT, BASELINE_MODULE_AUTHORITIES

    debt = baseline_inventory["architecture_debt"]
    frozen_architecture = baseline_inventory["architecture"]
    assert isinstance(debt, Mapping)
    assert isinstance(frozen_architecture, Mapping)
    baseline_snapshot = architecture_snapshot(
        source_texts=git_python_sources(ROOT, BASELINE_COMMIT),
        module_authorities=BASELINE_MODULE_AUTHORITIES,
    )
    frozen_graph = frozen_architecture["import_graph"]
    observed_graph = baseline_snapshot["import_graph"]
    assert isinstance(frozen_graph, Mapping)
    assert isinstance(observed_graph, Mapping)
    assert frozen_graph["module_authorities"] == BASELINE_MODULE_AUTHORITIES
    assert observed_graph["module_authorities"] == BASELINE_MODULE_AUTHORITIES
    expected_initial = measured_debt(baseline_snapshot)
    assert debt["initial"] == expected_initial
    assert debt["initial_sha256"] == canonical_sha256(expected_initial)


@pytest.mark.parametrize("registry_change", ("add", "remove", "update"))
def test_immutable_baseline_recomputation_ignores_future_live_registry_changes(
    baseline_inventory: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    registry_change: str,
) -> None:
    from . import _analysis

    future_registry = dict(_analysis.MODULE_AUTHORITIES)
    if registry_change == "add":
        future_registry["uquant.future_domain"] = "production_safe"
    elif registry_change == "remove":
        del future_registry["uquant.opportunity"]
    else:
        future_registry["uquant.cli"] = "validation_runner"
    monkeypatch.setattr(_analysis, "MODULE_AUTHORITIES", future_registry)

    test_initial_debt_is_recomputed_from_the_immutable_git_tree(baseline_inventory)


def test_generator_accepts_an_explicit_reachable_baseline(
    tmp_path: Path,
) -> None:
    from ._baseline import BASELINE_COMMIT
    from ._generate_baselines import verify_generation_context

    candidate = tmp_path / "reachable-baseline"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-checkout", str(ROOT), str(candidate)],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", BASELINE_COMMIT],
        cwd=candidate,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=candidate,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == BASELINE_COMMIT
    verified = verify_generation_context(
        baseline_root=ROOT,
        baseline_commit=BASELINE_COMMIT,
        candidate_root=candidate,
    )
    assert verified == BASELINE_COMMIT


def test_generator_cli_requires_portable_baseline_arguments_and_no_caller_metrics() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.architecture._generate_baselines", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--baseline-root" in completed.stdout
    assert "--baseline-commit" in completed.stdout
    assert "--pytest-wall-seconds" not in completed.stdout
    assert "--pytest-peak-rss-kib" not in completed.stdout


@pytest.mark.parametrize("scenario_index", range(3))
def test_three_current_replays_preserve_frozen_inputs_and_execution_integrity(
    baseline_inventory: dict[str, object], scenario_index: int
) -> None:
    scenarios = baseline_inventory["representative_replays"]
    assert isinstance(scenarios, list)
    assert len(scenarios) == 3
    expected = scenarios[scenario_index]
    assert isinstance(expected, Mapping)
    contract = current_candidate_contract()
    assert contract["superseded_behavior_contracts"][0] == (
        "Exact old Target/Order/Fill and exclusive economic-owner/epoch trajectories."
    )
    assert str(expected["requested_end"]) < contract["future_holdout_boundary"]
    symbols = tuple(str(symbol) for symbol in expected["symbols"])
    engine = ProductionEngine(ROOT / "data" / "frozen")
    # Keep the archived literal outcomes unchanged. The current contract instead
    # requires real causal execution, reconciled economics, and current identities.
    observed = engine.backtest(
        start=str(expected["requested_start"]),
        end=str(expected["requested_end"]),
        symbols=symbols,
    )
    validate_champion_session_streams(
        observed, start=str(expected["requested_start"]), end=str(expected["requested_end"]),
    )
    # Match the existing generalization runner's enrichment of a raw backtest.
    observed["symbol_pnl"] = symbol_pnl_from_result(observed, {
        symbol: engine.workspace.price(symbol, pd.Timestamp(observed["end"]))
        for symbol in observed["final_account"]["positions"]
    })
    sessions = tuple(row["date"] for row in observed["equity_curve"])
    attribution = validate_attribution_against_engine_result(
        observed, economic_start=observed["start"], economic_end=observed["end"],
        trusted_sessions=sessions,
        trusted_close=lambda symbol, session: engine.workspace.price(symbol, pd.Timestamp(session)),
        require_daily_replay_evidence=True,
    )
    validate_engine_control_plane(
        observed,
        economic_start=observed["start"],
        economic_end=observed["end"],
        expected_sessions=sessions,
        expected_config=engine.cfg,
        expected_code_sha256=code_fingerprint(),
        attribution=attribution,
    )
    assert_unique_execution_rows(
        final_account=observed["final_account"],
        trace=observed["decision_trace"],
        allowed_symbols=symbols,
    )
    validate_champion_physical_links(
        account_from_dict(observed["final_account"]), observed["decision_trace"],
    )
    for event in observed["sentinel_events"]:
        assert 0.0 <= event["target_gross_cap"] <= event["base_target_gross_cap"] <= engine.cfg.max_gross


def test_performance_baseline_records_wall_rss_and_pytest_time(
    baseline_inventory: dict[str, object],
) -> None:
    performance = baseline_inventory["performance_baseline"]
    assert isinstance(performance, Mapping)
    replay = performance["representative_replay"]
    pytest_run = performance["pytest_core_contracts"]
    assert isinstance(replay, Mapping)
    assert isinstance(pytest_run, Mapping)
    assert float(replay["wall_seconds"]) > 0.0
    assert int(replay["peak_rss_kib"]) > 0
    raw = pytest_run["raw_evidence"]
    assert isinstance(raw, Mapping)
    assert raw["command"] == [
        "python",
        "-m",
        "pytest",
        "-q",
        "tests/test_config_contracts.py",
        "tests/test_account_broker_schema.py",
        "tests/test_engine_contracts.py",
        "tests/test_execution.py",
    ]
    assert raw["exit_status"] == 0
    counts = raw["test_counts"]
    assert isinstance(counts, Mapping)
    assert int(counts["total"]) > 0
    assert counts == {
        "total": counts["total"],
        "passed": counts["total"],
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    environment = raw["environment"]
    assert isinstance(environment, Mapping)
    baseline = baseline_inventory["baseline"]
    assert isinstance(baseline, Mapping)
    assert environment == {
        "python_implementation": baseline["python_implementation"],
        "python_version": baseline["python_version"],
        "platform": baseline["platform"],
        "uv_lock_sha256": baseline["uv_lock_sha256"],
    }
    assert float(raw["wall_seconds"]) > 0.0
    assert int(raw["peak_rss_kib"]) > 0
    assert pytest_run["evidence_sha256"] == canonical_sha256(raw)
