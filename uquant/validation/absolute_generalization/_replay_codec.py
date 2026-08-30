"""Strict JSON codec for the immutable absolute-generalization replay authority."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date
from typing import cast

from uquant.contracts.strict_json import canonical_json_bytes, strict_json_loads

from .replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayAccountSnapshot,
    AbsoluteGeneralizationReplayManifestSnapshot,
    AbsoluteGeneralizationReplayObservation,
    AbsoluteGeneralizationReplayPayload,
    AbsoluteGeneralizationReplayRoleSnapshot,
)
from .scenarios import AbsoluteGeneralizationScenario


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return cast(Mapping[str, object], value)


def _sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return cast(Sequence[object], value)


def _replay_exact_fields(raw: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization replay evidence {label} fields differ")


def _text(value: object, *, label: str, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return value


def _number(value: object, *, label: str) -> float:
    if type(value) is not float:
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    if not math.isfinite(value):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return value


def _boolean(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return value


def _iso_date(value: object, *, label: str) -> date:
    text = _text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"absolute generalization replay evidence {label} is malformed") from exc


def _strings(value: object, *, label: str) -> tuple[str, ...]:
    return tuple(_text(item, label=label) for item in _sequence(value, label=label))


def _string_pairs(value: object, *, label: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in _sequence(value, label=label):
        pair = _sequence(item, label=label)
        if len(pair) != 2:
            raise ValueError(f"absolute generalization replay evidence {label} is malformed")
        result.append((_text(pair[0], label=label), _text(pair[1], label=label)))
    return tuple(result)


def _number_pairs(value: object, *, label: str) -> tuple[tuple[str, float], ...]:
    result: list[tuple[str, float]] = []
    for item in _sequence(value, label=label):
        pair = _sequence(item, label=label)
        if len(pair) != 2:
            raise ValueError(f"absolute generalization replay evidence {label} is malformed")
        result.append((_text(pair[0], label=label), _number(pair[1], label=label)))
    return tuple(result)


def _payload_to_raw(payload: AbsoluteGeneralizationReplayPayload) -> dict[str, object]:
    if type(payload) is not AbsoluteGeneralizationReplayPayload:
        raise ValueError("absolute generalization replay evidence payload type differs")
    if hashlib.sha256(payload.canonical_json).hexdigest() != payload.sha256:
        raise ValueError("absolute generalization replay evidence payload digest differs")
    value = strict_json_loads(payload.canonical_json)
    if canonical_json_bytes(value) != payload.canonical_json:
        raise ValueError("absolute generalization replay evidence payload is not canonical")
    return {"sha256": payload.sha256, "value": value}


def _replay_json_value(value: object) -> object:
    if type(value) is AbsoluteGeneralizationReplayPayload:
        return _payload_to_raw(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _replay_json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("absolute generalization replay evidence mapping keys differ")
        return {str(key): _replay_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_replay_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("absolute generalization replay evidence contains non-finite value")
        return value
    raise ValueError(f"absolute generalization replay evidence cannot encode {type(value).__name__}")


def replay_to_raw(replay: AbsoluteGeneralizationReplay) -> dict[str, object]:
    """Return strict JSON replay evidence without exposing mutable state."""

    if type(replay) is not AbsoluteGeneralizationReplay:
        raise ValueError("absolute generalization replay evidence type differs")
    raw = _replay_json_value(replay)
    return dict(_mapping(raw, label="replay"))


def _payload_from_raw(value: object, *, label: str) -> AbsoluteGeneralizationReplayPayload:
    raw = _mapping(value, label=label)
    _replay_exact_fields(raw, {"sha256", "value"}, label=label)
    digest = _text(raw["sha256"], label=f"{label} digest")
    encoded = canonical_json_bytes(raw["value"])
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError(f"absolute generalization replay evidence {label} digest differs")
    return AbsoluteGeneralizationReplayPayload(canonical_json=encoded, sha256=digest)


def _scenario_from_raw(value: object) -> AbsoluteGeneralizationScenario:
    raw = _mapping(value, label="scenario")
    expected = {
        "cell_id",
        "removed_symbol",
        "window_start",
        "window_end",
        "shard",
        "is_critical",
        "is_witness",
        "contract_sha256",
    }
    _replay_exact_fields(raw, expected, label="scenario")
    return AbsoluteGeneralizationScenario(
        cell_id=_text(raw["cell_id"], label="scenario cell"),
        removed_symbol=_text(raw["removed_symbol"], label="scenario removed symbol"),
        window_start=_iso_date(raw["window_start"], label="scenario window start"),
        window_end=_iso_date(raw["window_end"], label="scenario window end"),
        shard=_text(raw["shard"], label="scenario shard"),
        is_critical=_boolean(raw["is_critical"], label="scenario critical flag"),
        is_witness=_boolean(raw["is_witness"], label="scenario witness flag"),
        contract_sha256=_text(raw["contract_sha256"], label="scenario contract"),
    )


def _roles_from_raw(value: object) -> AbsoluteGeneralizationReplayRoleSnapshot:
    raw = _mapping(value, label="roles")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayRoleSnapshot)}
    _replay_exact_fields(raw, expected, label="roles")
    return AbsoluteGeneralizationReplayRoleSnapshot(
        as_of=_text(raw["as_of"], label="role session"),
        tradable_symbols=_strings(raw["tradable_symbols"], label="tradable symbols"),
        qualification_reference_symbols=_strings(
            raw["qualification_reference_symbols"], label="qualification symbols"
        ),
        risk_reference_symbols=_strings(raw["risk_reference_symbols"], label="risk symbols"),
        available_symbols=_strings(raw["available_symbols"], label="available symbols"),
        unavailable_reference_symbols=_strings(
            raw["unavailable_reference_symbols"], label="unavailable symbols"
        ),
        point_in_time_industries=_string_pairs(
            raw["point_in_time_industries"], label="industry pairs"
        ),
        tradable_identity=_text(raw["tradable_identity"], label="tradable identity"),
        qualification_reference_identity=_text(
            raw["qualification_reference_identity"], label="qualification identity"
        ),
        risk_reference_identity=_text(raw["risk_reference_identity"], label="risk identity"),
        point_in_time_industry_identity=_text(
            raw["point_in_time_industry_identity"], label="industry identity"
        ),
    )


def _manifest_from_raw(value: object) -> AbsoluteGeneralizationReplayManifestSnapshot:
    raw = _mapping(value, label="data manifest")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayManifestSnapshot)}
    _replay_exact_fields(raw, expected, label="data manifest")
    return AbsoluteGeneralizationReplayManifestSnapshot(
        generated_at=_text(raw["generated_at"], label="manifest generated at"),
        source=_text(raw["source"], label="manifest source"),
        adjustment=_text(raw["adjustment"], label="manifest adjustment"),
        files=_string_pairs(raw["files"], label="manifest files"),
        symbols=_strings(raw["symbols"], label="manifest symbols"),
        start=_text(raw["start"], label="manifest start"),
        end=_text(raw["end"], label="manifest end"),
        digest=_text(raw["digest"], label="manifest digest"),
    )


def _account_snapshot_from_raw(value: object, *, label: str) -> AbsoluteGeneralizationReplayAccountSnapshot:
    raw = _mapping(value, label=label)
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayAccountSnapshot)}
    _replay_exact_fields(raw, expected, label=label)
    return AbsoluteGeneralizationReplayAccountSnapshot(
        account_payload=_payload_from_raw(raw["account_payload"], label=f"{label} account"),
        changed_order_payloads=tuple(
            _payload_from_raw(item, label=f"{label} changed order")
            for item in _sequence(raw["changed_order_payloads"], label=f"{label} changed orders")
        ),
        changed_epoch_payloads=tuple(
            _payload_from_raw(item, label=f"{label} changed epoch")
            for item in _sequence(raw["changed_epoch_payloads"], label=f"{label} changed epochs")
        ),
        removed_order_keys=_strings(raw["removed_order_keys"], label=f"{label} removed orders"),
        removed_epoch_keys=_strings(raw["removed_epoch_keys"], label=f"{label} removed epochs"),
        order_ledger_chain_sha256=_text(raw["order_ledger_chain_sha256"], label=f"{label} order chain"),
        epoch_ledger_chain_sha256=_text(raw["epoch_ledger_chain_sha256"], label=f"{label} epoch chain"),
    )


def _observation_from_raw(value: object) -> AbsoluteGeneralizationReplayObservation:
    raw = _mapping(value, label="observation")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayObservation)}
    _replay_exact_fields(raw, expected, label="observation")
    return AbsoluteGeneralizationReplayObservation(
        session=_text(raw["session"], label="observation session"),
        equity=_number(raw["equity"], label="observation equity"),
        closing_marks=_number_pairs(raw["closing_marks"], label="closing marks"),
        decision_payload=_payload_from_raw(raw["decision_payload"], label="decision payload"),
        new_fills=tuple(
            _payload_from_raw(item, label="new fill")
            for item in _sequence(raw["new_fills"], label="new fills")
        ),
        post_open_account=_account_snapshot_from_raw(
            raw["post_open_account"], label="post-open snapshot"
        ),
        post_decision_account=_account_snapshot_from_raw(
            raw["post_decision_account"], label="post-decision snapshot"
        ),
        roles=_roles_from_raw(raw["roles"]),
        intentional_role_absent_symbols=_strings(
            raw["intentional_role_absent_symbols"], label="intentional role absences"
        ),
        expected_but_unavailable_symbols=_strings(
            raw["expected_but_unavailable_symbols"], label="expected unavailable symbols"
        ),
        replay_universe_identity=_text(
            raw["replay_universe_identity"], label="replay universe identity"
        ),
        data_manifest=_manifest_from_raw(raw["data_manifest"]),
        loaded_symbols=_strings(raw["loaded_symbols"], label="loaded symbols"),
    )


def replay_from_raw(value: object) -> AbsoluteGeneralizationReplay:
    """Strictly rebuild one immutable replay without deriving economic facts."""

    raw = _mapping(value, label="replay")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplay)}
    _replay_exact_fields(raw, expected, label="replay")
    return AbsoluteGeneralizationReplay(
        scenario=_scenario_from_raw(raw["scenario"]),
        status=_text(raw["status"], label="replay status"),
        replay_error=_text(raw["replay_error"], label="replay error", empty=True),
        initial_cash=_number(raw["initial_cash"], label="initial cash"),
        final_equity=_number(raw["final_equity"], label="final equity"),
        observations=tuple(
            _observation_from_raw(item)
            for item in _sequence(raw["observations"], label="observations")
        ),
        final_account_payload=_payload_from_raw(
            raw["final_account_payload"], label="final account"
        ),
    )
