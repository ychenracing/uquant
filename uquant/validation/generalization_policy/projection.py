"""Compile-anchored champion baseline and frozen AI-era gate policy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ...config_governance import (
    GovernedConfigMigration,
)
from .schema import (
    _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS,
    _DEPRECATED_V1_ATTRIBUTION_TOKEN,
    _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256,
    _artifact_equality_sha256,
    _hash_json,
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


def _project_raw_evidence_for_frozen_v1(
    raw: Mapping[str, Any],
    *,
    source_schema: int,
    frozen_v1_attribution_verified: bool = False,
    config_migration: GovernedConfigMigration | None = None,
) -> dict[str, Any]:
    """Apply the same closed raw-evidence migration used by exact equality."""

    try:
        projected = json.loads(
            json.dumps(raw, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("generalization raw evidence is not finite canonical JSON") from exc
    if not isinstance(projected, dict) or source_schema not in {1, 2}:
        raise ValueError("generalization raw evidence schema is malformed")
    if source_schema == 1:
        if not frozen_v1_attribution_verified or not isinstance(
            projected.get("attribution"), dict
        ):
            raise ValueError(
                "deprecated v1 attribution lacks its compiled collection validation"
            )
    elif "attribution" in projected or "legacy_attribution" in projected:
        raise ValueError("candidate v2 raw evidence injects deprecated v1 attribution")
    if config_migration is not None:
        if source_schema != 2 or (
            projected.get("effective_config_sha256")
            != config_migration.candidate_config_sha256
        ):
            raise ValueError("raw evidence differs from governed config migration carrier")
        projected["effective_config_sha256"] = config_migration.champion_config_sha256
    projected["attribution"] = dict(_DEPRECATED_V1_ATTRIBUTION_TOKEN)
    legacy_decision_digests = projected.pop("legacy_decision_digests", None)
    if legacy_decision_digests is not None:
        projected["decision_digests"] = legacy_decision_digests
    projected.pop("decision_trace", None)
    projected.pop("daily_replay_evidence", None)
    account = projected.get("final_account")
    if not isinstance(account, dict):
        return projected
    # The evaluator verifies both values against the current compiled
    # schema/source before this cross-version projection.  Fixed tokens let
    # immutable v1 and current v2 bindings compare without pretending their
    # schema versions and source hashes are equal.
    account["schema_version"] = "VALIDATED_ACCOUNT_SCHEMA_BINDING"
    account["code_hash"] = "VALIDATED_PRODUCTION_SOURCE_BINDING"
    for collection_name in ("pending_orders", "order_ledger", "fills"):
        collection = account.get(collection_name)
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                record.pop(name, None)
            sold_tranches = record.get("sold_tranches")
            if isinstance(sold_tranches, list):
                for sold_lot in sold_tranches:
                    if not isinstance(sold_lot, dict):
                        continue
                    for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                        sold_lot.pop(name, None)
    positions = account.get("positions")
    if isinstance(positions, dict):
        for position in positions.values():
            if not isinstance(position, dict):
                continue
            tranches = position.get("tranches")
            if not isinstance(tranches, list):
                continue
            for tranche in tranches:
                if not isinstance(tranche, dict):
                    continue
                for name in _ADDITIVE_ATTRIBUTION_IDENTITY_FIELDS:
                    tranche.pop(name, None)
    return projected


def _v2_economic_projection(
    artifact: Mapping[str, Any],
    *,
    config_migration: GovernedConfigMigration | None = None,
) -> dict[str, Any]:
    """Project validated v2 additions while retaining the frozen v1 control plane."""

    try:
        projected = json.loads(
            json.dumps(artifact, allow_nan=False, separators=(",", ":"), sort_keys=True)
        )
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
            provenance.get("effective_config_sha256")
            != config_migration.candidate_config_sha256
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
                raise ValueError(
                    "deprecated v1 attribution payload collection is malformed"
                )
            frozen_v1_attribution[identifier] = legacy_attribution
    if source_schema == 1 and _hash_json(frozen_v1_attribution) != (
        _REQUIRED_DEPRECATED_V1_ATTRIBUTION_COLLECTION_SHA256
    ):
        raise ValueError(
            "deprecated v1 attribution differs from the compiled frozen collection"
        )
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
