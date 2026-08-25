"""Future-holdout checkpoint carrier, artifact binding, and continuity."""

# ruff: noqa: RUF022 - frozen compatibility export order

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from ..execution_journal import JournalCheckpoint
from .artifact_transaction import (
    read_protected_artifact as _read_protected_artifact,
)
from .artifact_transaction import (
    resolved_path_text as _resolved_path_text,
)
from .capabilities import holdout_runtime_capabilities
from .contract import FutureHoldoutContract
from .contract import (
    canonical_sha256 as _canonical_sha256,
)
from .contract import (
    read_json as _read_json,
)
from .contract import (
    session_dates as _session_dates,
)
from .replay import read_future_holdout_decision, read_future_holdout_replay
from .source_identity import holdout_source_sha256

_CHECKPOINT_FIELDS = frozenset(
    {
        "schema_version",
        "checkpoint_id",
        "contract_sha256",
        "production_source_sha256",
        "prior_close_account_sha256",
        "holdout_data_sha256",
        "sessions",
        "decision_digests",
        "replay_canonical_sha256",
        "replay_output_path",
        "replay_output_sha256",
        "decision_output_path",
        "decision_output_sha256",
        "journal_checkpoint",
        "canonical_sha256",
    }
)


def _checkpoint_payload(
    replay: Mapping[str, Any],
    *,
    replay_output_path: str | Path,
    replay_output_bytes: bytes,
    decision_output_path: str | Path,
    decision_output_bytes: bytes,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 2,
        "checkpoint_id": "phase2-future-holdout-daily-checkpoint-v2",
        "contract_sha256": replay.get("contract_sha256"),
        "production_source_sha256": replay.get("production_source_sha256"),
        "prior_close_account_sha256": replay.get("prior_close_account_sha256"),
        "holdout_data_sha256": replay.get("holdout_data_sha256"),
        "sessions": replay.get("sessions"),
        "decision_digests": replay.get("decision_digests"),
        "replay_canonical_sha256": replay.get("canonical_sha256"),
        "replay_output_path": _resolved_path_text(replay_output_path),
        "replay_output_sha256": hashlib.sha256(replay_output_bytes).hexdigest(),
        "decision_output_path": _resolved_path_text(decision_output_path),
        "decision_output_sha256": hashlib.sha256(decision_output_bytes).hexdigest(),
        "journal_checkpoint": replay.get("journal_checkpoint"),
    }
    payload["canonical_sha256"] = _canonical_sha256(payload)
    return payload


def _checkpoint_identity_is_valid(
    raw: Mapping[str, Any],
    *,
    contract: FutureHoldoutContract,
) -> bool:
    seal = raw.get("canonical_sha256")
    unsealed = {key: value for key, value in raw.items() if key != "canonical_sha256"}
    capabilities = holdout_runtime_capabilities()
    source_identity = (
        holdout_source_sha256
        if capabilities is None
        else capabilities.holdout_source_sha256
    )
    return bool(
        set(raw) == _CHECKPOINT_FIELDS
        and isinstance(seal, str)
        and seal == _canonical_sha256(unsealed)
        and raw.get("schema_version") == 2
        and raw.get("checkpoint_id") == "phase2-future-holdout-daily-checkpoint-v2"
        and raw.get("contract_sha256") == contract.sha256
        and raw.get("production_source_sha256")
        == source_identity(Path(__file__).resolve().parents[3])
        and raw.get("prior_close_account_sha256") == contract.prior_close_account_sha256
        and isinstance(raw.get("holdout_data_sha256"), str)
        and len(cast(str, raw["holdout_data_sha256"])) == 64
        and isinstance(raw.get("replay_canonical_sha256"), str)
        and len(cast(str, raw["replay_canonical_sha256"])) == 64
    )


def _checkpoint_output_bindings_are_valid(raw: Mapping[str, Any]) -> bool:
    return bool(
        isinstance(raw.get("replay_output_path"), str)
        and Path(cast(str, raw["replay_output_path"])).is_absolute()
        and isinstance(raw.get("replay_output_sha256"), str)
        and len(cast(str, raw["replay_output_sha256"])) == 64
        and isinstance(raw.get("decision_output_path"), str)
        and Path(cast(str, raw["decision_output_path"])).is_absolute()
        and isinstance(raw.get("decision_output_sha256"), str)
        and len(cast(str, raw["decision_output_sha256"])) == 64
    )


def _validate_checkpoint_history(
    raw: Mapping[str, Any],
    *,
    contract: FutureHoldoutContract,
) -> None:
    sessions = raw.get("sessions")
    digests = raw.get("decision_digests")
    if (
        not isinstance(sessions, list)
        or not sessions
        or any(not isinstance(value, str) for value in sessions)
        or _session_dates(sessions, contract=contract) != tuple(sessions)
        or not isinstance(digests, list)
        or len(digests) != len(sessions)
        or any(not isinstance(value, str) or len(value) != 64 for value in digests)
    ):
        raise ValueError("future holdout journal checkpoint history is malformed")


def _read_checkpoint_carrier(
    path: str | Path,
    *,
    contract: FutureHoldoutContract,
) -> tuple[dict[str, Any], JournalCheckpoint] | None:
    source = Path(path)
    if not source.exists():
        return None
    raw = _read_json(source, label="future holdout journal checkpoint")
    if not _checkpoint_identity_is_valid(raw, contract=contract) or not _checkpoint_output_bindings_are_valid(
        raw
    ):
        raise ValueError("future holdout journal checkpoint carrier is invalid")
    _validate_checkpoint_history(raw, contract=contract)
    checkpoint = raw.get("journal_checkpoint")
    if not isinstance(checkpoint, Mapping):
        raise ValueError("future holdout journal checkpoint is malformed")
    try:
        trusted = JournalCheckpoint(**dict(checkpoint))
    except (TypeError, ValueError) as exc:
        raise ValueError("future holdout journal checkpoint is malformed") from exc
    return raw, trusted


def _verify_checkpoint_artifacts(
    checkpoint: Mapping[str, Any],
    *,
    contract: FutureHoldoutContract,
) -> dict[str, Any]:
    replay_path = cast(str, checkpoint["replay_output_path"])
    try:
        before = _read_protected_artifact(
            replay_path,
            label="prior deterministic replay artifact",
        )
        if hashlib.sha256(before).hexdigest() != checkpoint["replay_output_sha256"]:
            raise ValueError("prior deterministic replay artifact hash changed")
        replay = read_future_holdout_replay(
            replay_path,
            contract=contract,
            sessions=cast(Sequence[str], checkpoint["sessions"]),
            holdout_data_sha256=cast(str, checkpoint["holdout_data_sha256"]),
        )
        after = _read_protected_artifact(
            replay_path,
            label="prior deterministic replay artifact",
        )
        if before != after:
            raise ValueError("prior deterministic replay artifact changed during readback")
        if (
            replay["canonical_sha256"] != checkpoint["replay_canonical_sha256"]
            or replay["decision_digests"] != checkpoint["decision_digests"]
            or replay["journal_checkpoint"] != checkpoint["journal_checkpoint"]
        ):
            raise ValueError("prior deterministic replay artifact checkpoint is stale")
    except (OSError, ValueError) as exc:
        raise ValueError("prior deterministic replay artifact is missing or changed") from exc

    decision_path = cast(str, checkpoint["decision_output_path"])
    try:
        before = _read_protected_artifact(
            decision_path,
            label="prior daily decision artifact",
        )
        if hashlib.sha256(before).hexdigest() != checkpoint["decision_output_sha256"]:
            raise ValueError("prior daily decision artifact hash changed")
        read_future_holdout_decision(decision_path, replay=replay)
        after = _read_protected_artifact(
            decision_path,
            label="prior daily decision artifact",
        )
        if before != after:
            raise ValueError("prior daily decision artifact changed during readback")
    except (OSError, ValueError) as exc:
        raise ValueError("prior daily decision artifact is missing or changed") from exc
    return replay


def _validate_daily_replay_continuity(
    replay: Mapping[str, Any],
    *,
    prior_checkpoint: Mapping[str, Any] | None,
    contract: FutureHoldoutContract,
) -> None:
    sessions = replay.get("sessions")
    digests = replay.get("decision_digests")
    if (
        not isinstance(sessions, list)
        or not sessions
        or any(not isinstance(value, str) for value in sessions)
        or _session_dates(sessions, contract=contract) != tuple(sessions)
        or not isinstance(digests, list)
        or len(digests) != len(sessions)
    ):
        raise ValueError("future holdout replay daily history is malformed")
    if prior_checkpoint is None:
        if len(sessions) != 1:
            raise ValueError("future holdout replay requires exactly one uncheckpointed daily session")
        return

    prior_sessions = cast(list[str], prior_checkpoint["sessions"])
    prior_digests = cast(list[str], prior_checkpoint["decision_digests"])
    if sessions[: len(prior_sessions)] != prior_sessions:
        raise ValueError("future holdout replay changed the checkpointed session prefix")
    if len(sessions) not in {len(prior_sessions), len(prior_sessions) + 1}:
        raise ValueError("future holdout replay requires exactly one uncheckpointed daily session")
    if digests[: len(prior_digests)] != prior_digests:
        raise ValueError("future holdout replay changed a checkpointed daily decision")
    if (
        len(sessions) == len(prior_sessions)
        and replay.get("holdout_data_sha256") != prior_checkpoint["holdout_data_sha256"]
    ):
        raise ValueError("future holdout replay changed the checkpointed data prefix")


CHECKPOINT_FIELDS = _CHECKPOINT_FIELDS
checkpoint_payload = _checkpoint_payload
read_checkpoint_carrier = _read_checkpoint_carrier
validate_daily_replay_continuity = _validate_daily_replay_continuity
verify_checkpoint_artifacts = _verify_checkpoint_artifacts


__all__ = (
    "_CHECKPOINT_FIELDS",
    "_checkpoint_payload",
    "_read_checkpoint_carrier",
    "_verify_checkpoint_artifacts",
    "_validate_daily_replay_continuity",
)
