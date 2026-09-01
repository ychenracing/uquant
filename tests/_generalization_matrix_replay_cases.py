from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization_matrix import (
    _fixture_reference_contract,
    _provenance,
    _runner_payload,
    _scenarios,
    _write_verified_market,
)

from uquant.config import (
    DEFAULT_CONFIG,
    canonical_control_float,
    config_fingerprint,
)
from uquant.models.trading import derive_attribution_event_id
from uquant.validation import generalization_reference as reference_module
from uquant.validation.control_plane import legacy_decision_payload
from uquant.validation.generalization_matrix import (
    execute_generalization_matrix,
    validate_matrix_artifact,
)
from uquant.validation.generalization_reference import (
    evaluate_generalization_policy_artifact,
)


@pytest.mark.parametrize(
    ("shock_state", "severity", "reduction_level"),
    (
        ("SELF_SIGNED_FAKE", "SELF_SIGNED_FAKE", 0),
        ("SHOCK", "SEVERE", 3),
    ),
)
def test_matrix_rejects_self_signed_unreplayable_risk_diagnostics(
    shock_state: str,
    severity: str,
    reduction_level: int,
    matrix_data_dir: Path,
) -> None:
    """Catches both invented and valid-enum diagnostics hidden from frozen equality."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    raw = next(item["raw"] for item in changed["cells"] if item["economic"])
    risk = raw["decision_trace"][0]["risk"]
    risk.update(
        shock_state=shock_state,
        severity=severity,
        reduction_level=reduction_level,
    )
    raw["decision_digests"][0] = hashlib.sha256(
        json.dumps(
            raw["decision_trace"][0],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert failures
    assert any("decision trace" in failure for failure in failures)

def test_matrix_rejects_account_ledger_order_without_decision_origin(
    matrix_data_dir: Path,
) -> None:
    """Catches a valid durable order appended without a causal decision trace."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    raw = next(item["raw"] for item in changed["cells"] if item["economic"])
    account = raw["final_account"]
    injected = copy.deepcopy(account["order_ledger"][0])
    injected.update(
        order_id="O000000003",
        target_weight=0.2,
        status="CANCELLED",
        requested_shares=0,
        filled_shares=0,
        remaining_shares=0,
        attempts=0,
        last_event="CANCELLED",
        cancel_reason="fabricated no-decision order",
    )
    injected["event_id"] = derive_attribution_event_id(
        signal_date=injected["signal_date"],
        symbol=injected["symbol"],
        target_weight=injected["target_weight"],
        lifecycle=injected["lifecycle"],
        origin_lifecycle=injected["origin_lifecycle"],
        origin_subsystem=injected["origin_subsystem"],
        mechanism=injected["mechanism"],
        replaces_symbol=injected["replaces_symbol"],
        industry_at_entry=injected["industry_at_entry"],
        industry_manifest_sha256=injected["industry_manifest_sha256"],
        reduction_policy=injected["reduction_policy"],
        reason_code=injected["reason_code"],
        exit_kind=injected["exit_kind"],
    )
    account["order_ledger"].append(injected)
    account["next_order_sequence"] = 4

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert failures
    assert any(
        "durable account order O000000003 decision snapshot lifecycle differs"
        in failure
        for failure in failures
    )

def test_matrix_rejects_order_replayed_on_its_terminal_session(
    matrix_data_dir: Path,
) -> None:
    """Catches a filled order ID being reintroduced in a later decision snapshot."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    raw = next(item["raw"] for item in changed["cells"] if item["economic"])
    origin = copy.deepcopy(raw["decision_trace"][0]["orders"][1])
    origin["snapshot_kind"] = "CARRIED_FORWARD"
    raw["decision_trace"][-1]["orders"].append(origin)
    raw["decision_digests"][-1] = hashlib.sha256(
        json.dumps(
            raw["decision_trace"][-1],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    raw["legacy_decision_digests"][-1] = hashlib.sha256(
        json.dumps(
            legacy_decision_payload(raw["decision_trace"][-1]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert failures
    assert any(
        "terminal" in failure or "snapshot lifecycle" in failure
        for failure in failures
    ), failures

def test_matrix_and_policy_reject_self_signed_system_cap(
    matrix_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a trace/ledger cap rewrite hidden by the frozen-v1 projection."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    for cell in changed["cells"]:
        if not cell["economic"]:
            continue
        raw = cell["raw"]
        for index, trace in enumerate(raw["decision_trace"]):
            trace["risk"]["system_gross_cap"] = 0.8
            raw["decision_digests"][index] = hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        for row in cell["attribution"]["daily_ledger"]:
            row["caps"]["system_gross"] = 0.8

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert any("system gross cap" in failure for failure in failures), failures

    baseline, policy = _fixture_reference_contract(artifact)
    monkeypatch.setattr(
        reference_module,
        "_head_and_source",
        lambda _root: (
            artifact["provenance"]["head"],
            artifact["provenance"]["source_sha256"],
        ),
    )
    report = evaluate_generalization_policy_artifact(
        changed,
        baseline=baseline,
        policy=policy,
        require_exact_equality=True,
        data_dir=matrix_data_dir,
    )
    assert report["passed"] is False
    assert any("system gross cap" in failure for failure in report["failures"])

def test_matrix_accepts_explicit_hash_verified_config_override(
    matrix_data_dir: Path,
) -> None:
    """Ablations may supply one trusted config object; artifact hashes cannot choose it."""

    scenarios = _scenarios()
    expected_config = DEFAULT_CONFIG.override(max_gross=0.99)
    expected_sha256 = config_fingerprint(expected_config)
    provenance = _provenance(
        scenarios,
        matrix_data_dir,
        expected_config=expected_config,
    )

    def runner(scenario: Any) -> dict[str, Any]:
        raw = _runner_payload(scenario)
        raw["effective_config_sha256"] = expected_sha256
        for index, trace in enumerate(raw["decision_trace"]):
            trace["effective_config_sha256"] = expected_sha256
            trace["risk"]["system_gross_cap"] = expected_config.max_gross
            raw["decision_digests"][index] = hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        for row in raw["attribution"]["daily_ledger"]:
            row["caps"]["system_gross"] = expected_config.max_gross
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=provenance,
        data_dir=matrix_data_dir,
        expected_config=expected_config,
    )

    assert artifact["passed"] is True
    assert validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        expected_config=expected_config,
    ) == ()
    default_failures = validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )
    assert any("trusted effective config" in failure for failure in default_failures)

def test_matrix_uses_exact_canonical_config_cap_precision(
    matrix_data_dir: Path,
) -> None:
    """A sub-12-decimal config is serialized once; a different serialized cap fails."""

    scenarios = _scenarios()
    expected_config = DEFAULT_CONFIG.override(max_gross=0.999_999_999_999_9)
    expected_sha256 = config_fingerprint(expected_config)
    canonical_cap = canonical_control_float(expected_config.max_gross)
    provenance = _provenance(
        scenarios,
        matrix_data_dir,
        expected_config=expected_config,
    )

    def runner(scenario: Any) -> dict[str, Any]:
        raw = _runner_payload(scenario)
        raw["effective_config_sha256"] = expected_sha256
        for index, trace in enumerate(raw["decision_trace"]):
            trace["effective_config_sha256"] = expected_sha256
            trace["risk"]["system_gross_cap"] = canonical_cap
            raw["decision_digests"][index] = hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        for row in raw["attribution"]["daily_ledger"]:
            row["caps"]["system_gross"] = canonical_cap
        return raw

    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=runner,
        provenance=provenance,
        data_dir=matrix_data_dir,
        expected_config=expected_config,
    )
    assert artifact["passed"] is True

    forged = copy.deepcopy(artifact)
    for cell in forged["cells"]:
        if not cell["economic"]:
            continue
        raw = cell["raw"]
        for index, trace in enumerate(raw["decision_trace"]):
            trace["risk"]["system_gross_cap"] = 0.999_999_999_999
            raw["decision_digests"][index] = hashlib.sha256(
                json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        for row in cell["attribution"]["daily_ledger"]:
            row["caps"]["system_gross"] = 0.999_999_999_999
    failures = validate_matrix_artifact(
        forged,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
        expected_config=expected_config,
    )
    assert any("system gross cap" in failure for failure in failures), failures

def test_matrix_accepts_one_origin_and_contiguous_partial_order_snapshots(
    matrix_data_dir: Path,
) -> None:
    """Locks the legitimate retained/partial cross-session order lifecycle."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    raw = next(item["raw"] for item in artifact["cells"] if item["economic"])
    snapshots = [
        order
        for trace in raw["decision_trace"]
        for order in trace["orders"]
        if order["order_id"] == "O000000001"
    ]
    durable = raw["final_account"]["order_ledger"][0]

    assert [item["snapshot_kind"] for item in snapshots] == [
        "ORIGIN",
        "CARRIED_FORWARD",
    ]
    assert durable["status"] == "PARTIALLY_FILLED"
    assert durable["remaining_shares"] == 1
    assert raw["final_account"]["pending_orders"][0]["order_id"] == "O000000001"
    assert validate_matrix_artifact(
        artifact,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    ) == ()

@pytest.mark.parametrize(
    "mutation",
    (
        "changed_signal_date",
        "duplicate_origin",
        "duplicate_snapshot_row",
        "missing_carried_snapshot",
    ),
)
def test_matrix_rejects_invalid_carried_order_snapshot_lifecycle(
    mutation: str,
    matrix_data_dir: Path,
) -> None:
    """Catches detached origin data, duplicate intent, and noncontiguous retention."""

    scenarios = _scenarios()
    provenance = _provenance(scenarios, matrix_data_dir)
    artifact = execute_generalization_matrix(
        scenarios=scenarios,
        runner=_runner_payload,
        provenance=provenance,
        data_dir=matrix_data_dir,
    )
    changed = copy.deepcopy(artifact)
    raw = next(item["raw"] for item in changed["cells"] if item["economic"])
    carried = raw["decision_trace"][-1]["orders"][0]
    if mutation == "changed_signal_date":
        carried["signal_date"] = raw["decision_trace"][-1]["date"]
    elif mutation == "duplicate_origin":
        carried["snapshot_kind"] = "ORIGIN"
    elif mutation == "duplicate_snapshot_row":
        raw["decision_trace"][-1]["orders"].append(copy.deepcopy(carried))
    else:
        raw["decision_trace"][-1]["orders"] = []
    raw["decision_digests"][-1] = hashlib.sha256(
        json.dumps(
            raw["decision_trace"][-1],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    raw["legacy_decision_digests"][-1] = hashlib.sha256(
        json.dumps(
            legacy_decision_payload(raw["decision_trace"][-1]),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    failures = validate_matrix_artifact(
        changed,
        scenarios=scenarios,
        expected_provenance=provenance,
        data_dir=matrix_data_dir,
    )

    assert failures
    assert any(
        "decision order" in failure
        or "order IDs" in failure
        or "snapshot lifecycle" in failure
        for failure in failures
    ), failures

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
