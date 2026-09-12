"""Strict JSON codec for the immutable absolute-generalization replay authority."""

from __future__ import annotations

import gzip
import hashlib
import math
import shutil
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import cast

from uquant.config import config_fingerprint
from uquant.contracts.runtime_identity import runtime_environment_provenance
from uquant.contracts.strict_json import canonical_json_bytes, canonical_json_sha256, strict_json_loads
from uquant.infrastructure.atomic_files import (
    atomic_write_bytes,
    validate_atomic_output_boundary,
    validate_atomic_output_path,
)
from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.provenance.surfaces import load_source_surface_registry
from uquant.validation.manifest import verify_data_manifest

from .contract import load_absolute_generalization_contract
from .replay import (
    AbsoluteGeneralizationReplay,
    AbsoluteGeneralizationReplayAccountSnapshot,
    AbsoluteGeneralizationReplayManifestSnapshot,
    AbsoluteGeneralizationReplayObservation,
    AbsoluteGeneralizationReplayPayload,
    AbsoluteGeneralizationReplayRoleSnapshot,
    run_absolute_generalization_replay,
)
from .scenarios import AbsoluteGeneralizationScenario


def _replay_mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return cast(Mapping[str, object], value)


def _replay_sequence(value: object, *, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"absolute generalization replay evidence {label} is malformed")
    return cast(Sequence[object], value)


def _replay_exact_fields(raw: Mapping[str, object], expected: set[str], *, label: str) -> None:
    if set(raw) != expected:
        raise ValueError(f"absolute generalization replay evidence {label} fields differ")


def _replay_text(value: object, *, label: str, empty: bool = False) -> str:
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
    text = _replay_text(value, label=label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"absolute generalization replay evidence {label} is malformed") from exc


def _strings(value: object, *, label: str) -> tuple[str, ...]:
    return tuple(_replay_text(item, label=label) for item in _replay_sequence(value, label=label))


def _string_pairs(value: object, *, label: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in _replay_sequence(value, label=label):
        pair = _replay_sequence(item, label=label)
        if len(pair) != 2:
            raise ValueError(f"absolute generalization replay evidence {label} is malformed")
        result.append(
            (
                _replay_text(pair[0], label=label),
                _replay_text(pair[1], label=label),
            )
        )
    return tuple(result)


def _number_pairs(value: object, *, label: str) -> tuple[tuple[str, float], ...]:
    result: list[tuple[str, float]] = []
    for item in _replay_sequence(value, label=label):
        pair = _replay_sequence(item, label=label)
        if len(pair) != 2:
            raise ValueError(f"absolute generalization replay evidence {label} is malformed")
        result.append((_replay_text(pair[0], label=label), _number(pair[1], label=label)))
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


def _replay_dataclass_values(value: object) -> dict[str, object]:
    """Project the finite replay schema without reflection or object deepcopy."""
    if type(value) is AbsoluteGeneralizationReplay:
        return {
            "scenario": value.scenario,
            "status": value.status,
            "replay_error": value.replay_error,
            "initial_cash": value.initial_cash,
            "final_equity": value.final_equity,
            "observations": value.observations,
            "final_account_payload": value.final_account_payload,
        }
    if type(value) is AbsoluteGeneralizationReplayAccountSnapshot:
        return {
            "account_payload": value.account_payload,
            "changed_order_payloads": value.changed_order_payloads,
            "changed_epoch_payloads": value.changed_epoch_payloads,
            "removed_order_keys": value.removed_order_keys,
            "removed_epoch_keys": value.removed_epoch_keys,
            "order_ledger_chain_sha256": value.order_ledger_chain_sha256,
            "epoch_ledger_chain_sha256": value.epoch_ledger_chain_sha256,
        }
    if type(value) is AbsoluteGeneralizationReplayManifestSnapshot:
        return {
            "generated_at": value.generated_at,
            "source": value.source,
            "adjustment": value.adjustment,
            "files": value.files,
            "symbols": value.symbols,
            "start": value.start,
            "end": value.end,
            "digest": value.digest,
        }
    if type(value) is AbsoluteGeneralizationReplayObservation:
        return {
            "session": value.session,
            "equity": value.equity,
            "closing_marks": value.closing_marks,
            "decision_payload": value.decision_payload,
            "new_fills": value.new_fills,
            "post_open_account": value.post_open_account,
            "post_decision_account": value.post_decision_account,
            "roles": value.roles,
            "intentional_role_absent_symbols": value.intentional_role_absent_symbols,
            "expected_but_unavailable_symbols": value.expected_but_unavailable_symbols,
            "replay_universe_identity": value.replay_universe_identity,
            "data_manifest": value.data_manifest,
            "loaded_symbols": value.loaded_symbols,
            "decision_runtime_payload": value.decision_runtime_payload,
        }
    if type(value) is AbsoluteGeneralizationReplayRoleSnapshot:
        return {
            "as_of": value.as_of,
            "tradable_symbols": value.tradable_symbols,
            "qualification_reference_symbols": value.qualification_reference_symbols,
            "risk_reference_symbols": value.risk_reference_symbols,
            "available_symbols": value.available_symbols,
            "unavailable_reference_symbols": value.unavailable_reference_symbols,
            "point_in_time_industries": value.point_in_time_industries,
            "tradable_identity": value.tradable_identity,
            "qualification_reference_identity": value.qualification_reference_identity,
            "risk_reference_identity": value.risk_reference_identity,
            "point_in_time_industry_identity": value.point_in_time_industry_identity,
        }
    if type(value) is AbsoluteGeneralizationScenario:
        return {
            "cell_id": value.cell_id,
            "removed_symbol": value.removed_symbol,
            "window_start": value.window_start,
            "window_end": value.window_end,
            "shard": value.shard,
            "is_critical": value.is_critical,
            "is_witness": value.is_witness,
            "contract_sha256": value.contract_sha256,
        }
    raise ValueError("absolute generalization replay evidence dataclass type differs")

def _replay_json_value(value: object) -> object:
    if type(value) is AbsoluteGeneralizationReplayPayload:
        return _payload_to_raw(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _replay_json_value(_replay_dataclass_values(value))
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
    return dict(_replay_mapping(raw, label="replay"))


def raw_replay_identity(scenario: AbsoluteGeneralizationScenario, root: Path, data: Path) -> dict[str, object]:
    if root.resolve() != Path(__file__).resolve().parents[3]:
        raise ValueError("raw replay root differs from the executing checkout")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve raw replay checkout")
    objects = subprocess.run(
        [git, "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()  # nosec B603
    if len(objects) != 2:
        raise ValueError("raw replay checkout identity differs")
    contract = load_absolute_generalization_contract()
    return {
        "schema_version": 1, "scenario": _replay_json_value(scenario),
        "head": objects[0], "tree": objects[1], "contract": contract.canonical_sha256,
        "config": config_fingerprint(), "data": verify_data_manifest(data),
        "runtime": runtime_environment_provenance(root),
        "registry": load_source_surface_registry(root).canonical_sha256,
        "surfaces": {name: source_surface_fingerprint(root, name) for name in
                     ("economic_decision_v1", "full_package_v1", "validation_runner_v1")},
    }


def read_cached_replay(path: Path, identity: Mapping[str, object]) -> AbsoluteGeneralizationReplay:
    validate_atomic_output_path(path)
    raw = _replay_mapping(strict_json_loads(gzip.decompress(path.read_bytes())), label="raw cache")
    _replay_exact_fields(raw, {"identity", "replay", "sha256"}, label="raw cache")
    payload = {"identity": raw["identity"], "replay": raw["replay"]}
    if raw["identity"] != identity or raw["sha256"] != canonical_json_sha256(payload):
        raise ValueError("raw replay cache identity or digest differs")
    replay = replay_from_raw(raw["replay"])
    if _replay_json_value(replay.scenario) != identity["scenario"]:
        raise ValueError("raw replay cached scenario differs")
    return replay


def persist_raw_replay(
    path: Path, replay: AbsoluteGeneralizationReplay, identity: Mapping[str, object],
) -> AbsoluteGeneralizationReplay:
    """Publish a sealed native result once and require strict readback."""
    validate_atomic_output_path(path)
    payload: dict[str, object] = {"identity": identity, "replay": replay_to_raw(replay)}
    envelope = {**payload, "sha256": canonical_json_sha256(payload)}
    if not path.exists():
        atomic_write_bytes(path, gzip.compress(canonical_json_bytes(envelope), compresslevel=3, mtime=0))
    saved = read_cached_replay(path, identity)
    if replay_to_raw(saved) != payload["replay"]:
        raise ValueError("existing raw replay differs; refusing to overwrite")
    return saved


def cached_removal_replay(
    scenario: AbsoluteGeneralizationScenario, *, root: str | Path,
    data_dir: str | Path, cache_dir: str | Path,
) -> AbsoluteGeneralizationReplay:
    """Persist each native result before its reader; one scheduled writer per cell."""
    physical_data = Path(data_dir).absolute()
    if any(part.is_symlink() for part in (physical_data, *physical_data.parents)):
        raise ValueError("raw replay data path is unsafe")
    repository, data = Path(root).resolve(), physical_data.resolve()
    identity = raw_replay_identity(scenario, repository, data)
    path = Path(cache_dir) / "raw" / f"{canonical_json_sha256(identity)}.json.gz"
    validate_atomic_output_boundary(path, protected_roots=(data,))
    if path.exists():
        return read_cached_replay(path, identity)
    replay = run_absolute_generalization_replay(
        scenario, root=repository, data_dir=data, cache_dir=cache_dir,
    )
    if identity != raw_replay_identity(scenario, repository, data):
        raise ValueError("raw replay inputs changed during execution")
    return persist_raw_replay(path, replay, identity)


def _payload_from_raw(value: object, *, label: str) -> AbsoluteGeneralizationReplayPayload:
    raw = _replay_mapping(value, label=label)
    _replay_exact_fields(raw, {"sha256", "value"}, label=label)
    digest = _replay_text(raw["sha256"], label=f"{label} digest")
    encoded = canonical_json_bytes(raw["value"])
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError(f"absolute generalization replay evidence {label} digest differs")
    return AbsoluteGeneralizationReplayPayload(canonical_json=encoded, sha256=digest)


def _scenario_from_raw(value: object) -> AbsoluteGeneralizationScenario:
    raw = _replay_mapping(value, label="scenario")
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
        cell_id=_replay_text(raw["cell_id"], label="scenario cell"),
        removed_symbol=_replay_text(raw["removed_symbol"], label="scenario removed symbol"),
        window_start=_iso_date(raw["window_start"], label="scenario window start"),
        window_end=_iso_date(raw["window_end"], label="scenario window end"),
        shard=_replay_text(raw["shard"], label="scenario shard"),
        is_critical=_boolean(raw["is_critical"], label="scenario critical flag"),
        is_witness=_boolean(raw["is_witness"], label="scenario witness flag"),
        contract_sha256=_replay_text(raw["contract_sha256"], label="scenario contract"),
    )


def _replay_roles_from_raw(value: object) -> AbsoluteGeneralizationReplayRoleSnapshot:
    raw = _replay_mapping(value, label="roles")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayRoleSnapshot)}
    _replay_exact_fields(raw, expected, label="roles")
    return AbsoluteGeneralizationReplayRoleSnapshot(
        as_of=_replay_text(raw["as_of"], label="role session"),
        tradable_symbols=_strings(raw["tradable_symbols"], label="tradable symbols"),
        qualification_reference_symbols=_strings(
            raw["qualification_reference_symbols"], label="qualification symbols"
        ),
        risk_reference_symbols=_strings(raw["risk_reference_symbols"], label="risk symbols"),
        available_symbols=_strings(raw["available_symbols"], label="available symbols"),
        unavailable_reference_symbols=_strings(
            raw["unavailable_reference_symbols"], label="unavailable symbols"
        ),
        point_in_time_industries=_string_pairs(raw["point_in_time_industries"], label="industry pairs"),
        tradable_identity=_replay_text(raw["tradable_identity"], label="tradable identity"),
        qualification_reference_identity=_replay_text(
            raw["qualification_reference_identity"], label="qualification identity"
        ),
        risk_reference_identity=_replay_text(raw["risk_reference_identity"], label="risk identity"),
        point_in_time_industry_identity=_replay_text(
            raw["point_in_time_industry_identity"], label="industry identity"
        ),
    )


def _manifest_from_raw(value: object) -> AbsoluteGeneralizationReplayManifestSnapshot:
    raw = _replay_mapping(value, label="data manifest")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayManifestSnapshot)}
    _replay_exact_fields(raw, expected, label="data manifest")
    return AbsoluteGeneralizationReplayManifestSnapshot(
        generated_at=_replay_text(raw["generated_at"], label="manifest generated at"),
        source=_replay_text(raw["source"], label="manifest source"),
        adjustment=_replay_text(raw["adjustment"], label="manifest adjustment"),
        files=_string_pairs(raw["files"], label="manifest files"),
        symbols=_strings(raw["symbols"], label="manifest symbols"),
        start=_replay_text(raw["start"], label="manifest start"),
        end=_replay_text(raw["end"], label="manifest end"),
        digest=_replay_text(raw["digest"], label="manifest digest"),
    )


def _account_snapshot_from_raw(value: object, *, label: str) -> AbsoluteGeneralizationReplayAccountSnapshot:
    raw = _replay_mapping(value, label=label)
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayAccountSnapshot)}
    _replay_exact_fields(raw, expected, label=label)
    return AbsoluteGeneralizationReplayAccountSnapshot(
        account_payload=_payload_from_raw(raw["account_payload"], label=f"{label} account"),
        changed_order_payloads=tuple(
            _payload_from_raw(item, label=f"{label} changed order")
            for item in _replay_sequence(raw["changed_order_payloads"], label=f"{label} changed orders")
        ),
        changed_epoch_payloads=tuple(
            _payload_from_raw(item, label=f"{label} changed epoch")
            for item in _replay_sequence(raw["changed_epoch_payloads"], label=f"{label} changed epochs")
        ),
        removed_order_keys=_strings(raw["removed_order_keys"], label=f"{label} removed orders"),
        removed_epoch_keys=_strings(raw["removed_epoch_keys"], label=f"{label} removed epochs"),
        order_ledger_chain_sha256=_replay_text(
            raw["order_ledger_chain_sha256"], label=f"{label} order chain"
        ),
        epoch_ledger_chain_sha256=_replay_text(
            raw["epoch_ledger_chain_sha256"], label=f"{label} epoch chain"
        ),
    )


def _observation_from_raw(value: object) -> AbsoluteGeneralizationReplayObservation:
    raw = _replay_mapping(value, label="observation")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplayObservation)}
    _replay_exact_fields(raw, expected, label="observation")
    return AbsoluteGeneralizationReplayObservation(
        session=_replay_text(raw["session"], label="observation session"),
        equity=_number(raw["equity"], label="observation equity"),
        closing_marks=_number_pairs(raw["closing_marks"], label="closing marks"),
        decision_payload=_payload_from_raw(raw["decision_payload"], label="decision payload"),
        new_fills=tuple(
            _payload_from_raw(item, label="new fill")
            for item in _replay_sequence(raw["new_fills"], label="new fills")
        ),
        post_open_account=_account_snapshot_from_raw(raw["post_open_account"], label="post-open snapshot"),
        post_decision_account=_account_snapshot_from_raw(
            raw["post_decision_account"], label="post-decision snapshot"
        ),
        roles=_replay_roles_from_raw(raw["roles"]),
        intentional_role_absent_symbols=_strings(
            raw["intentional_role_absent_symbols"], label="intentional role absences"
        ),
        expected_but_unavailable_symbols=_strings(
            raw["expected_but_unavailable_symbols"], label="expected unavailable symbols"
        ),
        replay_universe_identity=_replay_text(
            raw["replay_universe_identity"], label="replay universe identity"
        ),
        data_manifest=_manifest_from_raw(raw["data_manifest"]),
        loaded_symbols=_strings(raw["loaded_symbols"], label="loaded symbols"),
        decision_runtime_payload=(
            None
            if raw["decision_runtime_payload"] is None
            else _payload_from_raw(raw["decision_runtime_payload"], label="decision runtime payload")
        ),
    )


def replay_from_raw(value: object) -> AbsoluteGeneralizationReplay:
    """Strictly rebuild one immutable replay without deriving economic facts."""

    raw = _replay_mapping(value, label="replay")
    expected = {field.name for field in fields(AbsoluteGeneralizationReplay)}
    _replay_exact_fields(raw, expected, label="replay")
    return AbsoluteGeneralizationReplay(
        scenario=_scenario_from_raw(raw["scenario"]),
        status=_replay_text(raw["status"], label="replay status"),
        replay_error=_replay_text(raw["replay_error"], label="replay error", empty=True),
        initial_cash=_number(raw["initial_cash"], label="initial cash"),
        final_equity=_number(raw["final_equity"], label="final equity"),
        observations=tuple(
            _observation_from_raw(item)
            for item in _replay_sequence(raw["observations"], label="observations")
        ),
        final_account_payload=_payload_from_raw(raw["final_account_payload"], label="final account"),
    )
