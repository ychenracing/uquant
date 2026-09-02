"""Adjudicate identity-only Checkpoint C drift without replaying the market window."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from uquant.contracts.strict_json import canonical_json_sha256, strict_json_loads

_REFERENCE_PATH: Final = Path("benchmarks/pre_cleanup_current_behavior_reference.json")
_REPORT_PATH: Final = Path("benchmarks/post_generalization_trust_closure_checkpoint_c.json")
_OUTPUT_PATH: Final = Path(
    "benchmarks/post_generalization_trust_closure_checkpoint_c_adjudication.json"
)
_IDENTITY_FIELDS: Final = frozenset(
    {"config_identity", "production_source_identity", "source_identity"}
)
_EXACT_DIMENSIONS: Final = (
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
_EXPECTED_IDENTITY_DIFFERENCES: Final = (
    "strategic_epochs[0].config_identity",
    "strategic_epochs[0].source_identity",
    "strategic_grant.production_source_identity",
)


def _read_sealed(path: Path) -> dict[str, Any]:
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"sealed JSON is not an object: {path}")
    unsigned = dict(value)
    claimed = unsigned.pop("canonical_sha256", None)
    if claimed != canonical_json_sha256(unsigned):
        raise ValueError(f"sealed JSON hash differs: {path}")
    return value


def _without_provenance_identities(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _without_provenance_identities(child)
            for key, child in value.items()
            if key not in _IDENTITY_FIELDS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_provenance_identities(child) for child in value]
    return value


def _difference_paths(left: object, right: object, *, path: str) -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child_path)
            else:
                paths.extend(_difference_paths(left[key], right[key], path=child_path))
        return paths
    if (
        isinstance(left, Sequence)
        and isinstance(right, Sequence)
        and not isinstance(left, (str, bytes, bytearray))
        and not isinstance(right, (str, bytes, bytearray))
    ):
        paths = []
        if len(left) != len(right):
            paths.append(f"{path}.length")
        for index, (left_child, right_child) in enumerate(zip(left, right, strict=False)):
            paths.extend(
                _difference_paths(left_child, right_child, path=f"{path}[{index}]")
            )
        return paths
    return [] if left == right else [path]


def _codec_matches_account_hash(projection: Mapping[str, Any]) -> bool:
    observation = projection.get("full_account_codec_observation")
    return observation == {
        "status": "SUCCESS",
        "schema_version": 8,
        "roundtrip_economic_sha256": projection.get("final_account_economic_sha256"),
    }


def _require_identity_bindings(
    *,
    reference: Mapping[str, Any],
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    reference_epoch = baseline["strategic_epochs"][0]
    candidate_epoch = candidate["strategic_epochs"][0]
    reference_grant = baseline["strategic_grant"]
    candidate_grant = candidate["strategic_grant"]
    if not all(
        isinstance(item, Mapping)
        for item in (reference_epoch, candidate_epoch, reference_grant, candidate_grant)
    ):
        raise ValueError("strategic identity carriers are malformed")
    expected = {
        "reference_epoch_config": f"config:{reference['raw_config_sha256']}",
        "candidate_epoch_config": f"config:{report['candidate_raw_config_sha256']}",
        "reference_epoch_source": reference["production_source_sha256"],
        "candidate_epoch_source": report["candidate_production_source_sha256"],
        "reference_grant_source": reference["production_source_sha256"],
        "candidate_grant_source": report["candidate_production_source_sha256"],
    }
    observed = {
        "reference_epoch_config": reference_epoch.get("config_identity"),
        "candidate_epoch_config": candidate_epoch.get("config_identity"),
        "reference_epoch_source": reference_epoch.get("source_identity"),
        "candidate_epoch_source": candidate_epoch.get("source_identity"),
        "reference_grant_source": reference_grant.get("production_source_identity"),
        "candidate_grant_source": candidate_grant.get("production_source_identity"),
    }
    if observed != expected:
        raise ValueError("strategic identities do not bind the recorded source and config")


def adjudicate_report(
    *,
    reference: str | Path = _REFERENCE_PATH,
    report: str | Path = _REPORT_PATH,
) -> dict[str, Any]:
    """Prove deterministic equivalence from one completed sealed candidate replay."""

    reference_payload = _read_sealed(Path(reference))
    report_payload = _read_sealed(Path(report))
    if report_payload.get("reference_canonical_sha256") != reference_payload["canonical_sha256"]:
        raise ValueError("checkpoint C report does not bind the sealed reference")
    if report_payload.get("removed_config_fields_present") != []:
        raise ValueError("checkpoint C candidate still contains removed config fields")
    baseline = reference_payload.get("projection")
    candidate = report_payload.get("candidate_projection")
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        raise ValueError("checkpoint C projections are malformed")

    recomputed = {key: baseline.get(key) == candidate.get(key) for key in _EXACT_DIMENSIONS}
    if report_payload.get("dimensions") != recomputed:
        raise ValueError("checkpoint C report dimensions differ from recomputation")
    exact = all(recomputed.values())
    if report_payload.get("exact_economic_equivalence") is not exact:
        raise ValueError("checkpoint C exact-equivalence summary differs")
    false_dimensions = {key for key, equal in recomputed.items() if not equal}
    if false_dimensions != {
        "final_account_economic_sha256",
        "strategic_epochs",
        "strategic_grant",
    }:
        raise ValueError("checkpoint C report has non-identity economic drift")

    identity_paths = sorted(
        _difference_paths(
            baseline["strategic_epochs"],
            candidate["strategic_epochs"],
            path="strategic_epochs",
        )
        + _difference_paths(
            baseline["strategic_grant"],
            candidate["strategic_grant"],
            path="strategic_grant",
        )
    )
    if tuple(identity_paths) != _EXPECTED_IDENTITY_DIFFERENCES:
        raise ValueError("checkpoint C strategic drift is not identity-only")
    _require_identity_bindings(
        reference=reference_payload,
        report=report_payload,
        baseline=baseline,
        candidate=candidate,
    )

    epoch_economic = _without_provenance_identities(
        baseline["strategic_epochs"]
    ) == _without_provenance_identities(candidate["strategic_epochs"])
    grant_economic = _without_provenance_identities(
        baseline["strategic_grant"]
    ) == _without_provenance_identities(candidate["strategic_grant"])
    current_codec = _codec_matches_account_hash(baseline) and _codec_matches_account_hash(
        candidate
    )
    final_account_observed = all(
        recomputed[key]
        for key in ("account_sessions", "fills", "metrics", "orders")
    ) and all((epoch_economic, grant_economic, current_codec))
    dimensions = {
        "account_sessions": recomputed["account_sessions"],
        "attribution_accounting": recomputed["attribution_accounting"],
        "config_semantic_sha256": recomputed["config_semantic_sha256"],
        "current_account_codec": current_codec,
        "decision_sessions": recomputed["decision_sessions"],
        "empty_current_account_roundtrip_sha256": recomputed[
            "empty_current_account_roundtrip_sha256"
        ],
        "fills": recomputed["fills"],
        "final_account_observed_economics": final_account_observed,
        "ledger_sessions": recomputed["ledger_sessions"],
        "metrics": recomputed["metrics"],
        "orders": recomputed["orders"],
        "strategic_epochs_economic": epoch_economic,
        "strategic_grant_economic": grant_economic,
        "window": recomputed["window"],
    }
    deterministic = all(dimensions.values())
    payload = {
        "schema_version": 1,
        "evidence_id": "post-generalization-trust-closure-checkpoint-c-adjudication",
        "scope": "CURRENT_SUPPORTED_PATH_DETERMINISTIC_EQUIVALENCE_ADJUDICATION",
        "authoritative_acceptance": False,
        "future_holdout_used": False,
        "reference_canonical_sha256": reference_payload["canonical_sha256"],
        "candidate_report_canonical_sha256": report_payload["canonical_sha256"],
        "exact_economic_equivalence": exact,
        "deterministic_economic_equivalence": deterministic,
        "identity_only_difference_paths": identity_paths,
        "dimensions": dimensions,
        "identity_bound_full_account_hashes": {
            "reference": baseline["final_account_economic_sha256"],
            "candidate": candidate["final_account_economic_sha256"],
            "equal": baseline["final_account_economic_sha256"]
            == candidate["final_account_economic_sha256"],
        },
        "identity_rebinding": {
            "reference_production_source_sha256": reference_payload[
                "production_source_sha256"
            ],
            "candidate_production_source_sha256": report_payload[
                "candidate_production_source_sha256"
            ],
            "reference_raw_config_sha256": reference_payload["raw_config_sha256"],
            "candidate_raw_config_sha256": report_payload["candidate_raw_config_sha256"],
            "semantic_config_sha256": candidate["config_semantic_sha256"],
        },
    }
    return {**payload, "canonical_sha256": canonical_json_sha256(payload)}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", default=str(_REFERENCE_PATH))
    parser.add_argument("--report", default=str(_REPORT_PATH))
    parser.add_argument("--output", default=str(_OUTPUT_PATH))
    args = parser.parse_args(argv)
    result = adjudicate_report(reference=args.reference, report=args.report)
    _write_json(Path(args.output), result)
    print(result["canonical_sha256"])
    return 0 if result["deterministic_economic_equivalence"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
