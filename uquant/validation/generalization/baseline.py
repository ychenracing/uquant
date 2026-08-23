"""Deterministic universe-generalization diagnostics and frozen references.

The production engine remains the only strategy implementation.  This module
only constructs causal universe perturbations, replays them through a supplied
runner, and aggregates dependency evidence.  It deliberately has no API that
writes a baseline file: reference updates must remain an explicit, reviewed
repository change.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import (
    _BASELINE_SCHEMA_VERSION,
    _POLICY_FIELDS,
    _REFERENCE_FIELDS,
    _SHA256,
    GeneralizationBaseline,
    GeneralizationObservation,
    GeneralizationPolicy,
    GeneralizationScenario,
)
from .provenance import (
    _validated_competitor_best,
    _validated_provenance,
    _validation_fingerprint,
)
from .scenarios import scenario_fingerprint


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"generalization baseline contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_constant(value: str) -> None:
    raise RuntimeError(f"generalization baseline contains a non-standard number: {value}")


def _policy_number(payload: Mapping[str, Any], name: str) -> float:
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"generalization policy field must be numeric: {name}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RuntimeError(f"generalization policy field must be finite: {name}")
    return numeric


def _parse_policy(value: Any) -> GeneralizationPolicy:
    """Parse a complete policy object and enforce every numeric bound."""
    if not isinstance(value, Mapping):
        raise RuntimeError("generalization baseline policy must be an object")
    observed = set(value)
    missing = sorted(_POLICY_FIELDS - observed)
    unexpected = sorted(observed - _POLICY_FIELDS)
    if missing:
        raise RuntimeError(f"generalization policy is missing fields: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization policy has unexpected fields: {unexpected}")

    raw_order_tolerance = value["order_tolerance"]
    if (
        isinstance(raw_order_tolerance, bool)
        or not isinstance(raw_order_tolerance, int)
        or raw_order_tolerance < 0
    ):
        raise RuntimeError("generalization policy.order_tolerance must be a nonnegative integer")
    numbers = {name: _policy_number(value, name) for name in _POLICY_FIELDS - {"order_tolerance"}}
    if not 0 < numbers["wealth_floor_ratio"] <= 1:
        raise RuntimeError("generalization wealth_floor_ratio must be in (0, 1]")
    if not 0 <= numbers["drawdown_tolerance"] <= 1:
        raise RuntimeError("generalization drawdown_tolerance must be in [0, 1]")
    if numbers["order_ceiling_ratio"] < 1:
        raise RuntimeError("generalization order_ceiling_ratio cannot be below one")
    material_fields = {
        "dominance_wealth_regression",
        "dominance_drawdown_regression",
        "dominance_order_regression",
        "pareto_wealth_improvement",
        "pareto_drawdown_improvement",
        "pareto_order_improvement",
        "pareto_wealth_regression",
        "pareto_drawdown_regression",
        "pareto_order_regression",
    }
    if any(numbers[name] < 0 for name in material_fields):
        raise RuntimeError("generalization dominance/Pareto thresholds cannot be negative")
    bounded_material_fields = {
        "dominance_wealth_regression",
        "dominance_drawdown_regression",
        "pareto_drawdown_improvement",
        "pareto_wealth_regression",
        "pareto_drawdown_regression",
    }
    if any(numbers[name] > 1 for name in bounded_material_fields):
        raise RuntimeError("generalization bounded dominance/Pareto thresholds cannot exceed one")
    if not 0 <= numbers["remove_one_max_dependency"] <= 0.25:
        raise RuntimeError("generalization remove-one dependency ceiling must be in [0, 0.25]")
    if numbers["remove_all_min_wealth"] <= 1 or numbers["no_optical_min_wealth"] <= 1:
        raise RuntimeError("generalization removal scenarios must require positive return")
    if not 0 <= numbers["remove_all_max_drawdown"] <= 1:
        raise RuntimeError("generalization remove-all drawdown ceiling must be in [0, 1]")
    if not 0.95 <= numbers["remove_all_competitor_ratio"] <= 1.5:
        raise RuntimeError("generalization remove-all competitor ratio must be in [0.95, 1.5]")
    if not 0 <= numbers["no_optical_max_drawdown"] <= 1:
        raise RuntimeError("generalization no-optical drawdown ceiling must be in [0, 1]")
    if not 0.5 < numbers["random_min_positive_fraction"] <= 1:
        raise RuntimeError("generalization random positive fraction must be in (0.5, 1]")
    if numbers["random_p10_min_wealth"] < 1:
        raise RuntimeError("generalization random p10 wealth floor cannot be below one")
    if not 0 < numbers["optical_dependency_share_threshold"] <= 0.70:
        raise RuntimeError("generalization optical dependency threshold must be in (0, 0.70]")

    return GeneralizationPolicy(
        order_tolerance=raw_order_tolerance,
        **numbers,
    )


def _read_generalization_baseline(path: str | Path) -> tuple[bytes, dict[str, Any]]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"generalization baseline is missing or corrupt: {source}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("generalization baseline must be a JSON object")
    return raw, payload


def _validate_baseline_envelope(
    payload: Mapping[str, Any],
    *,
    expected_provenance: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], GeneralizationPolicy]:
    """Validate baseline sections, fingerprints, policy, and replay provenance."""
    expected_sections = {
        "schema_version",
        "case_fingerprint",
        "validation_fingerprint",
        "provenance",
        "competitor_best",
        "policy",
        "references",
    }
    missing_sections = sorted(expected_sections - set(payload))
    unexpected_sections = sorted(set(payload) - expected_sections)
    if missing_sections:
        raise RuntimeError(f"generalization baseline is missing sections: {missing_sections}")
    if unexpected_sections:
        raise RuntimeError(f"generalization baseline has unexpected sections: {unexpected_sections}")
    if payload.get("schema_version") != _BASELINE_SCHEMA_VERSION:
        raise RuntimeError("unsupported generalization baseline schema")
    provenance = _validated_provenance(payload["provenance"])
    competitor_best = _validated_competitor_best(payload["competitor_best"])
    case_fingerprint = payload["case_fingerprint"]
    if not isinstance(case_fingerprint, str) or not _SHA256.fullmatch(case_fingerprint):
        raise RuntimeError("generalization case_fingerprint must be SHA-256")
    validation_fingerprint = payload["validation_fingerprint"]
    if not isinstance(validation_fingerprint, str) or not _SHA256.fullmatch(validation_fingerprint):
        raise RuntimeError("generalization validation_fingerprint must be SHA-256")
    if validation_fingerprint != _validation_fingerprint(
        case_fingerprint=case_fingerprint,
        provenance=provenance,
        competitor_best=competitor_best,
    ):
        raise RuntimeError("generalization validation fingerprint is stale")
    if expected_provenance is not None:
        expected = _validated_provenance(expected_provenance)
        if provenance != expected:
            raise RuntimeError("generalization baseline provenance does not match this replay")
    policy = _parse_policy(payload["policy"])
    return provenance, competitor_best, policy


def load_generalization_baseline(
    path: str | Path,
    cases: Sequence[GeneralizationScenario],
    *,
    expected_provenance: Mapping[str, Any] | None = None,
) -> GeneralizationBaseline:
    """Load a frozen baseline strictly, without normalizing or rewriting it."""
    raw, payload = _read_generalization_baseline(path)
    provenance, competitor_best, policy = _validate_baseline_envelope(
        payload,
        expected_provenance=expected_provenance,
    )
    references = payload.get("references")
    if not isinstance(references, dict):
        raise RuntimeError("generalization baseline references must be an object")
    expected = {case.name for case in cases}
    observed = set(references)
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise RuntimeError(f"generalization baseline is missing references: {missing}")
    if unexpected:
        raise RuntimeError(f"generalization baseline has unexpected references: {unexpected}")
    expected_fingerprint = scenario_fingerprint(cases)
    if payload.get("case_fingerprint") != expected_fingerprint:
        raise RuntimeError("generalization baseline case fingerprint is stale")

    validated: dict[str, dict[str, float | int]] = {}
    for name in sorted(expected):
        reference = references[name]
        if not isinstance(reference, dict):
            raise RuntimeError(f"generalization reference must be an object: {name}")
        missing_fields = sorted(_REFERENCE_FIELDS - set(reference))
        unexpected_fields = sorted(set(reference) - _REFERENCE_FIELDS)
        if missing_fields:
            raise RuntimeError(f"generalization reference is missing metrics: {name} {missing_fields}")
        if unexpected_fields:
            raise RuntimeError(f"generalization reference has unexpected metrics: {name} {unexpected_fields}")
        wealth = reference["final_wealth"]
        drawdown = reference["max_drawdown"]
        orders = reference["account_orders"]
        if (
            isinstance(wealth, bool)
            or not isinstance(wealth, (int, float))
            or not math.isfinite(float(wealth))
            or float(wealth) <= 0
        ):
            raise RuntimeError(f"generalization reference has invalid wealth: {name}")
        if (
            isinstance(drawdown, bool)
            or not isinstance(drawdown, (int, float))
            or not math.isfinite(float(drawdown))
            or not 0 <= float(drawdown) <= 1
        ):
            raise RuntimeError(f"generalization reference has invalid drawdown: {name}")
        if isinstance(orders, bool) or not isinstance(orders, int) or orders < 0:
            raise RuntimeError(f"generalization reference has invalid order count: {name}")
        validated[name] = {
            "final_wealth": float(wealth),
            "max_drawdown": float(drawdown),
            "account_orders": orders,
        }
    return GeneralizationBaseline(
        sha256=hashlib.sha256(raw).hexdigest(),
        case_fingerprint=expected_fingerprint,
        validation_fingerprint=_validation_fingerprint(
            case_fingerprint=expected_fingerprint,
            provenance=provenance,
            competitor_best=competitor_best,
        ),
        provenance=provenance,
        competitor_best=competitor_best,
        policy=policy,
        references=validated,
    )


def reference_payload(
    cases: Sequence[GeneralizationScenario],
    observations: Sequence[GeneralizationObservation],
    *,
    policy: Mapping[str, Any],
    provenance: Mapping[str, Any],
    competitor_best: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a caller-specified reference payload; no policy is fabricated."""
    by_name = {item.name: item for item in observations}
    if len(by_name) != len(observations):
        raise ValueError("generalization observations contain duplicate names")
    expected = {case.name for case in cases}
    if set(by_name) != expected:
        raise ValueError("generalization observations do not exactly cover the case matrix")
    validated_policy = _parse_policy(policy)
    validated_provenance = _validated_provenance(provenance)
    validated_competitor = _validated_competitor_best(competitor_best)
    case_fingerprint = scenario_fingerprint(cases)
    return {
        "schema_version": _BASELINE_SCHEMA_VERSION,
        "case_fingerprint": case_fingerprint,
        "validation_fingerprint": _validation_fingerprint(
            case_fingerprint=case_fingerprint,
            provenance=validated_provenance,
            competitor_best=validated_competitor,
        ),
        "provenance": validated_provenance,
        "competitor_best": validated_competitor,
        "policy": validated_policy.to_dict(),
        "references": {
            name: {
                "final_wealth": by_name[name].final_wealth,
                "max_drawdown": by_name[name].max_drawdown,
                "account_orders": by_name[name].account_orders,
            }
            for name in sorted(by_name)
        },
    }
