# ruff: noqa: E402, F401, I001
# Late re-exports preserve the immutable pytest collection identity and order.
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
from uquant.config import (
    DEFAULT_CONFIG,
    SystemConfig,
    config_fingerprint,
)
from uquant.engine import code_fingerprint
from uquant.types import (
    AccountOrder,
    AccountState,
    Fill,
    PendingOrder,
    Position,
    Tranche,
    derive_attribution_event_id,
)
from uquant.validation import generalization_reference as reference_module
from uquant.validation.generalization import PreWindowEvidence
from uquant.validation.generalization_contract import (
    build_official_scenarios,
    official_windows,
    scenario_contract_fingerprint,
)
from uquant.validation.generalization_matrix import (
    evidence_contract_fingerprint,
    execute_generalization_matrix,
    validate_matrix_artifact,
    window_contract_fingerprint,
)
from uquant.validation.generalization_reference import (
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


def _provenance(
    scenarios: tuple[Any, ...],
    data_dir: Path,
    *,
    expected_config: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    from uquant.validation.manifest import verify_data_manifest

    return {
        "head": "a" * 40,
        "source_sha256": "b" * 64,
        "effective_config_sha256": config_fingerprint(expected_config),
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
            "grant_id": "",
            "epoch_id": "",
            **values,
        }

    order_identities = tuple(
        identity(symbol, scenario.window.start) for symbol in (first, second)
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
            signal_date=scenario.window.start,
            submitted_date=scenario.window.start,
            symbol=symbol,
            side="BUY",
            target_weight=0.1,
            reason="fixture prose",
            lifecycle="CORE",
            status="PARTIALLY_FILLED" if index == 1 else "FILLED",
            requested_shares=2 if index == 1 else 1,
            filled_shares=1,
            remaining_shares=1 if index == 1 else 0,
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
    account.pending_orders = [
        PendingOrder(
            signal_date=scenario.window.start,
            symbol=first,
            side="BUY",
            target_weight=0.1,
            reason="fixture prose",
            lifecycle="CORE",
            remaining_shares=1,
            attempts=1,
            order_id="O000000001",
            reduction_policy="FIFO",
            reason_code="strategy_target",
            exit_kind="strategy",
            **order_identities[0],
        )
    ]
    account.fills = [
        Fill(
            signal_date=scenario.window.start,
            fill_date=scenario.window.end,
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
            entry_date=scenario.window.end,
            highest_close=final_prices[symbol],
            lifecycle="CORE",
            tranches=[
                Tranche(
                    tranche_id=f"{scenario.window.start}:{symbol}:1",
                    lifecycle="CORE",
                    shares=1,
                    avg_cost=10.0,
                    entry_date=scenario.window.end,
                    sellable_date=scenario.window.end,
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
            "cash": 100.0,
            "equity": 100.0,
            "gross_exposure": 0.0,
            "net_exposure": 0.0,
            "cash_weight": 1.0,
            "position_weights": {},
            "daily_pnl": 0.0,
            "target_weights": {first: 0.1, second: 0.1},
            "target_gross": 0.2,
            "caps": {"risk_gross": 0.9, "system_gross": 1.0},
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
            "caps": {"risk_gross": 0.9, "system_gross": 1.0},
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

    def order_snapshot(
        *,
        index: int,
        symbol: str,
        identity_values: Mapping[str, Any],
        snapshot_kind: str,
    ) -> dict[str, Any]:
        return {
            "order_id": f"O{index:09d}",
            "signal_date": scenario.window.start,
            "snapshot_kind": snapshot_kind,
            "symbol": symbol,
            "side": "BUY",
            "target_weight": 0.1,
            "reduction_policy": "FIFO",
            "reason_code": "strategy_target",
            "exit_kind": "strategy",
            **identity_values,
        }

    trace_specs = (
        (
            scenario.window.start,
            (
                (first, initial_target_identities[0], scenario.window.start),
                (second, initial_target_identities[1], scenario.window.start),
            ),
            (
                order_snapshot(
                    index=1,
                    symbol=first,
                    identity_values=order_identities[0],
                    snapshot_kind="ORIGIN",
                ),
                order_snapshot(
                    index=2,
                    symbol=second,
                    identity_values=order_identities[1],
                    snapshot_kind="ORIGIN",
                ),
            ),
        ),
        (
            scenario.window.end,
            (
                (first, order_identities[0], scenario.window.start),
                (second, final_target_identities[1], scenario.window.end),
            ),
            (
                order_snapshot(
                    index=1,
                    symbol=first,
                    identity_values=order_identities[0],
                    snapshot_kind="CARRIED_FORWARD",
                ),
            ),
        ),
    )
    traces = [
        {
            "schema": "uquant.decision-control-plane.v2",
            "date": date,
            "opportunity": "CHOPPY",
            "risk": {
                "state": "NORMAL",
                "target_gross_cap": 0.9,
                "system_gross_cap": 1.0,
            },
            "target_gross": 0.2,
            "targets": [
                target(symbol, identity_values, signal_date)
                for symbol, identity_values, signal_date in target_specs
            ],
            "orders": list(order_snapshots),
            "effective_config_sha256": config_fingerprint(),
        }
        for date, target_specs, order_snapshots in trace_specs
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
                "cash": 100.0,
                "position_shares": {},
                "close_marks": {},
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
    first["cash"] = 101.0
    first["equity"] = 101.0
    first["daily_pnl"] = 1.0
    second["daily_pnl"] = 1.0
    cell["raw"]["daily_replay_evidence"][0]["cash"] = 101.0
    cell["raw"]["equity_curve"][0]["equity"] = 101.0

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert any("daily replay evidence" in failure for failure in failures), failures


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
    cell["raw"]["daily_replay_evidence"][-1]["close_marks"][symbol] = 12.0
    _first, second = cell["attribution"]["daily_ledger"]
    second["position_weights"][symbol] = 12.0 / 102.0
    other = next(item for item in second["position_weights"] if item != symbol)
    cell["raw"]["daily_replay_evidence"][-1]["close_marks"][other] = 10.0
    second["position_weights"][other] = 10.0 / 102.0

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert any("close versus frozen data" in failure for failure in failures), failures


























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


















def _write_source_fixture(root: Path) -> None:
    paths = {
        "pyproject.toml": "[project]\nname='fixture'\n",
        "requirements.txt": "pandas==3.0.5\n",
        "uv.lock": "version = 1\n",
        "benchmarks/reference_registry.json": '{"reference_symbols":["a"]}\n',
        "benchmarks/config_parameter_governance.json": '{"artifact_sha256":"1"}\n',
        "uquant/module.py": "VALUE = 1\n",
        "uquant/validation/resources/ai_universe_manifest.json": '{"members":[]}\n',
    }
    for relative, content in paths.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")



from _generalization_matrix_replay_cases import (
    test_matrix_rejects_self_signed_unreplayable_risk_diagnostics,
    test_matrix_rejects_account_ledger_order_without_decision_origin,
    test_matrix_rejects_order_replayed_on_its_terminal_session,
    test_matrix_and_policy_reject_self_signed_system_cap,
    test_matrix_accepts_explicit_hash_verified_config_override,
    test_matrix_uses_exact_canonical_config_cap_precision,
    test_matrix_accepts_one_origin_and_contiguous_partial_order_snapshots,
    test_matrix_rejects_invalid_carried_order_snapshot_lifecycle,
    test_verified_market_cache_is_lookup_order_independent,
)

from _generalization_matrix_projection_cases import (
    test_champion_exact_equality_passes_but_mutation_fails,
    test_v2_projection_uses_reconstructed_legacy_control_and_only_normalizes_validated_bindings,
    test_v2_projection_normalizes_only_compile_anchored_config_deletion,
    test_v2_policy_evaluator_accepts_verified_fixture_exact_equality,
    test_v2_policy_evaluator_fails_before_projection_without_verified_data,
    test_v2_policy_evaluator_validates_control_plane_before_frozen_projection,
    test_matrix_preserves_replay_error_continues_and_excludes_it_from_quantiles,
)

from _generalization_matrix_validation_cases import (
    test_matrix_validator_rejects_replay_error_with_fabricated_metrics_or_missing_cell,
    test_matrix_validation_fails_closed_on_incomplete_or_stale_artifacts,
    test_zero_symbol_pnl_has_defined_non_fabricated_zero_concentration,
    test_matrix_validator_recomputes_top_level_contract,
)

from _generalization_matrix_provenance_cases import (
    test_matrix_source_provenance_rejects_dirty_reference_registry,
    test_matrix_source_provenance_rejects_dirty_config_governance,
    test_matrix_source_provenance_rejects_committed_registry_divergence,
    test_matrix_source_provenance_rejects_committed_governance_divergence,
    test_untouched_champion_exact_equality_is_an_accepted_policy_result,
    test_equal_champion_tail_bounds_survive_a_benign_non_tail_improvement,
    test_grandfathered_random_tail_rejects_worsening_beyond_the_baseline,
    test_champion_equality_acceptance_does_not_hide_a_genuine_cell_degradation,
    test_relative_cell_policy_accepts_equality_and_enforces_literal_boundaries,
    test_zero_reference_turnover_requires_candidate_zero,
    test_exact_equality_fails_closed_for_every_incomplete_or_mismatched_binding,
    test_exact_equality_rejects_schema_presence_and_raw_evidence_drift,
)
