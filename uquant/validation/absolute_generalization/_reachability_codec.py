"""Private strict JSON transport for Task 6 production reachability states."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, fields, is_dataclass
from enum import Enum
from typing import Any, cast

from uquant.config import DEFAULT_CONFIG, SystemConfig, config_fingerprint
from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads
from uquant.models.decision import LeaderScore, RiskAssessment, Target
from uquant.models.enums import Risk
from uquant.models.strategic_epoch import StrategicEpoch
from uquant.models.strategic_grant import (
    StrategicGrantIntent,
    StrategicQualificationObservation,
    validate_strategic_qualification,
)
from uquant.models.strategic_universe import StrategicUniverseRoles
from uquant.models.trading import AccountOrder, Fill

from .replay import AbsoluteGeneralizationReplayPayload

_STATE_FIELDS = frozenset(
    {
        "active_epoch_state",
        "account_payload",
        "capital_budget_level",
        "cfg",
        "flat_all_cash",
        "grant_state",
        "leaders",
        "opportunity_state",
        "outlet_evidence",
        "pending_execution",
        "protected_authority",
        "qualification_quorum",
        "qualification_ready",
        "qualification_route",
        "recovery_authority",
        "reference_available",
        "repair_status",
        "restore_authority",
        "risk",
        "risk_state",
        "snapshots",
        "universe",
        "unknown_execution",
    }
)
_OUTLET_FIELDS = frozenset({"target", "grant", "epoch", "orders", "fills"})
_MAX_STATE_BYTES = 4 * 1024 * 1024
_MAX_MARKET_ROWS = 256
_DECISION_RUNTIME_FIELDS = frozenset(
    {
        "effective_config_sha256",
        "risk_assessment",
        "strategic_universe_roles",
        "strategic_qualification",
        "strategic_successor_qualification",
        "leader_scores",
        "qualification_snapshots",
    }
)


def _reachability_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"absolute reachability {label} raw evidence is malformed")
    return cast(Mapping[str, object], value)


def _reachability_sequence(value: object, *, label: str) -> Sequence[object]:
    if type(value) not in {list, tuple}:
        raise ValueError(f"absolute reachability {label} raw evidence is malformed")
    return cast(Sequence[object], value)


def _reachability_plain(value: object) -> object:
    if isinstance(value, AbsoluteGeneralizationReplayPayload):
        if hashlib.sha256(value.canonical_json).hexdigest() != value.sha256:
            raise ValueError("absolute reachability account payload digest differs")
        return strict_json_loads(value.canonical_json)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _reachability_plain(asdict(value))
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("absolute reachability raw mapping key differs")
        return {str(key): _reachability_plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_reachability_plain(item) for item in value]
    return value


def reachability_state_to_raw(value: object) -> dict[str, object]:
    """Serialize one already-typed Task 6 state without policy conclusions."""

    state = _reachability_mapping(value, label="state")
    if set(state) != _STATE_FIELDS:
        raise ValueError("absolute reachability state raw fields differ")
    raw = cast(dict[str, object], _reachability_plain(state))
    cfg = state["cfg"]
    if not isinstance(cfg, SystemConfig):
        raise ValueError("absolute reachability config runtime type differs")
    raw["cfg"] = config_fingerprint(cfg)
    encoded = canonical_json_bytes(raw)
    if len(encoded) > _MAX_STATE_BYTES:
        raise ValueError("absolute reachability state raw evidence is unbounded")
    return raw


def _risk_from_raw(value: object) -> RiskAssessment:
    raw = _reachability_mapping(value, label="risk")
    expected = {field.name for field in fields(RiskAssessment)}
    if set(raw) != expected:
        raise ValueError("absolute reachability risk raw fields differ")
    values = dict(raw)
    if type(values["state"]) is not str:
        raise ValueError("absolute reachability risk state differs")
    values["state"] = Risk(values["state"])
    reasons = _reachability_sequence(values["reasons"], label="risk reasons")
    if any(type(item) is not str for item in reasons):
        raise ValueError("absolute reachability risk reasons differ")
    values["reasons"] = tuple(reasons)
    values["evidence"] = dict(_reachability_mapping(values["evidence"], label="risk evidence"))
    return RiskAssessment(**cast(dict[str, Any], values))


def _reachability_roles_from_raw(value: object) -> StrategicUniverseRoles:
    raw = _reachability_mapping(value, label="universe")
    expected = {field.name for field in fields(StrategicUniverseRoles)}
    if set(raw) != expected:
        raise ValueError("absolute reachability universe raw fields differ")
    values = dict(raw)
    text_fields = {
        "as_of",
        "tradable_identity",
        "qualification_reference_identity",
        "risk_reference_identity",
        "point_in_time_industry_identity",
    }
    if any(type(values[name]) is not str or not values[name] for name in text_fields):
        raise ValueError("absolute reachability universe raw identity differs")
    for name in (
        "tradable_symbols",
        "qualification_reference_symbols",
        "risk_reference_symbols",
        "available_symbols",
        "unavailable_reference_symbols",
    ):
        items = _reachability_sequence(values[name], label=name)
        if any(type(item) is not str or not item for item in items):
            raise ValueError("absolute reachability universe raw symbols differ")
        values[name] = tuple(items)
    industry_rows = tuple(
        tuple(_reachability_sequence(item, label="industry row"))
        for item in _reachability_sequence(values["point_in_time_industries"], label="industries")
    )
    if any(
        len(item) != 2 or any(type(value) is not str or not value for value in item) for item in industry_rows
    ):
        raise ValueError("absolute reachability universe raw industries differ")
    values["point_in_time_industries"] = industry_rows
    return StrategicUniverseRoles(**cast(dict[str, Any], values))


def _leaders_from_raw(value: object) -> dict[str, LeaderScore]:
    raw = _reachability_mapping(value, label="leaders")
    expected = {field.name for field in fields(LeaderScore)}
    if len(raw) > _MAX_MARKET_ROWS:
        raise ValueError("absolute reachability leader raw evidence is unbounded")
    result: dict[str, LeaderScore] = {}
    for symbol, item in raw.items():
        row = _reachability_mapping(item, label="leader")
        if set(row) != expected or row.get("symbol") != symbol:
            raise ValueError("absolute reachability leader raw fields differ")
        values = dict(row)
        components = _reachability_mapping(values["components"], label="leader components")
        numeric = (values["score"], values["confidence"])
        if any(
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(float(number))
            for number in numeric
        ):
            raise ValueError("absolute reachability leader raw evidence is malformed")
        finite_components: dict[str, int | float] = {}
        for name, number in components.items():
            if type(name) is not str:
                raise ValueError("absolute reachability leader raw evidence is malformed")
            if type(number) is str and number in {"NaN", "Infinity", "-Infinity"}:
                continue
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(float(number))
            ):
                raise ValueError("absolute reachability leader raw evidence is malformed")
            finite_components[name] = number
        values["components"] = finite_components
        result[symbol] = LeaderScore(**cast(dict[str, Any], values))
    return result


def _snapshots_from_raw(value: object) -> dict[str, dict[str, float]]:
    raw = _reachability_mapping(value, label="snapshots")
    if len(raw) > _MAX_MARKET_ROWS:
        raise ValueError("absolute reachability snapshot raw evidence is unbounded")
    result: dict[str, dict[str, float]] = {}
    for symbol, item in raw.items():
        values = _reachability_mapping(item, label="snapshot")
        finite_values: dict[str, float] = {}
        for name, number in values.items():
            if type(name) is not str:
                raise ValueError("absolute reachability snapshot raw evidence is malformed")
            if type(number) is str and number in {"NaN", "Infinity", "-Infinity"}:
                continue
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(float(number))
            ):
                raise ValueError("absolute reachability snapshot raw evidence is malformed")
            finite_values[name] = float(number)
        result[symbol] = finite_values
    return result


def decision_runtime_inputs_from_raw(
    payload: AbsoluteGeneralizationReplayPayload,
) -> dict[str, object]:
    """Rehydrate the exact same-close market facts retained by production decide."""

    if (
        type(payload) is not AbsoluteGeneralizationReplayPayload
        or hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256
    ):
        raise ValueError("absolute decision runtime payload identity differs")
    raw = _reachability_mapping(strict_json_loads(payload.canonical_json), label="decision runtime")
    if set(raw) != _DECISION_RUNTIME_FIELDS:
        raise ValueError("absolute decision runtime raw fields differ")
    if raw["effective_config_sha256"] != config_fingerprint(DEFAULT_CONFIG):
        raise ValueError("absolute decision runtime config identity differs")
    leader_rows = _reachability_sequence(raw["leader_scores"], label="decision leaders")
    if len(leader_rows) > _MAX_MARKET_ROWS:
        raise ValueError("absolute decision runtime leaders are unbounded")
    leader_mapping: dict[str, object] = {}
    for item in leader_rows:
        row = _reachability_mapping(item, label="decision leader")
        symbol = row.get("symbol")
        if type(symbol) is not str or not symbol or symbol in leader_mapping:
            raise ValueError("absolute decision runtime leader identity differs")
        leader_mapping[symbol] = dict(row)
    qualifications: list[StrategicQualificationObservation] = []
    for name in (
        "strategic_qualification",
        "strategic_successor_qualification",
    ):
        observation = StrategicQualificationObservation(
            **cast(dict[str, Any], dict(_reachability_mapping(raw[name], label=name)))
        )
        validate_strategic_qualification(observation)
        qualifications.append(observation)
    return {
        "effective_config_sha256": raw["effective_config_sha256"],
        "risk": _risk_from_raw(raw["risk_assessment"]),
        "universe": _reachability_roles_from_raw(raw["strategic_universe_roles"]),
        "strategic_qualification": qualifications[0],
        "strategic_successor_qualification": qualifications[1],
        "leaders": _leaders_from_raw(leader_mapping),
        "snapshots": _snapshots_from_raw(raw["qualification_snapshots"]),
    }


def _outlet_from_raw(value: object) -> object:
    if value is None:
        return None
    raw = _reachability_mapping(value, label="outlet")
    if set(raw) != _OUTLET_FIELDS:
        raise ValueError("absolute reachability outlet raw fields differ")
    return {
        "target": Target(
            **cast(
                dict[str, Any],
                dict(_reachability_mapping(raw["target"], label="target")),
            )
        ),
        "grant": StrategicGrantIntent(
            **cast(
                dict[str, Any],
                dict(_reachability_mapping(raw["grant"], label="grant")),
            )
        ),
        "epoch": StrategicEpoch(
            **cast(
                dict[str, Any],
                dict(_reachability_mapping(raw["epoch"], label="epoch")),
            )
        ),
        "orders": tuple(
            AccountOrder(
                **cast(
                    dict[str, Any],
                    dict(_reachability_mapping(item, label="order")),
                )
            )
            for item in _reachability_sequence(raw["orders"], label="orders")
        ),
        "fills": tuple(
            Fill(
                **cast(
                    dict[str, Any],
                    dict(_reachability_mapping(item, label="fill")),
                )
            )
            for item in _reachability_sequence(raw["fills"], label="fills")
        ),
    }


def reachability_state_from_raw(value: object) -> dict[str, object]:
    """Strictly rehydrate one raw state for the public Task 6 analyzers."""

    raw = _reachability_mapping(value, label="state")
    if set(raw) != _STATE_FIELDS:
        raise ValueError("absolute reachability state raw fields differ")
    if raw["cfg"] != config_fingerprint(DEFAULT_CONFIG):
        raise ValueError("absolute reachability config raw identity differs")
    account_raw = _reachability_mapping(raw["account_payload"], label="account")
    account_bytes = canonical_json_bytes(account_raw)
    account_payload = AbsoluteGeneralizationReplayPayload(
        canonical_json=account_bytes,
        sha256=hashlib.sha256(account_bytes).hexdigest(),
    )
    risk = _risk_from_raw(raw["risk"])
    universe = _reachability_roles_from_raw(raw["universe"])
    snapshots = _snapshots_from_raw(raw["snapshots"])
    leaders = _leaders_from_raw(raw["leaders"])
    outlet = _outlet_from_raw(raw["outlet_evidence"])
    encoded = canonical_json_bytes(raw)
    if len(encoded) > _MAX_STATE_BYTES:
        raise ValueError("absolute reachability state raw evidence is unbounded")
    state = dict(raw)
    state.update(
        {
            "account_payload": account_payload,
            "cfg": DEFAULT_CONFIG,
            "risk": risk,
            "universe": universe,
            "snapshots": snapshots,
            "leaders": leaders,
            "outlet_evidence": outlet,
        }
    )
    from .reachability import (  # local import avoids a module initialization cycle
        is_positive_strategic_outlet,
        project_observed_reachability_state,
    )

    projected = project_observed_reachability_state(
        account_payload=account_payload,
        cfg=DEFAULT_CONFIG,
        risk=risk,
        universe=universe,
        snapshots=snapshots,
        leaders=leaders,
        outlet_evidence=outlet,
    )
    claims = _STATE_FIELDS - {
        "account_payload",
        "cfg",
        "risk",
        "universe",
        "snapshots",
        "leaders",
        "outlet_evidence",
    }
    if any(state[name] != projected[name] for name in claims):
        raise ValueError("absolute reachability state raw claims differ")
    if outlet is not None:
        evidence = cast(Mapping[str, object], outlet)
        if not is_positive_strategic_outlet(
            target=cast(Target, evidence["target"]),
            grant=cast(StrategicGrantIntent, evidence["grant"]),
            epoch=cast(StrategicEpoch, evidence["epoch"]),
            orders=cast(tuple[AccountOrder, ...], evidence["orders"]),
            fills=cast(tuple[Fill, ...], evidence["fills"]),
        ):
            raise ValueError("absolute reachability outlet raw evidence differs")
    return state


__all__ = (
    "decision_runtime_inputs_from_raw",
    "reachability_state_from_raw",
    "reachability_state_to_raw",
)
