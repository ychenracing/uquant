"""Freeze and compare the current supported path around breaking cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from uquant.account.codec import account_from_dict
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.contracts.strict_json import canonical_json_sha256, strict_json_loads
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.models.account import AccountState
from uquant.validation.manifest import verify_data_manifest
from uquant.validation.universe import default_ai_universe

_REFERENCE_COMMIT: Final = "9adccaccd7b9f03982181395630be175af9bfd70"
_REFERENCE_TREE: Final = "85f2cb6e2beadd6f6f684338cfdf27c1d9d75088"
_REFERENCE_PRODUCTION_SOURCE_SHA256: Final = (
    "e0331925a7d199d60464c69080ade0abf831e106dcb1bef0afd6b74baaccc10f"
)
_REFERENCE_CONFIG_SHA256: Final = (
    "dae4d79fdd813832c6ab152611437c13be1d38227c7280691874d3a9267d93d5"
)
_START: Final = "2023-01-03"
_END: Final = "2026-08-05"
_REFERENCE_PATH: Final = Path("benchmarks/pre_cleanup_current_behavior_reference.json")
_REPORT_PATH: Final = Path("benchmarks/post_generalization_trust_closure_checkpoint_c.json")
_REMOVED_CONFIG_FIELDS: Final = (
    "hierarchical_industry_shrinkage_enabled",
    "group_balanced_reference_enabled",
    "same_day_leader_pipeline_enabled",
    "evidence_family_voting_enabled",
)
_DROPPED_IDENTITY_FIELDS: Final = frozenset(
    {
        "account_identity",
        "account_migrations",
        "code_hash",
        "decision_digest",
        "effective_config_sha256",
    }
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity_category(key: str) -> str | None:
    singular = {
        "previous_epoch_id": "epoch",
        "replacement_event_id": "event",
    }
    if key in singular:
        return singular[key]
    if key.endswith("_id"):
        return key.removesuffix("_id")
    if key.endswith("_ids"):
        category = key.removesuffix("_ids")
        for prefix in ("submitted_", "acknowledged_", "cancelled_"):
            category = category.removeprefix(prefix)
        return category
    return None


def normalize_source_derived_identities(value: object) -> object:
    """Replace source-derived IDs by stable first-observation ordinals."""

    identities: dict[str, dict[str, str]] = {}

    def token(category: str, raw: object) -> object:
        if not isinstance(raw, str) or not raw:
            return raw
        values = identities.setdefault(category, {})
        if raw not in values:
            values[raw] = f"{category}:{len(values) + 1}"
        return values[raw]

    def visit(item: object, *, parent_key: str = "") -> object:
        if isinstance(item, Mapping):
            normalized: dict[str, object] = {}
            for key in sorted(item):
                if not isinstance(key, str):
                    raise TypeError("cleanup equivalence requires string mapping keys")
                if key in _DROPPED_IDENTITY_FIELDS:
                    continue
                child = item[key]
                category = _identity_category(key)
                if category is not None and isinstance(child, str):
                    normalized[key] = token(category, child)
                elif category is not None and isinstance(child, Sequence) and not isinstance(
                    child, (str, bytes, bytearray)
                ):
                    normalized[key] = [token(category, value) for value in child]
                else:
                    normalized[key] = visit(child, parent_key=key)
            return normalized
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            category = _identity_category(parent_key)
            if category is not None:
                return [token(category, child) for child in item]
            return [visit(child, parent_key=parent_key) for child in item]
        return item

    return visit(value)


def _row_digests(rows: object, *, label: str) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise RuntimeError(f"cleanup equivalence {label} rows are malformed")
    normalized = normalize_source_derived_identities(rows)
    if not isinstance(normalized, list):  # pragma: no cover - guarded above
        raise RuntimeError(f"cleanup equivalence {label} normalization failed")
    projected: list[dict[str, str]] = []
    for index, row in enumerate(normalized):
        if not isinstance(row, Mapping):
            raise RuntimeError(f"cleanup equivalence {label} row is malformed")
        date = row.get("date")
        projected.append(
            {
                "date": str(date) if date is not None else str(index),
                "sha256": canonical_json_sha256(row),
            }
        )
    return projected


def _codec_observation(account_raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        decoded = account_from_dict(dict(account_raw), require_hashes=False)
    except (RuntimeError, TypeError, ValueError) as exc:
        return {
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "status": "SUCCESS",
        "schema_version": decoded.schema_version,
        "roundtrip_economic_sha256": canonical_json_sha256(
            normalize_source_derived_identities(decoded.to_dict())
        ),
    }


def _semantic_config() -> dict[str, Any]:
    payload = DEFAULT_CONFIG.to_dict()
    for field in _REMOVED_CONFIG_FIELDS:
        payload.pop(field, None)
    return payload


def project_current_behavior(result: Mapping[str, Any]) -> dict[str, Any]:
    """Project exact current economics while alpha-renaming source-derived IDs."""

    account_raw = result.get("final_account")
    attribution = result.get("attribution")
    if not isinstance(account_raw, Mapping) or not isinstance(attribution, Mapping):
        raise RuntimeError("cleanup equivalence backtest result is malformed")
    ledger = attribution.get("daily_ledger")
    replay = result.get("daily_replay_evidence")
    decisions = result.get("decision_trace")
    normalized_account = normalize_source_derived_identities(account_raw)
    if not isinstance(normalized_account, Mapping):  # pragma: no cover - guarded above
        raise RuntimeError("cleanup equivalence account normalization failed")
    empty = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    empty_roundtrip = account_from_dict(empty.to_dict(), require_hashes=False)
    metrics = {
        name: result[name]
        for name in (
            "final_wealth",
            "final_equity",
            "max_drawdown",
            "account_orders",
            "gross_turnover",
            "annual_turnover",
            "pending_orders",
        )
    }
    fills = normalized_account.get("fills")
    orders = normalized_account.get("order_ledger")
    epochs = normalized_account.get("strategic_epochs")
    if not isinstance(fills, list) or not isinstance(orders, list) or not isinstance(epochs, list):
        raise RuntimeError("cleanup equivalence account collections are malformed")
    return {
        "window": {"start": str(result["start"]), "end": str(result["end"])},
        "metrics": metrics,
        "decision_sessions": _row_digests(decisions, label="decision"),
        "ledger_sessions": _row_digests(ledger, label="ledger"),
        "account_sessions": _row_digests(replay, label="account"),
        "orders": orders,
        "fills": fills,
        "strategic_epochs": epochs,
        "strategic_grant": normalized_account.get("strategic_grant"),
        "final_account_economic_sha256": canonical_json_sha256(normalized_account),
        "config_semantic_sha256": canonical_json_sha256(_semantic_config()),
        "empty_current_account_roundtrip_sha256": canonical_json_sha256(
            normalize_source_derived_identities(empty_roundtrip.to_dict())
        ),
        "full_account_codec_observation": _codec_observation(account_raw),
        "attribution_accounting": attribution.get("accounting"),
    }


def _sealed(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_sealed(path: Path) -> dict[str, Any]:
    raw = strict_json_loads(path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("cleanup equivalence reference is not an object")
    unsigned = dict(raw)
    claimed = unsigned.pop("canonical_sha256", None)
    if claimed != canonical_json_sha256(unsigned):
        raise ValueError("cleanup equivalence reference seal differs")
    return raw


def _run(data_dir: Path) -> tuple[tuple[str, ...], dict[str, Any]]:
    symbols = tuple(default_ai_universe().symbols)
    if len(symbols) != 34:
        raise RuntimeError("cleanup equivalence requires the current 34-name universe")
    result = ProductionEngine(data_dir, DEFAULT_CONFIG).backtest(
        symbols=symbols,
        start=_START,
        end=_END,
    )
    return symbols, project_current_behavior(result)


def capture_reference(
    *,
    data_dir: str | Path = "data/frozen",
    output: str | Path = _REFERENCE_PATH,
) -> dict[str, Any]:
    """Capture the exact pre-cleanup current supported path once."""

    if code_fingerprint() != _REFERENCE_PRODUCTION_SOURCE_SHA256:
        raise RuntimeError("pre-cleanup production source identity differs")
    if config_fingerprint(DEFAULT_CONFIG) != _REFERENCE_CONFIG_SHA256:
        raise RuntimeError("pre-cleanup config identity differs")
    data_path = Path(data_dir)
    symbols, projection = _run(data_path)
    config = DEFAULT_CONFIG.to_dict()
    removed = {field: config[field] for field in _REMOVED_CONFIG_FIELDS}
    if set(removed.values()) != {False}:
        raise RuntimeError("pre-cleanup compatibility config path is active")
    payload = {
        "schema_version": 1,
        "evidence_id": "pre-cleanup-current-behavior-reference",
        "scope": "CURRENT_SUPPORTED_PATH_PRE_CLEANUP_REFERENCE",
        "authoritative_acceptance": False,
        "reference_commit": _REFERENCE_COMMIT,
        "reference_tree": _REFERENCE_TREE,
        "runner_source_sha256": _sha256_file(Path(__file__)),
        "production_source_sha256": code_fingerprint(),
        "raw_config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "semantic_config_sha256": canonical_json_sha256(_semantic_config()),
        "compatibility_fields": removed,
        "data": verify_data_manifest(data_path),
        "symbols": list(symbols),
        "projection": projection,
    }
    sealed = _sealed(payload)
    _write_json(Path(output), sealed)
    return sealed


def compare_reference(
    *,
    data_dir: str | Path = "data/frozen",
    reference: str | Path = _REFERENCE_PATH,
    output: str | Path = _REPORT_PATH,
) -> dict[str, Any]:
    """Compare the candidate current path against the frozen pre-cleanup facts."""

    reference_payload = _read_sealed(Path(reference))
    if reference_payload.get("runner_source_sha256") != _sha256_file(Path(__file__)):
        raise RuntimeError("cleanup equivalence runner identity differs")
    data_path = Path(data_dir)
    if reference_payload.get("data") != verify_data_manifest(data_path):
        raise RuntimeError("cleanup equivalence data identity differs")
    symbols, candidate = _run(data_path)
    if reference_payload.get("symbols") != list(symbols):
        raise RuntimeError("cleanup equivalence universe differs")
    baseline = reference_payload.get("projection")
    if not isinstance(baseline, Mapping):
        raise RuntimeError("cleanup equivalence reference projection is malformed")
    dimensions = {
        key: baseline.get(key) == candidate.get(key)
        for key in (
            "window",
            "metrics",
            "decision_sessions",
            "ledger_sessions",
            "account_sessions",
            "orders",
            "fills",
            "strategic_epochs",
            "strategic_grant",
            "final_account_economic_sha256",
            "config_semantic_sha256",
            "empty_current_account_roundtrip_sha256",
            "attribution_accounting",
        )
    }
    payload = {
        "schema_version": 1,
        "evidence_id": "post-generalization-trust-closure-checkpoint-c",
        "scope": "CURRENT_SUPPORTED_PATH_BREAKING_CLEANUP_EQUIVALENCE",
        "authoritative_acceptance": False,
        "reference_canonical_sha256": reference_payload["canonical_sha256"],
        "reference_commit": reference_payload["reference_commit"],
        "candidate_production_source_sha256": code_fingerprint(),
        "candidate_raw_config_sha256": config_fingerprint(DEFAULT_CONFIG),
        "removed_config_fields_present": [
            field for field in _REMOVED_CONFIG_FIELDS if field in DEFAULT_CONFIG.to_dict()
        ],
        "dimensions": dimensions,
        "exact_economic_equivalence": all(dimensions.values()),
        "reference_full_account_codec_observation": baseline.get(
            "full_account_codec_observation"
        ),
        "candidate_full_account_codec_observation": candidate.get(
            "full_account_codec_observation"
        ),
        "candidate_projection": candidate,
    }
    sealed = _sealed(payload)
    _write_json(Path(output), sealed)
    return sealed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "compare"))
    parser.add_argument("--data-dir", default="data/frozen")
    parser.add_argument("--reference", default=str(_REFERENCE_PATH))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    if args.mode == "capture":
        result = capture_reference(
            data_dir=args.data_dir,
            output=args.output or args.reference,
        )
    else:
        result = compare_reference(
            data_dir=args.data_dir,
            reference=args.reference,
            output=args.output or _REPORT_PATH,
        )
    print(result["canonical_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
