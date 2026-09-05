"""Fail-closed historical debt projection for risk owner splits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from pathlib import Path

from ._owner_transport import (
    _definitions,
    _source,
    architecture_risk_reviewed_sources,
    expand_architecture_risk_assessment,
    expand_architecture_risk_stage,
)

_RISK_ARCHITECTURE_AUTHORITY_STALE = frozenset(
    {
        "uquant.account.code_identity",
        "uquant.account.validation_attribution",
        "uquant.application.target_attribution",
        "uquant.attribution.validation_artifact",
        "uquant.attribution.validation_lots",
        "uquant.broker_contract",
        "uquant.holding_history",
        "uquant.models.strategic_epoch",
        "uquant.models.strategic_grant",
        "uquant.models.strategic_rearm",
        "uquant.models.strategic_universe",
        "uquant.risk_sentinel.history_cache",
        "uquant.risk_sentinel.source_identity_archive",
        "uquant.validation.competitor_reference",
        "uquant.validation.generalization_matrix_evidence",
        "uquant.validation.generalization_matrix_validation",
        "uquant.validation.generalization_policy.cell_policy",
        "uquant.validation.generalization_policy.evaluation_stages",
        "uquant.validation.generalization_policy.tail_evaluation",
        "uquant.validation.holdout.capabilities",
        "uquant.validation.holdout.cli_operations",
        "uquant.validation.production_observation",
        "uquant.validation.production_observation_contract",
        "uquant.validation.promotion_contract",
    }
)
_RISK_ARCHITECTURE_AUTHORITY_REBINDINGS = {
    "uquant.account.code_identity": "uquant.account.migrations",
}


def architecture_risk_historical_authorities(
    authorities: Mapping[str, str],
    source_paths: Set[str],
) -> dict[str, str]:
    """Project exact current authorities onto the frozen risk archive."""
    source_modules = {
        path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".") for path in source_paths
    }
    stale = set(authorities) - source_modules
    assert stale == _RISK_ARCHITECTURE_AUTHORITY_STALE
    archived = source_modules - set(authorities)
    assert archived == set(_RISK_ARCHITECTURE_AUTHORITY_REBINDINGS.values())
    projected = {
        module: authority
        for module, authority in authorities.items()
        if module not in _RISK_ARCHITECTURE_AUTHORITY_STALE
    }
    for current, historical in _RISK_ARCHITECTURE_AUTHORITY_REBINDINGS.items():
        assert current in stale
        projected[historical] = authorities[current]
    return projected


_RISK_FUNCTION_OWNERS: Mapping[str, tuple[str, str]] = {
    "uquant.risk.anchors:_assess_dynamic_anchors": (
        "uquant/risk/anchors.py",
        "_assess_dynamic_anchors",
    ),
    "uquant.risk.assessment:_assess_base_risk": (
        "uquant/risk/assessment.py",
        "_assess_base_risk",
    ),
    "uquant.risk.assessment:_assess_market_and_book_evidence": (
        "uquant/risk/assessment.py",
        "_assess_market_and_book_evidence",
    ),
    "uquant.risk.capital:_observe_capital_budget": (
        "uquant/risk/capital.py",
        "_observe_capital_budget",
    ),
    "uquant.risk.recovery_state:_assess_protected_recovery": (
        "uquant/risk/recovery_state.py",
        "_assess_protected_recovery",
    ),
    "uquant.risk.recovery_state:_assess_recovery_state": (
        "uquant/risk/recovery_state.py",
        "_assess_recovery_state",
    ),
    "uquant.risk.transitions:_assess_acute_and_cooldown": (
        "uquant/risk/transitions.py",
        "_assess_acute_and_cooldown",
    ),
    "uquant.risk.transitions:_assess_break_conditions": (
        "uquant/risk/transitions.py",
        "_assess_break_conditions",
    ),
    "uquant.risk.transitions:_assess_confirmed_concentrated_break": (
        "uquant/risk/transitions.py",
        "_assess_confirmed_concentrated_break",
    ),
    "uquant.risk.transitions:_resolve_risk_transition": (
        "uquant/risk/transitions.py",
        "_resolve_risk_transition",
    ),
}

_CURRENT_PUBLIC_RISK_OWNERS: Mapping[str, tuple[str, str, str]] = {
    "uquant.risk.recovery_state:_assess_protected_recovery": (
        "uquant.risk.protected_recovery:assess_protected_recovery",
        "uquant/risk/protected_recovery.py",
        "assess_protected_recovery",
    ),
    "uquant.risk.transitions:_assess_confirmed_concentrated_break": (
        "uquant.risk.confirmed_break:assess_confirmed_concentrated_break",
        "uquant/risk/confirmed_break.py",
        "assess_confirmed_concentrated_break",
    ),
    "uquant.risk.transitions:_resolve_risk_transition": (
        "uquant.risk.transition_resolution:resolve_risk_transition",
        "uquant/risk/transition_resolution.py",
        "resolve_risk_transition",
    ),
}


def architecture_risk_function_debt_projection(
    *,
    root: Path,
    observed: Set[str],
    expected: Set[str],
    function_rows: Sequence[Mapping[str, object]],
    overrides: Mapping[str, str] | None = None,
) -> set[str]:
    """Restore historical risk IDs from the immutable reviewed projection."""
    missing = set(expected) - set(observed)
    assert not (set(observed) - set(expected))
    assert missing == set(_RISK_FUNCTION_OWNERS)
    current_rows = {str(row["id"]): row for row in function_rows}
    for identifier in set(_RISK_FUNCTION_OWNERS) & set(current_rows):
        current_lines = current_rows[identifier]["lines"]
        current_branches = current_rows[identifier]["branch_points"]
        assert isinstance(current_lines, int) and current_lines <= 120
        assert isinstance(current_branches, int) and current_branches <= 20
    reviewed_sources = architecture_risk_reviewed_sources(root=root, overrides=overrides)
    for identifier, (relative, function_name) in _RISK_FUNCTION_OWNERS.items():
        public_owner = _CURRENT_PUBLIC_RISK_OWNERS.get(identifier)
        row_identifier = public_owner[0] if public_owner is not None else identifier
        row = current_rows[row_identifier]
        assert int(row["lines"]) <= 120
        assert int(row["branch_points"]) <= 20
        definitions = _definitions(_source(root, relative, reviewed_sources))
        function = (
            _definitions(_source(root, public_owner[1], reviewed_sources))[
                public_owner[2]
            ]
            if public_owner is not None
            else definitions[function_name]
        )
        if function_name == "_assess_base_risk":
            expanded = expand_architecture_risk_assessment(
                root=root,
                candidate=function,
                overrides=reviewed_sources,
            )
            assert len(expanded.body) == 85
        else:
            expand_architecture_risk_stage(
                root=root,
                relative=relative,
                stage_name=function_name,
                wrapper=function,
                overrides=reviewed_sources,
            )
    return set(observed) | missing


def architecture_risk_historical_base_lines(
    *,
    root: Path,
    current_row: Mapping[str, object],
) -> int:
    """Return the immutable governance-base span after the live budget check."""
    assert current_row["id"] == "uquant.risk.assessment:_assess_base_risk"
    current_lines = current_row["lines"]
    current_branches = current_row["branch_points"]
    assert isinstance(current_lines, int) and current_lines <= 120
    assert isinstance(current_branches, int) and current_branches <= 20
    reviewed_sources = architecture_risk_reviewed_sources(root=root)
    reviewed = _definitions(
        _source(root, "uquant/risk/assessment.py", reviewed_sources)
    )["_assess_base_risk"]
    expanded = expand_architecture_risk_assessment(
        root=root,
        candidate=reviewed,
        overrides=reviewed_sources,
    )
    assert len(expanded.body) == 85
    assert expanded.end_lineno is not None
    span = expanded.end_lineno - expanded.lineno + 1
    assert isinstance(span, int)
    return span
