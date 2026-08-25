"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...config_governance import (
    GovernedConfigMigration,
)
from .schema import (
    ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS as _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS,
)
from .schema import (
    DEPRECATED_V1_ATTRIBUTION_TOKEN as _DEPRECATED_V1_ATTRIBUTION_TOKEN,
)
from .schema import (
    REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256 as _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256,
)
from .schema import (
    artifact_equality_sha256 as _artifact_equality_sha256,
)
from .schema import (
    hash_json as _hash_json,
)


def _candidate_contract_sha256(cell: Mapping[str, Any]) -> str:
    return _hash_json(
        {
            key: value
            for key, value in cell.items()
            if key
            not in {
                "raw",
                "metrics",
                "replay_error",
                "attribution_status",
                "attribution",
                "concentration",
            }
        }
    )


def _canonical_projected_raw(raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        projected = json.loads(json.dumps(raw, allow_nan=False, separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("generalization raw evidence is not finite canonical JSON") from exc
    if not isinstance(projected, dict):
        raise ValueError("generalization raw evidence schema is malformed")
    return projected


def _apply_raw_config_migration(
    projected: dict[str, Any],
    *,
    source_schema: int,
    config_migration: GovernedConfigMigration | None,
) -> None:
    if config_migration is None:
        return
    if source_schema != 2 or (
        projected.get("effective_config_sha256") != config_migration.candidate_config_sha256
    ):
        raise ValueError("raw evidence differs from governed config migration carrier")
    projected["effective_config_sha256"] = config_migration.champion_config_sha256


def _strip_additive_identity(record: dict[str, Any]) -> None:
    for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
        record.pop(name, None)
    sold_tranches = record.get("sold_tranches")
    if not isinstance(sold_tranches, list):
        return
    for sold_lot in sold_tranches:
        if isinstance(sold_lot, dict):
            for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                sold_lot.pop(name, None)


def _strip_account_record_identity(account: dict[str, Any]) -> None:
    for collection_name in ("pending_orders", "order_ledger", "fills"):
        collection = account.get(collection_name)
        if not isinstance(collection, list):
            continue
        for record in collection:
            if isinstance(record, dict):
                _strip_additive_identity(record)


def _strip_position_identity(account: dict[str, Any]) -> None:
    positions = account.get("positions")
    if not isinstance(positions, dict):
        return
    for position in positions.values():
        if not isinstance(position, dict):
            continue
        tranches = position.get("tranches")
        if not isinstance(tranches, list):
            continue
        for tranche in tranches:
            if isinstance(tranche, dict):
                for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                    tranche.pop(name, None)


def _project_raw_evidence_for_frozen_v1(
    raw: Mapping[str, Any],
    *,
    source_schema: int,
    frozen_v1_attribution_verified: bool = False,
    config_migration: GovernedConfigMigration | None = None,
) -> dict[str, Any]:
    """Apply the same closed raw-evidence migration used by exact equality."""
    projected = _canonical_projected_raw(raw)
    if source_schema not in {1, 2}:
        raise ValueError("generalization raw evidence schema is malformed")
    if source_schema == 1:
        if not frozen_v1_attribution_verified or not isinstance(projected.get("attribution"), dict):
            raise ValueError("deprecated v1 attribution lacks its compiled collection validation")
    elif "attribution" in projected or "legacy_attribution" in projected:
        raise ValueError("candidate v2 raw evidence injects deprecated v1 attribution")
    _apply_raw_config_migration(
        projected,
        source_schema=source_schema,
        config_migration=config_migration,
    )
    projected["attribution"] = dict(_DEPRECATED_V1_ATTRIBUTION_TOKEN)
    legacy_decision_digests = projected.pop("legacy_decision_digests", None)
    if legacy_decision_digests is not None:
        projected["decision_digests"] = legacy_decision_digests
    projected.pop("decision_trace", None)
    projected.pop("daily_replay_evidence", None)
    account = projected.get("final_account")
    if not isinstance(account, dict):
        return projected
    account["schema_version"] = "VALIDATED_ACCOUNT_SCHEMA_BINDING"
    account["code_hash"] = "VALIDATED_PRODUCTION_SOURCE_BINDING"
    _strip_account_record_identity(account)
    _strip_position_identity(account)
    return projected


def _v2_economic_projection(
    artifact: Mapping[str, Any],
    *,
    config_migration: GovernedConfigMigration | None = None,
) -> dict[str, Any]:
    """Project validated v2 additions while retaining the frozen v1 control plane."""

    try:
        projected = json.loads(json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("generalization candidate is not finite canonical JSON") from exc
    if not isinstance(projected, dict):
        raise ValueError("generalization candidate artifact is malformed")
    source_schema = projected.get("schema_version")
    if source_schema not in {1, 2}:
        raise ValueError("generalization candidate schema version is malformed")
    projected["schema_version"] = 1
    projected.pop("attribution_definition", None)
    if config_migration is not None:
        provenance = projected.get("provenance")
        if not isinstance(provenance, dict) or (
            provenance.get("effective_config_sha256") != config_migration.candidate_config_sha256
        ):
            raise ValueError("artifact differs from governed config migration carrier")
        provenance["effective_config_sha256"] = config_migration.champion_config_sha256
    cells = projected.get("cells")
    if not isinstance(cells, list):
        raise ValueError("generalization candidate cell collection is malformed")
    frozen_v1_attribution: dict[str, Any] = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("generalization candidate cell is malformed")
        raw = cell.get("raw")
        if isinstance(raw, dict) and source_schema == 1:
            identifier = f"{cell.get('window')}/{cell.get('scenario')}"
            legacy_attribution = raw.get("attribution")
            if not isinstance(legacy_attribution, dict) or identifier in frozen_v1_attribution:
                raise ValueError("deprecated v1 attribution payload collection is malformed")
            frozen_v1_attribution[identifier] = legacy_attribution
    if source_schema == 1 and _hash_json(frozen_v1_attribution) != (
        _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256
    ):
        raise ValueError("deprecated v1 attribution differs from the compiled frozen collection")
    for cell in cells:
        cell.pop("attribution_status", None)
        cell.pop("attribution", None)
        cell.pop("concentration", None)
        raw = cell.get("raw")
        if isinstance(raw, dict):
            cell["raw"] = _project_raw_evidence_for_frozen_v1(
                raw,
                source_schema=source_schema,
                frozen_v1_attribution_verified=source_schema == 1,
                config_migration=config_migration,
            )
    return projected


def _attribution_neutral_equality_sha256(
    artifact: Mapping[str, Any],
    *,
    config_migration: GovernedConfigMigration | None = None,
) -> str:
    projected = _v2_economic_projection(artifact, config_migration=config_migration)
    return _artifact_equality_sha256(projected)


attribution_neutral_equality_sha256 = _attribution_neutral_equality_sha256
candidate_contract_sha256 = _candidate_contract_sha256
project_raw_evidence_for_frozen_v1 = _project_raw_evidence_for_frozen_v1
v2_economic_projection = _v2_economic_projection
