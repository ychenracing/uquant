from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import inspect
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from uquant.config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from uquant.types import (
    AccountOrder,
    AccountState,
    AttributionMechanism,
    Decision,
    Fill,
    Lifecycle,
    Opportunity,
    OrderStatus,
    OriginSubsystem,
    PendingOrder,
    ReductionPolicy,
    Risk,
    RiskAssessment,
    Side,
    Target,
    derive_attribution_event_id,
)

ROOT = Path(__file__).parents[1]
PUBLIC_API_PATH = ROOT / "benchmarks" / "public_api_contract.json"
VALIDATION_PATH = ROOT / "tests" / "fixtures" / "compatibility_config_validation_contract.json"

_ANALYSIS_SPEC = importlib.util.spec_from_file_location(
    "compatibility_architecture_analysis",
    ROOT / "tests" / "architecture" / "_analysis.py",
)
assert _ANALYSIS_SPEC is not None and _ANALYSIS_SPEC.loader is not None
_ANALYSIS = importlib.util.module_from_spec(_ANALYSIS_SPEC)
_ANALYSIS_SPEC.loader.exec_module(_ANALYSIS)
public_module_contract = _ANALYSIS.public_module_contract


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


PUBLIC_API = _load_json(PUBLIC_API_PATH)["contract"]
VALIDATION_CASES = _load_json(VALIDATION_PATH)["cases"]
assert isinstance(PUBLIC_API, dict)
assert isinstance(VALIDATION_CASES, list)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sample_models() -> dict[str, object]:
    identity = {
        "event_id": "evt_" + "1" * 64,
        "origin_subsystem": OriginSubsystem.LEADER.value,
        "mechanism": AttributionMechanism.LEADER_SELECTION.value,
        "origin_lifecycle": Lifecycle.CORE.value,
        "replaces_symbol": None,
        "industry_at_entry": "technology",
        "industry_manifest_sha256": "2" * 64,
    }
    pending = PendingOrder(
        signal_date="2026-08-21",
        symbol="sz300308",
        side=Side.BUY.value,
        target_weight=0.25,
        reason="leader",
        lifecycle=Lifecycle.CORE.value,
        remaining_shares=100,
        attempts=1,
        order_id="ord-1",
        **identity,
    )
    order = AccountOrder(
        order_id="ord-1",
        signal_date="2026-08-21",
        submitted_date="2026-08-22",
        symbol="sz300308",
        side=Side.BUY.value,
        target_weight=0.25,
        reason="leader",
        lifecycle=Lifecycle.CORE.value,
        requested_shares=100,
        remaining_shares=100,
        last_update_date="2026-08-22",
        **identity,
    )
    fill = Fill(
        signal_date="2026-08-21",
        fill_date="2026-08-22",
        symbol="sz300308",
        side=Side.BUY.value,
        shares=100,
        price=10.0,
        gross_value=1000.0,
        commission=5.0,
        stamp_duty=0.0,
        transfer_fee=0.01,
        slippage_cost=1.0,
        reason="leader",
        lifecycle=Lifecycle.CORE.value,
        order_id="ord-1",
        fill_id="fill-1",
        **identity,
    )
    target = Target(
        symbol="sz300308",
        weight=0.25,
        lifecycle=Lifecycle.CORE.value,
        alpha_score=0.9,
        confidence=0.8,
        reason="leader",
        **identity,
    )
    risk = RiskAssessment(
        state=Risk.NORMAL,
        target_gross_cap=1.0,
        votes=0,
        evidence={"breadth": 0.75},
        reasons=("normal",),
        shock_state="NONE",
    )
    decision = Decision(
        date="2026-08-21",
        opportunity=Opportunity.TREND,
        risk=Risk.NORMAL,
        target_gross=0.25,
        target_k=1,
        targets=(target,),
        pending_orders=(pending,),
        risk_summary={"target_gross_cap": 1.0, "system_gross_cap": 1.0},
        decision_digest="3" * 64,
    )
    account = AccountState.empty(1234.5)
    account.pending_orders.append(pending)
    account.order_ledger.append(order)
    account.fills.append(fill)
    return {
        "account": account,
        "decision": decision,
        "fill": fill,
        "order": order,
        "pending": pending,
        "risk": risk,
        "target": target,
    }


def test_all_281_system_config_fields_match_the_frozen_flat_contract() -> None:
    expected_flat = PUBLIC_API["flat_config_serialization"]
    expected_module = PUBLIC_API["modules"]["uquant.config"]
    assert isinstance(expected_flat, Mapping)
    assert isinstance(expected_module, Mapping)
    observed_module = public_module_contract("uquant.config")
    fields = dataclasses.fields(SystemConfig)
    payload = DEFAULT_CONFIG.to_dict()

    assert len(fields) == 281
    assert [field.name for field in fields] == expected_flat["field_order"]
    assert list(payload) == expected_flat["field_order"]
    assert payload == expected_flat["values"]
    assert config_fingerprint(DEFAULT_CONFIG) == expected_flat["sha256"]
    assert observed_module["public_names"] == expected_module["public_names"]
    assert observed_module["classes"] == expected_module["classes"]
    assert observed_module["dataclasses"] == expected_module["dataclasses"]
    assert observed_module["functions"] == expected_module["functions"]


def test_system_config_flat_construction_override_and_value_semantics_are_exact() -> None:
    payload = DEFAULT_CONFIG.to_dict()
    reconstructed = SystemConfig(**payload)
    changed = DEFAULT_CONFIG.override(max_positions=5)

    assert inspect.signature(SystemConfig.override) == inspect.Signature(
        parameters=[
            inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter(
                "changes",
                inspect.Parameter.VAR_KEYWORD,
                annotation="Any",
            ),
        ],
        return_annotation="SystemConfig",
    )
    assert reconstructed == DEFAULT_CONFIG
    assert reconstructed is not DEFAULT_CONFIG
    assert repr(reconstructed) == repr(DEFAULT_CONFIG)
    assert hash(reconstructed) == hash(DEFAULT_CONFIG)
    assert type(changed) is SystemConfig
    assert changed.max_positions == 5
    assert DEFAULT_CONFIG.max_positions == 6
    assert not hasattr(DEFAULT_CONFIG, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_CONFIG.max_positions = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "case",
    VALIDATION_CASES,
    ids=lambda case: "+".join(case["changes"]),
)
def test_all_frozen_config_validation_types_messages_and_order_are_exact(
    case: dict[str, object],
) -> None:
    changes = case["changes"]
    assert isinstance(changes, dict)
    with pytest.raises(Exception) as captured:
        DEFAULT_CONFIG.override(**changes)

    assert type(captured.value).__name__ == case["exception_type"]
    assert str(captured.value) == case["message"]


def test_enum_literals_and_representative_model_bytes_are_frozen() -> None:
    samples = _sample_models()
    enum_values = {
        enum.__name__: [member.value for member in enum]
        for enum in (
            Opportunity,
            Risk,
            Lifecycle,
            OriginSubsystem,
            AttributionMechanism,
            Side,
            OrderStatus,
            ReductionPolicy,
        )
    }
    decision = samples["decision"]
    assert isinstance(decision, Decision)
    serialized = {
        "account": samples["account"].to_dict(),  # type: ignore[union-attr]
        "decision": dataclasses.asdict(decision),
        "decision_payload": decision.canonical_payload(effective_config_sha256="4" * 64),
        "enums": enum_values,
        "fill": dataclasses.asdict(samples["fill"]),
        "order": dataclasses.asdict(samples["order"]),
        "pending": dataclasses.asdict(samples["pending"]),
        "risk": dataclasses.asdict(samples["risk"]),
        "target": dataclasses.asdict(samples["target"]),
    }
    expected = {
        "account": "21b08f5925b4029d06065484030c9d229dfadf6837227c1c5ab878ce1f1c5e22",
        "decision": "75ce8bd276a9fc876c6e1055c887e09e778f518f6ebe4a60b5410eaf675bdb3b",
        "decision_payload": "4b04a403e9463d1cf94d1b64398555048270b52096f8fd4c9e056b17c19267f0",
        "enums": "55848e6711e2b9ce2021f8a8ffb5f07673d3d8cc2b622274f354cadc4f2b45e0",
        "fill": "cd1a8a954049594018ee18abaa2b2b49988748b7a3df646bbfd4b49688175399",
        "order": "c9a8428396c9e321bc4323a76b2f0c3b99bf028d36d78edbd347d372e8db925c",
        "pending": "12f47a83e1d70f7ff614a5ac21c6a52a173f37ea28a3acbd7891b16f64bbc3d2",
        "risk": "349973e8efcd6444e1dbbc00e80909ad9cd52e332ceeaedcd3dfb2c682f51fce",
        "target": "40deb0e6450d7bdb5eaf7da4ff263501ac70c4a970aa1a56695bfe1b104b982d",
    }

    assert {name: _canonical_sha256(value) for name, value in serialized.items()} == expected


def test_model_field_order_defaults_factories_and_flat_account_schema_are_frozen() -> None:
    expected_module = PUBLIC_API["modules"]["uquant.types"]
    expected_schema = PUBLIC_API["account_state_schema"]
    assert isinstance(expected_module, Mapping)
    assert isinstance(expected_schema, Mapping)
    observed_module = public_module_contract("uquant.types")
    empty = AccountState.empty(DEFAULT_CONFIG.initial_cash)

    assert observed_module["public_names"] == expected_module["public_names"]
    assert observed_module["classes"] == expected_module["classes"]
    assert observed_module["dataclasses"] == expected_module["dataclasses"]
    assert observed_module["enums"] == expected_module["enums"]
    assert observed_module["functions"] == expected_module["functions"]
    assert len(dataclasses.fields(AccountState)) == 84
    assert [field.name for field in dataclasses.fields(AccountState)] == expected_schema["field_order"]
    assert list(empty.to_dict()) == expected_schema["serialized_key_order"]
    assert empty.to_dict() == expected_schema["empty_state"]
    assert _canonical_sha256(empty.to_dict()) == expected_schema["empty_state_sha256"]
    other = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    assert empty.positions is not other.positions
    assert empty.pending_orders is not other.pending_orders
    assert empty.fills is not other.fills


def test_model_mutability_equality_hash_repr_slots_and_identity_are_frozen() -> None:
    samples = _sample_models()
    order = samples["order"]
    target = samples["target"]
    risk = samples["risk"]
    decision = samples["decision"]
    assert isinstance(order, AccountOrder)
    assert isinstance(target, Target)
    assert isinstance(risk, RiskAssessment)
    assert isinstance(decision, Decision)

    assert all(not hasattr(value, "__dict__") for value in samples.values())
    assert dataclasses.replace(target) == target
    assert dataclasses.replace(risk) == risk
    assert dataclasses.replace(decision) == decision
    assert hash(target) == hash(dataclasses.replace(target))
    with pytest.raises(TypeError, match="unhashable"):
        hash(risk)
    with pytest.raises(TypeError, match="unhashable"):
        hash(decision)
    with pytest.raises(TypeError, match="unhashable"):
        hash(order)
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.weight = 0.5  # type: ignore[misc]
    order.attempts = 2
    assert order.attempts == 2
    assert hashlib.sha256(repr(order).encode()).hexdigest() == (
        "b8b1038a4fc35be9c574254989552a55d3543df4a496801bd9787d576fa26806"
    )
    assert hashlib.sha256(repr(target).encode()).hexdigest() == (
        "84193350af5a31235610c50c51cdf69c3b692daa02f1ba0c04aaba9a1b72b977"
    )
    assert hashlib.sha256(repr(risk).encode()).hexdigest() == (
        "19dd60d8cb1c0cc9b73d2747f850ced2feacfa636fe527d30d8f731d1c73d852"
    )
    assert (
        derive_attribution_event_id(
            signal_date="2026-08-21",
            symbol="sz300308",
            target_weight=0.25,
            lifecycle="CORE",
            origin_lifecycle="CORE",
            origin_subsystem="LEADER",
            mechanism="LEADER_SELECTION",
            replaces_symbol=None,
            industry_at_entry="technology",
            industry_manifest_sha256="2" * 64,
            reduction_policy="FIFO",
            reason_code="strategy_target",
            exit_kind="strategy",
        )
        == "evt_d319f95ad6bc72805717179455cbcd29a14c165f04b625c9353d24045e31f9d7"
    )
