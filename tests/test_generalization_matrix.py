from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from uquant.attribution import build_economic_attribution
from uquant.config import config_fingerprint
from uquant.engine import code_fingerprint
from uquant.types import (
    AccountOrder,
    AccountState,
    Fill,
    Position,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation import generalization_matrix as matrix_module
from uquant.validation import generalization_reference as reference_module
from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import (
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from uquant.validation.generalization_matrix import (
    _head_and_source,
    evidence_contract_fingerprint,
    execute_generalization_matrix,
    validate_matrix_artifact,
    window_contract_fingerprint,
)
from uquant.validation.generalization_reference import (
    evaluate_cell_non_regression,
    evaluate_generalization_policy_artifact,
    load_generalization_baseline,
    load_generalization_policy,
)
from uquant.validation.universe import load_ai_universe


def _write_verified_market(
    root: Path,
    *,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    end_closes: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    requested_end = {} if end_closes is None else dict(end_closes)
    rows = {symbol: (10.0, requested_end.get(symbol, 11.0)) for symbol in symbols}
    rows.update({"sh000300": (100.0, 101.0), "sh000682": (100.0, 101.0)})
    results: list[dict[str, str]] = []
    checksum_lines: list[str] = []
    for symbol, (start_close, end_close) in sorted(rows.items()):
        payload = (
            "date,open,high,low,close,volume\n"
            f"{start},{start_close},{start_close},{start_close},{start_close},1000\n"
            f"{end},{end_close},{end_close},{end_close},{end_close},1000\n"
        )
        path = root / f"{symbol}.csv"
        path.write_text(payload, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        results.append({"symbol": symbol, "sha256": digest})
        checksum_lines.append(f"{digest}  {symbol}.csv")
    (root / "DATA_MANIFEST.json").write_text(
        json.dumps({"snapshot_id": "unit-market", "results": results}, sort_keys=True),
        encoding="utf-8",
    )
    (root / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    from uquant.validation.manifest import verify_data_manifest

    return verify_data_manifest(root)


@pytest.fixture(scope="module")
def matrix_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("verified-matrix-market")
    _write_verified_market(
        root,
        symbols=load_ai_universe().symbols,
        start="2023-01-03",
        end="2023-06-30",
    )
    return root


def _scenarios() -> tuple[Any, ...]:
    universe = load_ai_universe()
    symbols = universe.symbols_as_of("2022-12-30")
    evidence = PreWindowEvidence(
        as_of="2022-12-30",
        scores=tuple((symbol, float(index)) for index, symbol in enumerate(symbols)),
    )
    return build_official_scenarios(
        window=official_windows(("h1_2023",))[0],
        evidence=evidence,
    )


def _provenance(scenarios: tuple[Any, ...], data_dir: Path) -> dict[str, Any]:
    from uquant.validation.manifest import verify_data_manifest

    return {
        "head": "a" * 40,
        "source_sha256": "b" * 64,
        "effective_config_sha256": config_fingerprint(),
        "data": verify_data_manifest(data_dir),
        "runtime": {
            "python_full_version": "3.12.11",
            "numpy_version": "2.2.6",
            "pandas_version": "2.3.1",
            "uv_version": "0.8.4",
            "uv_lock_sha256": "f" * 64,
        },
        "universe_sha256": load_ai_universe().sha256,
        "industry_sha256": "1" * 64,
        "window_fingerprint": window_contract_fingerprint(scenarios),
        "scenario_fingerprint": scenario_contract_fingerprint(scenarios),
        "evidence_fingerprint": evidence_contract_fingerprint(scenarios),
        "lookback_sessions": 120,
    }


def _runner_payload(scenario: Any) -> dict[str, Any]:
    first, second = scenario.symbols[:2]
    sequence = sum(ord(character) for character in scenario.name) % 20
    universe = load_ai_universe()

    def identity(symbol: str, signal_date: str) -> dict[str, Any]:
        industry = universe.industry_of(symbol, signal_date)
        values = {
            "origin_subsystem": "LEADER",
            "mechanism": "LEADER_SELECTION",
            "origin_lifecycle": "CORE",
            "replaces_symbol": None,
            "industry_at_entry": industry,
            "industry_manifest_sha256": universe.sha256,
        }
        return {
            "event_id": derive_attribution_event_id(
                signal_date=signal_date,
                symbol=symbol,
                target_weight=0.1,
                lifecycle="CORE",
                reduction_policy="FIFO",
                reason_code="strategy_target",
                exit_kind="strategy",
                **values,
            ),
            **values,
        }

    order_identities = tuple(
        identity(symbol, scenario.evidence_as_of) for symbol in (first, second)
    )
    initial_target_identities = tuple(
        identity(symbol, scenario.window.start) for symbol in (first, second)
    )
    final_target_identities = tuple(
        identity(symbol, scenario.window.end) for symbol in (first, second)
    )
    account = AccountState.empty(100.0)
    account.cash = 80.0
    account.order_ledger = [
        AccountOrder(
            order_id=f"O{index:09d}",
            signal_date=scenario.evidence_as_of,
            submitted_date=scenario.evidence_as_of,
            symbol=symbol,
            side="BUY",
            target_weight=0.1,
            reason="fixture prose",
            lifecycle="CORE",
            status="FILLED",
            requested_shares=1,
            filled_shares=1,
            remaining_shares=0,
            attempts=1,
            last_update_date=scenario.window.end,
            last_event="FILLED",
            reduction_policy="FIFO",
            reason_code="strategy_target",
            exit_kind="strategy",
            **identity_values,
        )
        for index, (symbol, identity_values) in enumerate(
            zip((first, second), order_identities, strict=True),
            start=1,
        )
    ]
    account.next_order_sequence = 3
    account.fills = [
        Fill(
            signal_date=scenario.evidence_as_of,
            fill_date=scenario.window.start,
            symbol=symbol,
            side="BUY",
            shares=1,
            price=10.0,
            gross_value=10.0,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="fixture prose",
            lifecycle="CORE",
            order_id=f"O{index:09d}",
            fill_id=f"fixture-fill-{index}",
            reduction_policy="FIFO",
            reason_code="strategy_target",
            exit_kind="strategy",
            **identity_values,
        )
        for index, (symbol, identity_values) in enumerate(
            zip((first, second), order_identities, strict=True),
            start=1,
        )
    ]
    final_prices = {first: 11.0, second: 11.0}
    account.positions = {
        symbol: Position(
            symbol=symbol,
            shares=1,
            avg_cost=10.0,
            entry_date=scenario.window.start,
            highest_close=final_prices[symbol],
            lifecycle="CORE",
            tranches=[
                Tranche(
                    tranche_id=f"{scenario.window.start}:{symbol}:1",
                    lifecycle="CORE",
                    shares=1,
                    avg_cost=10.0,
                    entry_date=scenario.window.start,
                    sellable_date=scenario.window.start,
                    highest_close=final_prices[symbol],
                    lowest_close=final_prices[symbol],
                    **identity_values,
                )
            ],
        )
        for symbol, identity_values in zip(
            (first, second), order_identities, strict=True
        )
    }
    account.last_successful_run = scenario.window.end
    account.data_hash = "a" * 64
    account.data_hash_as_of = scenario.window.end
    account.data_hash_symbols = sorted((first, second))
    account.code_hash = code_fingerprint()
    ledger = [
        {
            "date": scenario.window.start,
            "cash": 80.0,
            "equity": 100.0,
            "gross_exposure": 0.2,
            "net_exposure": 0.2,
            "cash_weight": 0.8,
            "position_weights": {first: 0.1, second: 0.1},
            "daily_pnl": 0.0,
            "target_weights": {first: 0.1, second: 0.1},
            "target_gross": 0.2,
            "caps": {"risk_gross": 0.9, "system_gross": 0.9},
            "binding_owner": "STRATEGY",
            "risk_state": "NORMAL",
            "opportunity": "CHOPPY",
        },
        {
            "date": scenario.window.end,
            "cash": 80.0,
            "equity": 102.0,
            "gross_exposure": 22.0 / 102.0,
            "net_exposure": 22.0 / 102.0,
            "cash_weight": 80.0 / 102.0,
            "position_weights": {first: 11.0 / 102.0, second: 11.0 / 102.0},
            "daily_pnl": 2.0,
            "target_weights": {first: 0.1, second: 0.1},
            "target_gross": 0.2,
            "caps": {"risk_gross": 0.9, "system_gross": 0.9},
            "binding_owner": "STRATEGY",
            "risk_state": "NORMAL",
            "opportunity": "CHOPPY",
        },
    ]
    attribution = build_economic_attribution(
        account=account,
        final_prices=final_prices,
        sessions=(scenario.window.start, scenario.window.end),
        economic_start=scenario.window.start,
        economic_end=scenario.window.end,
        final_equity=102.0,
        daily_ledger=ledger,
        benchmark_close={scenario.window.start: 100.0, scenario.window.end: 100.0},
    )

    def target(
        symbol: str,
        identity_values: Mapping[str, Any],
        signal_date: str,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "weight": 0.1,
            "lifecycle": "CORE",
            "reduction_policy": "FIFO",
            "reason_code": "strategy_target",
            "exit_kind": "strategy",
            "event_signal_date": signal_date,
            "event_target_weight_hex": (0.1).hex(),
            **identity_values,
        }

    traces = [
        {
            "schema": "uquant.decision-control-plane.v2",
            "date": date,
            "opportunity": "CHOPPY",
            "risk": {
                "state": "NORMAL",
                "shock_state": "NONE",
                "reduction_level": 0,
                "severity": "NORMAL",
                "target_gross_cap": 0.9,
                "system_gross_cap": 0.9,
            },
            "target_gross": 0.2,
            "targets": [
                target(symbol, identity_values, date)
                for symbol, identity_values in zip(
                    (first, second), identities, strict=True
                )
            ],
            "orders": [],
            "effective_config_sha256": config_fingerprint(),
        }
        for date, identities in (
            (scenario.window.start, initial_target_identities),
            (scenario.window.end, final_target_identities),
        )
    ]

    def digest(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    legacy_payloads = [
        {
            "date": trace["date"],
            "opportunity": trace["opportunity"],
            "risk": trace["risk"]["state"],
            "targets": [
                {
                    name: row[name]
                    for name in (
                        "symbol",
                        "weight",
                        "lifecycle",
                        "reduction_policy",
                        "reason_code",
                        "exit_kind",
                    )
                }
                for row in trace["targets"]
            ],
            "orders": [
                {
                    name: row[name]
                    for name in (
                        "order_id",
                        "symbol",
                        "side",
                        "target_weight",
                        "reduction_policy",
                        "reason_code",
                        "exit_kind",
                    )
                }
                for row in trace["orders"]
            ],
        }
        for trace in traces
    ]
    return {
        "final_wealth": 1.02,
        "final_equity": 102.0,
        "start": scenario.window.start,
        "end": scenario.window.end,
        "max_drawdown": 0.05 + sequence / 1000.0,
        "account_orders": sequence,
        "gross_turnover": 0.2,
        "annual_turnover": 0.4 + sequence / 100.0,
        "symbol_pnl": {first: 1.0, second: 1.0},
        "attribution": attribution,
        "effective_config_sha256": config_fingerprint(),
        "decision_trace": traces,
        "decision_digests": [digest(trace) for trace in traces],
        "legacy_decision_digests": [digest(payload) for payload in legacy_payloads],
        "equity_curve": [
            {"date": scenario.window.start, "equity": 100.0},
            {"date": scenario.window.end, "equity": 102.0},
        ],
        "daily_replay_evidence": [
            {
                "date": scenario.window.start,
                "cash": 80.0,
                "position_shares": {first: 1, second: 1},
                "close_marks": {first: 10.0, second: 10.0},
            },
            {
                "date": scenario.window.end,
                "cash": 80.0,
                "position_shares": {first: 1, second: 1},
                "close_marks": final_prices,
            },
        ],
        "final_account": account.to_dict(),
        "opaque_raw_cell": {"scenario": scenario.name, "values": [1, 2, 3]},
    }


def test_matrix_preserves_every_raw_cell_and_reports_required_aggregates(
    matrix_data_dir: Path,
) -> None:
    """Catches dropped raw results or aggregates that omit tail/turnover/concentration."""
    scenarios = _scenarios()
    observed_raw: dict[str, Mapping[str, Any]] = {}

    def runner(scenario: Any) -> Mapping[str, Any]:
        raw = _runner_payload(scenario)
        observed_raw[scenario.name] = raw
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )

    economic = [cell for cell in artifact["cells"] if cell["economic"]]
    insufficient = [cell for cell in artifact["cells"] if not cell["economic"]]
    assert len(economic) == 32
    assert len(insufficient) == 7
    assert len(observed_raw) == 32
    assert all(
        cell["raw"]
        == {
            key: value
            for key, value in observed_raw[cell["scenario"]].items()
            if key != "attribution"
        }
        for cell in economic
    )
    assert all(
        cell["status"] == "INSUFFICIENT_SAMPLE"
        and cell["raw"] is None
        and cell["metrics"] is None
        and cell["attribution_status"] == "INSUFFICIENT_SAMPLE"
        and cell["attribution"] is None
        and cell["concentration"] is None
        for cell in insufficient
    )
    assert all(cell["attribution_status"] == "VALID" for cell in economic)
    assert all("attribution" not in cell["raw"] for cell in economic)
    assert all(
        cell["attribution"] == observed_raw[cell["scenario"]]["attribution"]
        for cell in economic
    )
    assert all(
        cell["concentration"] == cell["attribution"]["symbol_concentration"]
        for cell in economic
    )
    assert artifact["schema_version"] == 2
    assert artifact["attribution_definition"] == {
        "schema": "uquant.economic-attribution.v1",
        "interval": "cell start/end inclusive; no pre-window warmup or post-end data",
        "accounting_identity": "realized_pnl + open_pnl = final_equity - initial_cash",
        "lot_identity": "originating BUY event plus per-SELL sold_tranches",
        "concentration": "positive, signed-net, and absolute PnL denominators",
        "diagnostics": "cash drag and paired risk avoidance are not accounting PnL",
    }
    assert set(artifact["aggregates"]["all"]) >= {
        "median_wealth",
        "worst_wealth",
        "p10_wealth",
        "p90_drawdown",
        "worst_drawdown",
        "median_orders",
        "p90_orders",
        "median_gross_turnover",
        "p90_gross_turnover",
        "worst_gross_turnover",
        "median_top1_concentration",
        "worst_top1_concentration",
        "median_top3_concentration",
        "worst_top3_concentration",
        "median_pnl_hhi",
        "worst_pnl_hhi",
    }
    assert economic[0]["metrics"]["top1_concentration"] == pytest.approx(0.5)
    assert economic[0]["metrics"]["top3_concentration"] == pytest.approx(1.0)
    assert economic[0]["metrics"]["pnl_hhi"] == pytest.approx(0.5)
    assert economic[0]["evidence"] == {
        "as_of": "2022-12-30",
        "eligible_symbols": list(load_ai_universe().symbols_as_of("2022-12-30")),
        "ineligible_symbols": [],
        "lookback_sessions": 120,
        "scores": [
            [symbol, float(index)]
            for index, symbol in enumerate(load_ai_universe().symbols_as_of("2022-12-30"))
        ],
        "sha256": economic[0]["evidence"]["sha256"],
    }
    assert len(economic[0]["evidence"]["sha256"]) == 64
    assert artifact["concentration_definition"]["denominator"] == "sum(abs(symbol_pnl))"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_attribution",
        "extra_attribution_field",
        "interval_end",
        "reconciliation",
        "nonfinite_cost",
        "detached_concentration",
        "causal_ledger_weight",
    ),
)
def test_matrix_rejects_invalid_or_detached_economic_attribution(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["economic"])
    if mutation == "missing_attribution":
        del cell["attribution"]
    elif mutation == "extra_attribution_field":
        cell["attribution"]["extra"] = None
    elif mutation == "interval_end":
        cell["attribution"]["interval"]["economic_end"] = "2099-01-01"
    elif mutation == "reconciliation":
        cell["attribution"]["accounting"]["total_pnl"] += 0.01
    elif mutation == "nonfinite_cost":
        cell["attribution"]["costs"]["all_in"] = float("nan")
    elif mutation == "causal_ledger_weight":
        cell["attribution"]["daily_ledger"][0]["cash_weight"] = 0.123
    else:
        cell["concentration"] = {}

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert failures
    assert any("attribution" in failure or "concentration" in failure for failure in failures)


def test_matrix_rejects_coherent_daily_ledger_and_raw_evidence_tamper(
    matrix_data_dir: Path,
) -> None:
    """Catches a self-consistent intermediate equity path detached from fills and marks."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["economic"])
    first, second = cell["attribution"]["daily_ledger"]
    first["cash"] = 81.0
    first["equity"] = 101.0
    first["cash_weight"] = 81.0 / 101.0
    first["position_weights"] = {
        symbol: 10.0 / 101.0 for symbol in first["position_weights"]
    }
    first["gross_exposure"] = 20.0 / 101.0
    first["net_exposure"] = 20.0 / 101.0
    first["daily_pnl"] = 1.0
    second["daily_pnl"] = 1.0
    cell["raw"]["daily_replay_evidence"][0]["cash"] = 81.0
    cell["raw"]["equity_curve"][0]["equity"] = 101.0

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert any("daily replay evidence" in failure for failure in failures)


def test_matrix_rejects_daily_replay_evidence_beyond_economic_end(
    matrix_data_dir: Path,
) -> None:
    """Catches raw replay evidence extending a cell into later or holdout sessions."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["economic"])
    cell["raw"]["daily_replay_evidence"][-1]["date"] = "2099-01-01"

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert any("daily replay evidence" in failure for failure in failures)


def test_matrix_rejects_coherent_mark_ledger_and_equity_tamper(
    matrix_data_dir: Path,
) -> None:
    """Catches an artifact self-signing a changed mark and all derived daily values."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["economic"])
    symbol = cell["symbols"][0]
    cell["raw"]["daily_replay_evidence"][0]["close_marks"][symbol] = 11.0
    cell["raw"]["equity_curve"][0]["equity"] = 101.0
    first, second = cell["attribution"]["daily_ledger"]
    first["equity"] = 101.0
    first["cash_weight"] = 80.0 / 101.0
    first["position_weights"][symbol] = 11.0 / 101.0
    other = next(item for item in first["position_weights"] if item != symbol)
    first["position_weights"][other] = 10.0 / 101.0
    first["gross_exposure"] = 21.0 / 101.0
    first["net_exposure"] = 21.0 / 101.0
    first["daily_pnl"] = 1.0
    second["daily_pnl"] = 1.0

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert any("close versus frozen data" in failure for failure in failures)


def test_verified_market_cache_is_lookup_order_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches cache population order changing frozen close/session results."""
    from uquant.data import DataStore
    from uquant.validation.replay_evidence import VerifiedMarketData

    scenario = next(item for item in _scenarios() if item.economic)
    symbols = tuple(scenario.symbols[:2])
    expected_manifest = _write_verified_market(
        tmp_path,
        symbols=symbols,
        start=scenario.window.start,
        end=scenario.window.end,
    )
    loaded_symbols: list[str] = []
    original_load = DataStore.load

    def tracked_load(store: DataStore, symbol: str) -> Any:
        loaded_symbols.append(symbol)
        return original_load(store, symbol)

    monkeypatch.setattr(DataStore, "load", tracked_load)
    market = VerifiedMarketData(tmp_path, expected_manifest=expected_manifest)
    construction_loads = tuple(loaded_symbols)
    first = (
        market.close(symbols[0], scenario.window.start),
        market.close(symbols[1], scenario.window.end),
        market.sessions(scenario.window.start, scenario.window.end),
    )
    second = (
        market.close(symbols[0], scenario.window.start),
        market.close(symbols[1], scenario.window.end),
        market.sessions(scenario.window.start, scenario.window.end),
    )

    assert first == second == (10.0, 11.0, (scenario.window.start, scenario.window.end))
    assert tuple(loaded_symbols) == construction_loads
    assert len(construction_loads) == 4


def test_champion_exact_equality_passes_but_mutation_fails(
    matrix_data_dir: Path,
) -> None:
    """Catches a default comparison that rejects equality or tolerates a regression."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    champion = {
        f"{cell['window']}/{cell['scenario']}": copy.deepcopy(cell["metrics"])
        for cell in artifact["cells"]
        if cell["economic"]
    }

    assert validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        champion_cells=champion,
    ) == ()
    mutated = copy.deepcopy(artifact)
    first = next(cell for cell in mutated["cells"] if cell["economic"])
    first["metrics"]["final_wealth"] -= 0.01
    failures = validate_matrix_artifact(
        mutated,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        champion_cells=champion,
    )
    assert any("champion equality" in failure for failure in failures)


def test_v2_projection_uses_reconstructed_legacy_control_and_only_normalizes_validated_bindings() -> None:
    frozen = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = copy.deepcopy(frozen)
    candidate["schema_version"] = 2
    candidate["attribution_definition"] = copy.deepcopy(
        matrix_module._ATTRIBUTION_DEFINITION
    )
    for cell in candidate["cells"]:
        cell["attribution_status"] = (
            "VALID"
            if cell["metrics"] is not None
            else "ERROR"
            if cell["replay_error"]
            else "INSUFFICIENT_SAMPLE"
        )
        cell["attribution"] = {"replacement": "closed-schema placeholder"} if cell["metrics"] else None
        cell["concentration"] = {"replacement": "closed-schema placeholder"} if cell["metrics"] else None
        if cell["raw"] is not None:
            # The v1 field used reason-text classification and post-window
            # prices.  It is not reproduced by v2; the migration contract
            # admits only the compiled frozen payload and rejects injection.
            cell["raw"].pop("attribution")
            cell["raw"]["legacy_decision_digests"] = copy.deepcopy(
                cell["raw"]["decision_digests"]
            )
            cell["raw"]["decision_digests"] = ["new attribution-bearing digest"]
            cell["raw"]["decision_trace"] = [{"new": "strictly validated control evidence"}]
            cell["raw"]["daily_replay_evidence"] = [{"new": "strictly validated replay evidence"}]
            account = cell["raw"]["final_account"]
            account["schema_version"] = 5
            account["code_hash"] = "new committed source fingerprint"
            if account["fills"]:
                account["fills"][0]["event_id"] = "evt_" + "1" * 64

    expected = load_generalization_baseline().attribution_neutral_equality_sha256
    assert matrix_module._SCHEMA_VERSION == 2
    assert reference_module._attribution_neutral_equality_sha256(candidate) == expected

    injected = copy.deepcopy(candidate)
    valid_injected = next(cell for cell in injected["cells"] if cell["metrics"] is not None)
    valid_injected["raw"]["attribution"] = {"forged": "deprecated v1 evidence"}
    with pytest.raises(ValueError, match="deprecated v1 attribution"):
        reference_module._attribution_neutral_equality_sha256(injected)

    changed_frozen = copy.deepcopy(frozen)
    valid_frozen = next(cell for cell in changed_frozen["cells"] if cell["metrics"] is not None)
    valid_frozen["raw"]["attribution"]["by_reason"]["forged"] = {}
    with pytest.raises(ValueError, match="deprecated v1 attribution"):
        reference_module._attribution_neutral_equality_sha256(changed_frozen)

    for mutation in ("metric", "cash", "legacy_decision", "arbitrary_raw_field"):
        changed = copy.deepcopy(candidate)
        valid = next(cell for cell in changed["cells"] if cell["metrics"] is not None)
        if mutation == "metric":
            valid["metrics"]["final_wealth"] += 0.000001
        elif mutation == "cash":
            valid["raw"]["final_account"]["cash"] += 0.01
        elif mutation == "legacy_decision":
            valid["raw"]["legacy_decision_digests"][0] = "0" * 64
        else:
            valid["raw"]["arbitrary"] = None
        assert reference_module._attribution_neutral_equality_sha256(changed) != expected


def _fixture_reference_contract(
    artifact: Mapping[str, Any],
) -> tuple[Any, Any]:
    cells: dict[str, Any] = {}
    for cell in artifact["cells"]:
        raw_error = cell["replay_error"]
        error = (
            None
            if raw_error is None
            else reference_module.ReplayError(
                exception_type=raw_error["exception_type"],
                message=raw_error["message"],
            )
        )
        cells[f"{cell['window']}/{cell['scenario']}"] = reference_module.BaselineCell(
            window=cell["window"],
            scenario=cell["scenario"],
            family=cell["family"],
            status=cell["status"],
            economic=cell["economic"],
            pool_size=cell["pool_size"],
            seed_index=cell["seed_index"],
            derived_seed=cell["derived_seed"],
            evidence_sha256=cell["evidence"]["sha256"],
            contract_sha256=reference_module._candidate_contract_sha256(cell),
            metrics=None if cell["metrics"] is None else dict(cell["metrics"]),
            replay_error=error,
        )
    baseline = reference_module.GeneralizationBaseline(
        sha256="9" * 64,
        runner_head=str(artifact["provenance"]["head"]),
        runner_source_sha256=str(artifact["provenance"]["source_sha256"]),
        artifact_sha256="8" * 64,
        artifact_size_bytes=1,
        artifact_equality_sha256="7" * 64,
        attribution_neutral_equality_sha256=(
            reference_module._attribution_neutral_equality_sha256(artifact)
        ),
        provenance=dict(artifact["provenance"]),
        aggregates=dict(artifact["aggregates"]),
        cells=cells,
    )
    policy = replace(load_generalization_policy(), baseline_sha256=baseline.sha256)
    return baseline, policy


def test_v2_policy_evaluator_accepts_verified_fixture_exact_equality(
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches strict v2 readback rejecting its own verified canonical artifact."""
    from uquant.data import DataStore

    scenarios = _scenarios()
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
    )
    loaded_symbols: list[str] = []
    original_load = DataStore.load

    def tracked_load(store: DataStore, symbol: str) -> Any:
        loaded_symbols.append(symbol)
        return original_load(store, symbol)

    monkeypatch.setattr(DataStore, "load", tracked_load)

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=matrix_data_dir,
    )

    assert result["passed"] is True
    assert result["exact_equality_passed"] is True
    assert result["economic_cells_valid"] == 32
    assert result["replay_error_cells"] == 0
    assert loaded_symbols
    assert len(loaded_symbols) == len(set(loaded_symbols))


@pytest.mark.parametrize(
    ("mutation", "failure_text"),
    (
        ("decision_digest", "decision digest"),
        ("account_schema", "account schema"),
        ("account_code", "account code hash"),
        ("fill_event", "event identity"),
        ("raw_legacy_attribution", "deprecated v1 attribution"),
    ),
)
def test_v2_policy_evaluator_validates_control_plane_before_frozen_projection(
    mutation: str,
    failure_text: str,
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches v2 current-control tamper being hidden by the frozen-v1 projection."""
    scenarios = _scenarios()
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
        raising=False,
    )
    changed = copy.deepcopy(artifact)
    cell = next(item for item in changed["cells"] if item["metrics"] is not None)
    if mutation == "decision_digest":
        cell["raw"]["decision_digests"][0] = "0" * 64
    elif mutation == "account_schema":
        cell["raw"]["final_account"]["schema_version"] = 999
    elif mutation == "account_code":
        cell["raw"]["final_account"]["code_hash"] = "0" * 64
    elif mutation == "raw_legacy_attribution":
        cell["raw"]["attribution"] = {"forged": "deprecated v1 evidence"}
    else:
        cell["raw"]["final_account"]["fills"][0]["event_id"] = "evt_" + "0" * 64

    result = evaluate_generalization_policy_artifact(
        changed,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=matrix_data_dir,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any(failure_text in failure for failure in result["failures"])


def test_matrix_preserves_replay_error_continues_and_excludes_it_from_quantiles(
    matrix_data_dir: Path,
) -> None:
    """Catches one engine exception aborting the matrix or becoming a fake metric."""
    scenarios = _scenarios()
    failing = next(item for item in scenarios if item.name == "random__20__0000")
    executed: list[str] = []

    def runner(scenario: Any) -> dict[str, Any]:
        executed.append(scenario.name)
        if scenario is failing:
            raise RuntimeError("allocator failed\n  without a finite result")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )

    assert len(executed) == 32
    assert executed[-1] == "random__20__0004"
    error_cell = next(cell for cell in artifact["cells"] if cell["scenario"] == failing.name)
    assert error_cell["raw"] is None
    assert error_cell["metrics"] is None
    assert error_cell["replay_error"] == {
        "exception_type": "RuntimeError",
        "message": "allocator failed without a finite result",
    }
    assert artifact["aggregates"]["all"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["all"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["all"]["replay_error_cells"] == 1
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_expected"] == 32
    assert artifact["aggregates"]["by_window"]["h1_2023"]["economic_cells_valid"] == 31
    assert artifact["aggregates"]["by_window"]["h1_2023"]["replay_error_cells"] == 1
    valid_wealth = [
        float(cell["metrics"]["final_wealth"])
        for cell in artifact["cells"]
        if cell["metrics"] is not None
    ]
    assert artifact["aggregates"]["all"]["worst_wealth"] == min(valid_wealth)
    assert artifact["passed"] is False
    assert artifact["failures"] == [
        "cell replay failed: h1_2023/random__20__0000: RuntimeError: "
        "allocator failed without a finite result"
    ]


def test_matrix_validator_rejects_replay_error_with_fabricated_metrics_or_missing_cell(
    matrix_data_dir: Path,
) -> None:
    """Catches error evidence being converted to metrics or silently dropped."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    failing = next(item for item in scenarios if item.name == "random__20__0000")

    def runner(scenario: Any) -> dict[str, Any]:
        if scenario is failing:
            raise RuntimeError("fixed replay failure")
        return _runner_payload(scenario)

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    fabricated = copy.deepcopy(artifact)
    error_cell = next(
        cell for cell in fabricated["cells"] if cell["scenario"] == failing.name
    )
    error_cell["raw"] = _runner_payload(failing)
    error_cell["metrics"] = next(
        cell["metrics"] for cell in artifact["cells"] if cell["metrics"] is not None
    )
    fabricated_failures = validate_matrix_artifact(
        fabricated,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert any("replay error" in failure for failure in fabricated_failures)

    missing = copy.deepcopy(artifact)
    missing["cells"] = [
        cell for cell in missing["cells"] if cell["scenario"] != failing.name
    ]
    missing_failures = validate_matrix_artifact(
        missing,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert any("missing cell records" in failure for failure in missing_failures)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "nonfinite", "stale"])
def test_matrix_validation_fails_closed_on_incomplete_or_stale_artifacts(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    """Catches matrix aggregation that accepts missing/duplicate/invalid evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    if mutation == "missing":
        changed["cells"].pop()
    elif mutation == "duplicate":
        changed["cells"].append(copy.deepcopy(changed["cells"][0]))
    elif mutation == "nonfinite":
        next(cell for cell in changed["cells"] if cell["economic"])["raw"][
            "final_wealth"
        ] = float("nan")
    else:
        changed["provenance"]["head"] = "9" * 40

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert failures
    assert any(mutation in failure or "cell" in failure for failure in failures)


def test_zero_symbol_pnl_has_defined_non_fabricated_zero_concentration(
    matrix_data_dir: Path,
) -> None:
    """Catches NaN or invented attribution when exact symbol PnL has no mass."""
    scenarios = _scenarios()

    def zero_runner(scenario: Any) -> dict[str, Any]:
        raw = _runner_payload(scenario)
        account = AccountState.empty(100.0)
        ledger = [
            {
                "date": date,
                "cash": 100.0,
                "equity": 100.0,
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "cash_weight": 1.0,
                "position_weights": {},
                "daily_pnl": 0.0,
                "target_weights": {},
                "target_gross": 0.0,
                "caps": {"risk_gross": 0.9, "system_gross": 0.9},
                "binding_owner": "STRATEGY",
                "risk_state": "NORMAL",
                "opportunity": "CHOPPY",
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["attribution"] = build_economic_attribution(
            account=account,
            final_prices={},
            sessions=(scenario.window.start, scenario.window.end),
            economic_start=scenario.window.start,
            economic_end=scenario.window.end,
            final_equity=100.0,
            daily_ledger=ledger,
            benchmark_close={scenario.window.start: 100.0, scenario.window.end: 100.0},
        )
        account.last_successful_run = scenario.window.end
        account.data_hash = "a" * 64
        account.data_hash_as_of = scenario.window.end
        account.code_hash = code_fingerprint()
        raw["final_account"] = account.to_dict()
        raw["final_equity"] = 100.0
        raw["final_wealth"] = 1.0
        raw["account_orders"] = 0
        raw["gross_turnover"] = 0.0
        raw["annual_turnover"] = 0.0
        raw["symbol_pnl"] = {}
        raw["equity_curve"] = [
            {"date": date, "equity": 100.0}
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["daily_replay_evidence"] = [
            {
                "date": date,
                "cash": 100.0,
                "position_shares": {},
                "close_marks": {},
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["decision_trace"] = [
            {
                "schema": "uquant.decision-control-plane.v2",
                "date": date,
                "opportunity": "CHOPPY",
                "risk": {
                    "state": "NORMAL",
                    "shock_state": "NONE",
                    "reduction_level": 0,
                    "severity": "NORMAL",
                    "target_gross_cap": 0.9,
                    "system_gross_cap": 0.9,
                },
                "target_gross": 0.0,
                "targets": [],
                "orders": [],
                "effective_config_sha256": config_fingerprint(),
            }
            for date in (scenario.window.start, scenario.window.end)
        ]
        raw["decision_digests"] = [
            hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            for trace in raw["decision_trace"]
        ]
        raw["legacy_decision_digests"] = [
            hashlib.sha256(
                json.dumps(
                    {
                        "date": trace["date"],
                        "opportunity": "CHOPPY",
                        "risk": "NORMAL",
                        "targets": [],
                        "orders": [],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            for trace in raw["decision_trace"]
        ]
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=zero_runner,
        provenance=_provenance(scenarios, matrix_data_dir),
        data_dir=matrix_data_dir,
    )
    metrics = next(cell["metrics"] for cell in artifact["cells"] if cell["economic"])
    assert metrics["top1_concentration"] == 0.0
    assert metrics["top3_concentration"] == 0.0
    assert metrics["pnl_hhi"] == 0.0


@pytest.mark.parametrize(
    "mutation",
    ["schema", "gate", "concentration", "aggregate", "aggregate_nonfinite", "state"],
)
def test_matrix_validator_recomputes_top_level_contract(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    """Catches forged top-level gate state, definitions, or aggregate evidence."""
    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    if mutation == "schema":
        changed["schema_version"] = 99
    elif mutation == "gate":
        changed["gate"] = "not-the-generalization-gate"
    elif mutation == "concentration":
        changed["concentration_definition"]["denominator"] = "signed PnL"
    elif mutation == "aggregate":
        changed["aggregates"]["all"]["median_wealth"] = 999.0
    elif mutation == "aggregate_nonfinite":
        changed["aggregates"]["all"]["median_wealth"] = float("nan")
    else:
        changed["passed"] = False
        changed["failures"] = ["fabricated"]

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert failures
    assert any(mutation.split("_")[0] in failure or "gate state" in failure for failure in failures)


def _write_source_fixture(root: Path) -> None:
    paths = {
        "pyproject.toml": "[project]\nname='fixture'\n",
        "requirements.txt": "pandas==3.0.5\n",
        "uv.lock": "version = 1\n",
        "benchmarks/reference_registry.json": '{"reference_symbols":["a"]}\n',
        "uquant/module.py": "VALUE = 1\n",
        "uquant/validation/resources/ai_universe_manifest.json": '{"members":[]}\n',
    }
    for relative, content in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_matrix_source_provenance_rejects_dirty_reference_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a dirty reference context being outside the HEAD guard."""
    _write_source_fixture(tmp_path)
    observed_status: tuple[str, ...] = ()

    def fake_git(root: Path, arguments: Any) -> str:
        nonlocal observed_status
        args = tuple(arguments)
        if args[0] == "status":
            observed_status = args
            return " M benchmarks/reference_registry.json\n" if "benchmarks/reference_registry.json" in args else ""
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="committed source"):
        _head_and_source(tmp_path)
    assert "benchmarks/reference_registry.json" in observed_status


def test_matrix_source_provenance_rejects_committed_registry_divergence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches current and HEAD hashes agreeing while decision registry differs."""
    _write_source_fixture(tmp_path)

    def fake_git(root: Path, arguments: Any) -> str:
        args = tuple(arguments)
        if args[0] == "status":
            return ""
        if args[:2] == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args[0] == "show":
            relative = args[1].split(":", 1)[1]
            if relative == "benchmarks/reference_registry.json":
                return '{"reference_symbols":["different"]}\n'
            return (root / relative).read_text(encoding="utf-8")
        raise AssertionError(args)

    monkeypatch.setattr(matrix_module, "_git", fake_git)
    with pytest.raises(RuntimeError, match="exact checked-out HEAD"):
        _head_and_source(tmp_path)


def test_untouched_champion_has_exact_equality_but_records_frozen_gate_failures() -> None:
    """Catches Pareto-only equality or dishonest suppression of champion failures."""
    baseline = load_generalization_baseline()
    policy = load_generalization_policy()
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
    )

    assert result["exact_equality_passed"] is True
    assert result["passed"] is False
    assert result["economic_cells_expected"] == 192
    assert result["economic_cells_valid"] == 191
    assert result["replay_error_cells"] == 1
    assert any("continuous_ai_era/random__20__0000" in item for item in result["failures"])
    assert any("random tail" in item for item in result["failures"])
    assert not any("exact equality differs" in item for item in result["failures"])
    assert not any("intrinsic directional" in item for item in result["failures"])


def test_relative_cell_policy_accepts_equality_and_enforces_literal_boundaries() -> None:
    """Catches equality rejection or weakened wealth/risk/activity non-regression."""
    policy = load_generalization_policy()
    reference = {
        "final_wealth": 2.0,
        "max_drawdown": 0.10,
        "account_orders": 10,
        "gross_turnover": 4.0,
        "annual_turnover": 2.0,
        "top1_concentration": 0.5,
        "top3_concentration": 0.8,
        "pnl_hhi": 0.4,
    }

    assert evaluate_cell_non_regression(reference, reference, policy=policy) == ()
    assert evaluate_cell_non_regression(
        {**reference, "final_wealth": 1.899999}, reference, policy=policy
    ) == ("final_wealth 1.899999 is below 95% reference 1.9",)
    assert evaluate_cell_non_regression(
        {**reference, "max_drawdown": 0.120001}, reference, policy=policy
    ) == ("max_drawdown 0.120001 exceeds reference-plus-buffer 0.12",)
    assert evaluate_cell_non_regression(
        {**reference, "account_orders": 12}, reference, policy=policy
    ) == ("account_orders 12 exceeds reference activity limit 11",)
    assert evaluate_cell_non_regression(
        {**reference, "gross_turnover": 4.400001}, reference, policy=policy
    ) == ("gross_turnover 4.400001 exceeds 110% reference 4.4",)
    assert evaluate_cell_non_regression(
        {**reference, "annual_turnover": 2.200001}, reference, policy=policy
    ) == ("annual_turnover 2.200001 exceeds 110% reference 2.2",)


def test_zero_reference_turnover_requires_candidate_zero() -> None:
    """Catches a ratio fallback that permits activity where the champion had none."""
    policy = load_generalization_policy()
    reference = {
        "final_wealth": 1.0,
        "max_drawdown": 0.0,
        "account_orders": 0,
        "gross_turnover": 0.0,
        "annual_turnover": 0.0,
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    candidate = {**reference, "gross_turnover": 0.000001}

    assert evaluate_cell_non_regression(candidate, reference, policy=policy) == (
        "gross_turnover 1e-06 must remain zero because reference is zero",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_cell",
        "metrics_removed",
        "fabricated_insufficient_evidence",
        "contract_mismatch",
        "duplicate_cell",
        "extra_cell",
        "malformed_cell",
        "nonfinite_metric",
        "provenance_mismatch",
        "aggregate_mismatch",
        "replay_error_mismatch",
        "finite_metrics_mismatch",
    ),
)
def test_exact_equality_fails_closed_for_every_incomplete_or_mismatched_binding(
    mutation: str,
) -> None:
    """Catches structural failures being reported while exact equality stays true."""
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    if mutation == "missing_cell":
        artifact["cells"].pop()
    elif mutation == "metrics_removed":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ] = None
    elif mutation == "fabricated_insufficient_evidence":
        next(cell for cell in artifact["cells"] if not cell["economic"])["raw"] = {
            "fabricated": True
        }
    elif mutation == "contract_mismatch":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "evidence"
        ]["sha256"] = "0" * 64
    elif mutation == "duplicate_cell":
        artifact["cells"].append(copy.deepcopy(artifact["cells"][0]))
    elif mutation == "extra_cell":
        extra = copy.deepcopy(artifact["cells"][0])
        extra["window"] = "extra-window"
        extra["scenario"] = "extra-scenario"
        artifact["cells"].append(extra)
    elif mutation == "malformed_cell":
        artifact["cells"].append({"window": "h1_2023"})
    elif mutation == "nonfinite_metric":
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ]["final_wealth"] = float("nan")
    elif mutation == "provenance_mismatch":
        artifact["provenance"]["data"]["snapshot_id"] = "drifted-snapshot"
    elif mutation == "aggregate_mismatch":
        artifact["aggregates"]["all"]["median_wealth"] += 0.000001
    elif mutation == "replay_error_mismatch":
        next(cell for cell in artifact["cells"] if cell["replay_error"] is not None)[
            "replay_error"
        ]["message"] = "different canonical replay failure"
    else:
        next(cell for cell in artifact["cells"] if cell["metrics"] is not None)[
            "metrics"
        ]["final_wealth"] += 0.000001

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
        require_exact_equality=True,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any("exact equality differs" in failure for failure in result["failures"])


@pytest.mark.parametrize(
    "mutation",
    (
        "insufficient_nullable_fields_deleted",
        "successful_replay_error_deleted",
        "replay_error_metrics_deleted",
        "replay_error_raw_deleted",
        "extra_top_level_field",
        "extra_provenance_field",
        "extra_runtime_field",
        "extra_data_field",
        "missing_universe_binding",
        "missing_scenario_binding",
        "missing_window_binding",
        "extra_cell_field",
        "extra_evidence_field",
        "successful_raw_tamper",
    ),
)
def test_exact_equality_rejects_schema_presence_and_raw_evidence_drift(
    mutation: str,
) -> None:
    """Catches absent nullable fields and unbound artifact structure or raw evidence."""
    artifact = json.loads(
        (Path("artifacts") / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )
    successful = next(cell for cell in artifact["cells"] if cell["metrics"] is not None)
    replay_error = next(
        cell for cell in artifact["cells"] if cell["replay_error"] is not None
    )
    if mutation == "insufficient_nullable_fields_deleted":
        insufficient = next(cell for cell in artifact["cells"] if not cell["economic"])
        for field in ("raw", "metrics", "replay_error"):
            del insufficient[field]
    elif mutation == "successful_replay_error_deleted":
        del successful["replay_error"]
    elif mutation == "replay_error_metrics_deleted":
        del replay_error["metrics"]
    elif mutation == "replay_error_raw_deleted":
        del replay_error["raw"]
    elif mutation == "extra_top_level_field":
        artifact["extra"] = None
    elif mutation == "extra_provenance_field":
        artifact["provenance"]["extra"] = None
    elif mutation == "extra_runtime_field":
        artifact["provenance"]["runtime"]["extra"] = None
    elif mutation == "extra_data_field":
        artifact["provenance"]["data"]["extra"] = None
    elif mutation == "missing_universe_binding":
        del artifact["provenance"]["universe_sha256"]
    elif mutation == "missing_scenario_binding":
        del artifact["provenance"]["scenario_fingerprint"]
    elif mutation == "missing_window_binding":
        del artifact["provenance"]["window_fingerprint"]
    elif mutation == "extra_cell_field":
        successful["extra"] = None
    elif mutation == "extra_evidence_field":
        successful["evidence"]["extra"] = None
    else:
        successful["raw"]["final_wealth"] += 0.000001

    result = evaluate_generalization_policy_artifact(
        artifact,
        baseline=load_generalization_baseline(),
        policy=load_generalization_policy(),
        require_exact_equality=True,
    )

    assert result["passed"] is False
    assert result["exact_equality_passed"] is False
    assert any("exact equality differs" in failure for failure in result["failures"])
