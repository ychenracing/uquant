"""Fail-closed owner projections for immutable risk and portfolio gates."""

from __future__ import annotations

import ast
import copy
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from pathlib import Path

from ._governance_inventory import ARCHITECTURE_REFERENCE_TREE

_ECONOMIC_ADDITIONS = frozenset(
    {
        "uquant/account/validation_attribution.py",
        "uquant/application/target_attribution.py",
        "uquant/attribution/validation_artifact.py",
        "uquant/attribution/validation_lots.py",
        "uquant/portfolio/allocation_closure.py",
        "uquant/portfolio/allocation_opening.py",
        "uquant/portfolio/allocation_protected.py",
        "uquant/portfolio/allocation_recovery.py",
        "uquant/portfolio/allocation_tactical.py",
        "uquant/portfolio/leaders/extensions.py",
        "uquant/portfolio/recovery/cohort_admission.py",
        "uquant/portfolio/recovery/tactical_admission.py",
        "uquant/portfolio/strategic/qualification_candidates.py",
        "uquant/risk/confirmed_break.py",
        "uquant/risk/market_book.py",
        "uquant/risk/protected_recovery.py",
        "uquant/risk/transition_resolution.py",
        "uquant/risk_sentinel/history_cache.py",
    }
)
_EXECUTION_ADDITIONS = frozenset(
    {
        "uquant/account/validation_attribution.py",
        "uquant/broker_contract.py",
    }
)
_SENTINEL_ADDITIONS = frozenset(
    {
        "uquant/account/validation_attribution.py",
        "uquant/risk_sentinel/history_cache.py",
        "uquant/risk_sentinel/source_identity_archive.py",
    }
)
_VALIDATION_ADDITIONS = frozenset(
    {
        "research/current_heads_competitor_matrix.py",
        "research/five_window_outperformance.py",
        "research/future_holdout_cli.py",
        "research/performance_diagnostic.py",
        "research/generalization_ablation_cli.py",
        "research/risk_counterfactual_cli.py",
        "research/risk_differential_analysis.py",
        "research/risk_differential_cli.py",
        "research/tencent_history_adapter.py",
        "research/window_competitor_adapter.py",
        "research/window_outperformance.py",
        "scripts/build_reproducible_wheel.py",
        "uquant/risk_sentinel/source_identity_archive.py",
        "uquant/validation/competitor_reference.py",
        "uquant/validation/generalization_matrix_evidence.py",
        "uquant/validation/generalization_matrix_validation.py",
        "uquant/validation/generalization_policy/cell_policy.py",
        "uquant/validation/generalization_policy/evaluation_stages.py",
        "uquant/validation/generalization_policy/tail_evaluation.py",
        "uquant/validation/holdout/capabilities.py",
        "uquant/validation/holdout/cli_operations.py",
        "uquant/validation/production_observation.py",
        "uquant/validation/production_observation_contract.py",
        "uquant/validation/promotion_contract.py",
    }
)
ARCHITECTURE_SOURCE_SURFACE_ADDITIONS: Mapping[str, frozenset[str]] = {
    "economic_decision_v1": _ECONOMIC_ADDITIONS,
    "execution_account_v1": _EXECUTION_ADDITIONS,
    "sentinel_v1": _SENTINEL_ADDITIONS,
    "validation_runner_v1": _VALIDATION_ADDITIONS,
    "full_package_v1": frozenset(
        (
            _ECONOMIC_ADDITIONS
            | _EXECUTION_ADDITIONS
            | _SENTINEL_ADDITIONS
            | _VALIDATION_ADDITIONS
        )
        - {"scripts/build_reproducible_wheel.py"}
    ),
}

_CURRENT_SOURCE_PATHS = {
    "research/phase1_diagnostic.py": "research/performance_diagnostic.py",
    "research/phase2_ablation_cli.py": "research/generalization_ablation_cli.py",
    "scripts/run_phase1_diagnostic.py": "scripts/run_performance_diagnostic.py",
    "scripts/run_phase2_ablation.py": "scripts/run_generalization_ablation.py",
    "scripts/verify_phase1_decision_equivalence.py": (
        "scripts/verify_decision_equivalence.py"
    ),
}

_CURRENT_RESOURCE_PATHS = {
    "benchmarks/architecture_refactor_public_api.json": (
        "benchmarks/public_api_contract.json"
    ),
    "uquant/contracts/resources/phase1_frozen_champion.json": (
        "uquant/contracts/resources/performance_frozen_champion.json"
    ),
    "uquant/validation/resources/phase1_frozen_champion.json": (
        "uquant/validation/resources/performance_frozen_champion.json"
    ),
}


def architecture_source_surface_projection(identifier: str, historical: Set[str]) -> set[str]:
    """Add only the exact owners registered for one frozen surface."""
    assert identifier in ARCHITECTURE_SOURCE_SURFACE_ADDITIONS
    additions = ARCHITECTURE_SOURCE_SURFACE_ADDITIONS[identifier]
    projected = set(historical)
    for previous, current in _CURRENT_SOURCE_PATHS.items():
        if previous in projected:
            projected.remove(previous)
            projected.add(current)
    assert not (projected & additions)
    return projected | set(additions)


def architecture_resource_surface_projection(historical: Sequence[str]) -> list[str]:
    """Project current resource paths without changing resource bytes or protocol IDs."""

    return sorted(_CURRENT_RESOURCE_PATHS.get(path, path) for path in historical)


def _definitions(source: str) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.parse(source, type_comments=True).body
        if isinstance(node, ast.FunctionDef)
    }


def _definition_or_exact_alias(
    root: Path,
    relative: str,
    source: str,
    name: str,
) -> tuple[ast.FunctionDef, bool]:
    tree = ast.parse(source, type_comments=True)
    definitions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    if name in definitions:
        return definitions[name], False
    aliases = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
        and isinstance(node.value, ast.Name)
    ]
    assert len(aliases) == 1
    public = aliases[0].value.id
    if public in definitions:
        return definitions[public], True
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == public and alias.asname is None for alias in node.names)
    ]
    assert len(imports) == 1 and imports[0].level == 1 and imports[0].module
    imported_relative = (
        Path(relative).parent / f"{imports[0].module.replace('.', '/')}.py"
    ).as_posix()
    imported = _definitions((root / imported_relative).read_text(encoding="utf-8"))
    assert public in imported
    return imported[public], True


def _source(root: Path, relative: str, overrides: Mapping[str, str] | None) -> str:
    if overrides is not None and relative in overrides:
        return overrides[relative]
    return (root / relative).read_text(encoding="utf-8")


def architecture_risk_reviewed_sources(
    *,
    root: Path,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the current risk owners plus bounded mutation candidates."""
    reviewed = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / "uquant" / "risk").rglob("*.py"))
    }
    if overrides is not None:
        assert not (set(overrides) - set(reviewed))
        reviewed.update(overrides)
    return reviewed


def architecture_portfolio_reviewed_sources(
    *,
    root: Path,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the current portfolio owners plus bounded mutation candidates."""
    reviewed = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / "uquant" / "portfolio").rglob("*.py"))
    }
    if overrides is not None:
        assert not (set(overrides) - set(reviewed))
        reviewed.update(overrides)
    return reviewed


def _alias_is_exact(source: str, public: str, private: str) -> None:
    tree = ast.parse(source, type_comments=True)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == public
            for target in ([node.target] if isinstance(node, ast.AnnAssign) else node.targets)
        )
    ]
    assert len(matches) == 1
    value = matches[0].value
    assert isinstance(value, ast.Name) and value.id == private


def _imports_exact_public(
    source: str,
    *,
    imported_module: str,
    public: str,
    asname: str | None = None,
) -> None:
    matches = [
        alias
        for node in ast.parse(source, type_comments=True).body
        if isinstance(node, ast.ImportFrom) and node.module == imported_module
        for alias in node.names
        if alias.name == public
    ]
    assert len(matches) == 1 and matches[0].asname == asname


def _renamed_function_is_exact(
    current_source: str,
    *,
    current_name: str,
    legacy_source: str,
    legacy_name: str,
) -> None:
    current = copy.deepcopy(_definitions(current_source)[current_name])
    legacy = _definitions(legacy_source)[legacy_name]
    current.name = legacy.name
    assert ast.dump(current, include_attributes=False) == ast.dump(legacy, include_attributes=False)


_RISK_PRIVATE_MOVES = {
    "uquant.risk.assessment:uquant.risk.capital:_portfolio_drawdowns": (
        "uquant/risk/capital.py",
        "portfolio_drawdowns",
        "_portfolio_drawdowns",
        "uquant/risk/market_book.py",
        "capital",
    ),
    "uquant.risk.assessment:uquant.risk.recovery_state:_reset_recovery_owner_rearm": (
        "uquant/risk/recovery_state.py",
        "reset_recovery_owner_rearm",
        "_reset_recovery_owner_rearm",
        "uquant/risk/market_book.py",
        "recovery_state",
    ),
    "uquant.risk.transitions:uquant.risk.strategic_guard:_strategic_crisis_severity": (
        "uquant/risk/strategic_guard.py",
        "strategic_crisis_severity",
        "_strategic_crisis_severity",
        "uquant/risk/confirmed_break.py",
        "strategic_guard",
    ),
}
_RISK_RENAMED_MOVE = (
    "uquant.risk.transitions:uquant.risk.recovery_state:_persistent_crisis_cap",
    "uquant/risk/protected_recovery.py",
    "persistent_crisis_cap",
    "uquant/risk/recovery_state.py",
    "_persistent_crisis_cap",
    "uquant/risk/transition_resolution.py",
    "protected_recovery",
)

_PORTFOLIO_PRIVATE_MOVES = {
    "uquant.portfolio.pipeline:uquant.portfolio.recovery.admission:_recovery_admission_targets": (
        "uquant/portfolio/recovery/admission.py",
        "recovery_admission_targets",
        "_recovery_admission_targets",
        "uquant/portfolio/allocation_recovery.py",
        "recovery.admission",
    ),
    "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_awaiting_recovery_cohort_targets": (
        "uquant/portfolio/recovery/targets.py",
        "awaiting_recovery_cohort_targets",
        "_awaiting_recovery_cohort_targets",
        "uquant/portfolio/recovery/cohort_admission.py",
        "targets",
    ),
    "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_controlled_oversold_rebound_targets": (
        "uquant/portfolio/recovery/targets.py",
        "controlled_oversold_rebound_targets",
        "_controlled_oversold_rebound_targets",
        "uquant/portfolio/recovery/tactical_admission.py",
        "targets",
    ),
    "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_locked_recovery_cohort_targets": (
        "uquant/portfolio/recovery/targets.py",
        "locked_recovery_cohort_targets",
        "_locked_recovery_cohort_targets",
        "uquant/portfolio/recovery/cohort_admission.py",
        "targets",
    ),
    "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_overextended_pullback_targets": (
        "uquant/portfolio/recovery/targets.py",
        "overextended_pullback_targets",
        "_overextended_pullback_targets",
        "uquant/portfolio/recovery/tactical_admission.py",
        "targets",
    ),
    "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_recovery_cohort_targets": (
        "uquant/portfolio/recovery/targets.py",
        "recovery_cohort_targets",
        "_recovery_cohort_targets",
        "uquant/portfolio/recovery/cohort_admission.py",
        "targets",
    ),
}


def architecture_private_relocation_projection(
    *,
    root: Path,
    task: int,
    observed: Set[str],
    expected: Set[str],
    overrides: Mapping[str, str] | None = None,
) -> set[str]:
    """Project only exact public-owner moves back to their historical edge IDs."""
    moves = _RISK_PRIVATE_MOVES if task == 7 else _PORTFOLIO_PRIVATE_MOVES
    assert task in {7, 8}
    if task == 7:
        overrides = architecture_risk_reviewed_sources(root=root, overrides=overrides)
    else:
        overrides = architecture_portfolio_reviewed_sources(root=root, overrides=overrides)
    missing = set(expected) - set(observed)
    expected_missing = set(moves)
    if task == 7:
        expected_missing.add(_RISK_RENAMED_MOVE[0])
    assert not (set(observed) - set(expected)) and missing == expected_missing
    for legacy, (owner, public, private, importer, imported_module) in moves.items():
        _alias_is_exact(_source(root, owner, overrides), public, private)
        _imports_exact_public(
            _source(root, importer, overrides),
            imported_module=imported_module,
            public=public,
            asname=("run_recovery_admission" if public == "recovery_admission_targets" else None),
        )
        assert legacy in missing
    if task == 7:
        (
            legacy,
            owner,
            public,
            legacy_owner,
            private,
            importer,
            imported_module,
        ) = _RISK_RENAMED_MOVE
        _renamed_function_is_exact(
            _source(root, owner, overrides),
            current_name=public,
            legacy_source=_source(root, legacy_owner, overrides),
            legacy_name=private,
        )
        _imports_exact_public(
            _source(root, importer, overrides),
            imported_module=imported_module,
            public=public,
        )
        assert legacy in missing
    return set(observed) | missing


_PIPELINE_STAGES = (
    ("uquant/portfolio/allocation_opening.py", "prepare_allocation", 1, 38),
    ("uquant/portfolio/allocation_tactical.py", "allocate_tactical", 38, 51),
    (
        "uquant/portfolio/allocation_protected.py",
        "restore_protected_allocation",
        51,
        52,
    ),
    ("uquant/portfolio/allocation_recovery.py", "allocate_recovery", 52, 83),
    ("uquant/portfolio/allocation_closure.py", "close_allocation", 83, 95),
)
_PIPELINE_CALL_GRAPH: Mapping[str, tuple[str, ...]] = {
    "_allocate_strategy": tuple(stage[1] for stage in _PIPELINE_STAGES),
    "prepare_allocation": (
        "_initialize_allocation",
        "_risk_neutral_handoff",
        "_risk_neutral_expansion",
        "_recovery_transfer_conditions",
        "_repair_locked_cohort",
        "_caution_recovery_trail",
        "_hard_recovery_trail",
        "_tactical_expiry_due",
        "_bounded_recovery_conditions",
        "_strategic_allocation",
    ),
    "_allocate_tactical_book": (
        "_advance_tactical_cooldown",
        "_active_tactical_positions",
        "_promote_tactical_recovery",
        "_graduate_tactical_leader",
        "_allocate_tactical_position",
        "_allocate_crisis",
    ),
    "allocate_tactical": (
        "_allocate_frozen",
        "_arm_leader_cycle",
        "_allocate_tactical_book",
    ),
    "_protected_proposal": ("_protected_restoration_is_open",),
    "restore_protected_allocation": (
        "_protected_proposal",
        "_restoration_status",
        "_deferred_restoration_targets",
        "_commit_restoration",
        "_final_restoration_targets",
    ),
    "_trail_recovery_anchors": (
        "_trailed_recovery_winners",
        "_winner_trail_targets",
    ),
    "_allocate_owner_rearm": ("_owner_rearm_is_open",),
    "_admit_recovery": ("run_recovery_admission",),
    "_allocate_recovery_route": (
        "_recovery_market",
        "_graduate_recovery",
        "_mature_recovery_anchor",
        "_admit_recovery",
        "_bounded_recovery_fallback",
    ),
    "allocate_recovery": (
        "_trail_recovery_anchors",
        "_allocate_owner_rearm",
        "_allocate_recovery_route",
    ),
    "_close_live_core": ("_slow_market_owner_is_active",),
    "close_allocation": (
        "_allocate_armed_leader",
        "_retain_confirmed_live_core",
        "_close_live_core",
    ),
}
_ECONOMIC_NODE_TYPES = (
    ast.Compare,
    ast.BoolOp,
    ast.BinOp,
    ast.IfExp,
    ast.DictComp,
    ast.SetComp,
    ast.ListComp,
    ast.GeneratorExp,
    ast.Call,
)


class _PipelineTransportNames(ast.NodeTransformer):
    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        node = self.generic_visit(node)
        assert isinstance(node, ast.Attribute)
        if isinstance(node.value, ast.Name) and node.value.id == "state":
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == "run_recovery_admission":
            return ast.copy_location(ast.Name(id="_recovery_admission_targets", ctx=node.ctx), node)
        return node


def _economic_nodes(statements: Sequence[ast.stmt]) -> Counter[str]:
    return Counter(
        ast.dump(node, include_attributes=False)
        for statement in statements
        for node in ast.walk(statement)
        if isinstance(node, _ECONOMIC_NODE_TYPES)
    )


def _function_calls(
    function: ast.FunctionDef,
    known: Set[str],
) -> tuple[ast.Call, ...]:
    return tuple(
        node
        for statement in function.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in known
    )


def _validate_bound_call(
    call: ast.Call,
    callee: ast.FunctionDef,
    *,
    value_aliases: Mapping[str, str] | None = None,
) -> None:
    assert not any(keyword.arg is None for keyword in call.keywords)
    aliases = {} if value_aliases is None else value_aliases
    positional = [*callee.args.posonlyargs, *callee.args.args]
    assert len(call.args) <= len(positional)
    for argument, parameter in zip(call.args, positional, strict=False):
        expected_value = aliases.get(parameter.arg, parameter.arg)
        assert isinstance(argument, ast.Name) and argument.id == expected_value
    provided = [parameter.arg for parameter in positional[: len(call.args)]]
    provided.extend(str(keyword.arg) for keyword in call.keywords)
    parameters = [
        *(argument.arg for argument in positional),
        *(argument.arg for argument in callee.args.kwonlyargs),
    ]
    assert provided == [name for name in parameters if name in provided]
    for keyword in call.keywords:
        expected_value = aliases.get(str(keyword.arg), str(keyword.arg))
        direct = ast.unparse(keyword.value) == expected_value
        carried = (
            isinstance(keyword.value, ast.Attribute)
            and isinstance(keyword.value.value, ast.Name)
            and keyword.value.value.id in {"state", "restoration"}
            and keyword.value.attr == keyword.arg
        )
        assert direct or carried
    positional_required = len(positional) - len(callee.args.defaults)
    required = {
        *(argument.arg for argument in positional[:positional_required]),
        *(
            argument.arg
            for argument, default in zip(
                callee.args.kwonlyargs,
                callee.args.kw_defaults,
                strict=True,
            )
            if default is None
        ),
    }
    assert required <= set(provided)


def _validate_extra_transport_nodes(
    extras: Counter[str],
    *,
    frozen: Counter[str],
    helper_names: Set[str],
) -> None:
    for dumped, count in extras.items():
        # Re-parse through a one-expression module is not possible for an AST
        # dump; compare the small, exact transport shapes by their dump prefix.
        if dumped.startswith("Call(func=Name(id='"):
            name = dumped.split("'", 2)[1]
            if name in helper_names | {"AllocationState", "ProtectedRestoration"}:
                continue
            if name == "bool" and any(candidate in dumped for candidate in frozen):
                continue
        if (
            dumped.startswith("Compare(left=Name(id='")
            and ("ops=[Is()]" in dumped or "ops=[IsNot()]" in dumped)
            and "comparators=[Constant(value=None)]" in dumped
        ):
            continue
        raise AssertionError((dumped, count))


def _architecture_start_pipeline(root: Path) -> ast.FunctionDef:
    source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:uquant/portfolio/pipeline.py"],
        cwd=root,
        text=True,
    )
    return _definitions(source)["_allocate_strategy"]


def expand_architecture_portfolio_pipeline(
    *,
    root: Path,
    candidate: ast.FunctionDef | None,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Expand every allocation owner back to immutable statement order."""
    overrides = architecture_portfolio_reviewed_sources(root=root, overrides=overrides)
    frozen = _architecture_start_pipeline(root)
    pipeline_source = _source(root, "uquant/portfolio/pipeline.py", overrides)
    pipeline = _definitions(pipeline_source)["_allocate_strategy"]
    if candidate is not None:
        assert ast.dump(candidate, include_attributes=False) == ast.dump(
            pipeline, include_attributes=False
        )
    sources = {
        relative: _source(root, relative, overrides) for relative, _entry, _start, _stop in _PIPELINE_STAGES
    }
    definitions = {
        name: definition for source in sources.values() for name, definition in _definitions(source).items()
    }
    definitions.update({stage[1]: _definitions(sources[stage[0]])[stage[1]] for stage in _PIPELINE_STAGES})
    admission = _definitions(_source(root, "uquant/portfolio/recovery/admission.py", overrides))[
        "_recovery_admission_targets"
    ]
    definitions["run_recovery_admission"] = admission
    known = set(definitions) | {stage[1] for stage in _PIPELINE_STAGES}
    pipeline_definitions = {"_allocate_strategy": pipeline}
    for function_name, expected_calls in _PIPELINE_CALL_GRAPH.items():
        function = pipeline_definitions.get(function_name, definitions.get(function_name))
        assert function is not None
        calls = _function_calls(function, known)
        assert tuple(call.func.id for call in calls if isinstance(call.func, ast.Name)) == expected_calls
        for call in calls:
            assert isinstance(call.func, ast.Name)
            _validate_bound_call(call, definitions[call.func.id])

    normalized_sources = {
        relative: _PipelineTransportNames().visit(copy.deepcopy(ast.parse(source, type_comments=True)))
        for relative, source in sources.items()
    }
    expanded_body = [copy.deepcopy(frozen.body[0])]
    expected_start = 1
    for relative, _entry, start, stop in _PIPELINE_STAGES:
        assert start == expected_start and stop > start
        expected_start = stop
        frozen_nodes = _economic_nodes(frozen.body[start:stop])
        tree = normalized_sources[relative]
        assert isinstance(tree, ast.Module)
        current_nodes = _economic_nodes(
            [
                statement
                for function in tree.body
                if isinstance(function, ast.FunctionDef)
                for statement in function.body
            ]
        )
        assert not (frozen_nodes - current_nodes), relative
        _validate_extra_transport_nodes(
            current_nodes - frozen_nodes,
            frozen=frozen_nodes,
            helper_names=set(_definitions(sources[relative])) | {"_recovery_admission_targets"},
        )
        expanded_body.extend(copy.deepcopy(frozen.body[start:stop]))
    assert expected_start == len(frozen.body)
    expanded = copy.deepcopy(pipeline)
    expanded.body = expanded_body
    assert ast.dump(expanded, include_attributes=False) == ast.dump(frozen, include_attributes=False)
    return expanded


_RISK_MARKET_CALL_GRAPH: Mapping[str, tuple[str, ...]] = {
    "_assess_market_and_book_evidence": ("assess_market_and_book_evidence",),
    "_transition_health": ("_update_chronic_overlay",),
    "assess_market_and_book_evidence": (
        "_present_reference_symbols",
        "_market_context",
        "_disabled_overlay_assessment",
        "_collect_reference_observations",
        "_breadth_metrics",
        "_reference_correlation",
        "_reference_context_metrics",
        "_transition_health",
        "_market_voting_state",
        "_sector_guard_state",
        "_held_book_state",
        "_apply_live_book_votes",
    ),
}
_RISK_STAGE_TRANSPORT_CALLS: Mapping[str, tuple[str, ...]] = {
    "_assess_dynamic_anchors": (
        "_healthy_anchor_basket",
        "_immediate_anchor_break",
    ),
    "_assess_break_conditions": (
        "_shock_rearmed",
        "_cohort_break_state",
        "_strategic_tail_state",
    ),
    "_assess_recovery_state": (
        "_RecoveryStateContext",
        "_credible_recovery_state",
        "_recovery_break_state",
        "_restoration_relapse_state",
        "RecoveryAssessment",
    ),
    "_observe_capital_budget": (
        "_independent_capital_damage",
        "_observed_capital_budget_level",
        "_young_strategic_cohort",
    ),
    "_update_strategic_damage_guard": (),
    "_apply_capital_overlays": (),
    "_assess_acute_and_cooldown": (
        "_AcuteContext",
        "_acute_trigger_state",
        "_acute_evacuation_assessment",
        "_capital_cooldown_assessment",
    ),
    "_assess_protected_recovery": ("assess_protected_recovery",),
    "_assess_confirmed_concentrated_break": ("assess_confirmed_concentrated_break",),
    "_resolve_risk_transition": ("resolve_risk_transition",),
}
_RISK_CALL_VALUE_ALIASES: Mapping[str, Mapping[str, str]] = {
    "_update_chronic_overlay": {"transition_damage": "damage"},
    "_reference_context_metrics": {
        "calculated": "metrics",
        "context": "reference_context",
    },
    "_transition_health": {
        "breadth60": "metrics.breadth60",
        "leader_failure": "metrics.leader_failure",
        "sector_stress": "metrics.sector_stress",
    },
    "_apply_live_book_votes": {
        "state": "voting",
        "guard": "sector_guard",
    },
}


def expand_architecture_risk_market_stage(
    *,
    root: Path,
    wrapper: ast.FunctionDef,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Project the exact market-book delegation to its immutable owner."""
    frozen_source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:uquant/risk/assessment.py"],
        cwd=root,
        text=True,
    )
    frozen = _definitions(frozen_source)["_assess_market_and_book_evidence"]
    assessment = _definitions(_source(root, "uquant/risk/assessment.py", overrides))[
        "_assess_market_and_book_evidence"
    ]
    assert ast.dump(wrapper, include_attributes=False) == ast.dump(assessment, include_attributes=False)
    market_source = _source(root, "uquant/risk/market_book.py", overrides)
    market_definitions = _definitions(market_source)
    definitions = {**market_definitions, "_assess_market_and_book_evidence": assessment}
    known = set(definitions) | {"assess_market_and_book_evidence"}
    definitions["assess_market_and_book_evidence"] = market_definitions["assess_market_and_book_evidence"]
    for name, expected_calls in _RISK_MARKET_CALL_GRAPH.items():
        calls = _function_calls(definitions[name], known)
        assert tuple(call.func.id for call in calls if isinstance(call.func, ast.Name)) == expected_calls
        for call in calls:
            assert isinstance(call.func, ast.Name)
            _validate_bound_call(
                call,
                definitions[call.func.id],
                value_aliases=_RISK_CALL_VALUE_ALIASES.get(call.func.id),
            )
    return copy.deepcopy(frozen)


def expand_architecture_risk_stage(
    *,
    root: Path,
    relative: str,
    stage_name: str,
    wrapper: ast.FunctionDef,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Project one exact risk owner split to the frozen risk surface."""
    if stage_name == "_assess_market_and_book_evidence":
        return expand_architecture_risk_market_stage(
            root=root,
            wrapper=wrapper,
            overrides=overrides,
        )
    assert stage_name in _RISK_STAGE_TRANSPORT_CALLS
    frozen_source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:{relative}"],
        cwd=root,
        text=True,
    )
    frozen = _definitions(frozen_source)[stage_name]
    current_source = _source(root, relative, overrides)
    current, is_alias = _definition_or_exact_alias(
        root,
        relative,
        current_source,
        stage_name,
    )
    assert ast.dump(wrapper, include_attributes=False) == ast.dump(current, include_attributes=False)
    if is_alias:
        assert _RISK_STAGE_TRANSPORT_CALLS[stage_name] == (
            stage_name.removeprefix("_"),
        )
        return copy.deepcopy(frozen)
    assert ast.dump(current.args, include_attributes=False) == ast.dump(frozen.args, include_attributes=False)
    assert ast.dump(current.returns, include_attributes=False) == ast.dump(
        frozen.returns, include_attributes=False
    )
    frozen_calls = Counter(
        ast.dump(node, include_attributes=False) for node in ast.walk(frozen) if isinstance(node, ast.Call)
    )
    transport_calls: list[ast.Call] = []
    for statement in current.body:
        for node in ast.walk(statement):
            if not isinstance(node, ast.Call):
                continue
            dumped = ast.dump(node, include_attributes=False)
            if frozen_calls[dumped]:
                frozen_calls[dumped] -= 1
            else:
                transport_calls.append(node)
    assert all(isinstance(call.func, ast.Name) for call in transport_calls)
    assert (
        tuple(call.func.id for call in transport_calls if isinstance(call.func, ast.Name))
        == _RISK_STAGE_TRANSPORT_CALLS[stage_name]
    )
    risk_relatives = (
        sorted(
            relative
            for relative in overrides
            if relative.startswith("uquant/risk/")
            and relative.count("/") == 2
            and relative.endswith(".py")
        )
        if overrides is not None
        else [path.relative_to(root).as_posix() for path in sorted((root / "uquant/risk").glob("*.py"))]
    )
    risk_definitions = {
        name: definition
        for relative in risk_relatives
        for name, definition in _definitions(
            _source(root, relative, overrides)
        ).items()
    }
    for call in transport_calls:
        assert isinstance(call.func, ast.Name)
        if call.func.id not in risk_definitions:
            continue
        callee = risk_definitions[call.func.id]
        assert not any(keyword.arg is None for keyword in call.keywords)
        parameters = {
            *(argument.arg for argument in callee.args.posonlyargs),
            *(argument.arg for argument in callee.args.args),
            *(argument.arg for argument in callee.args.kwonlyargs),
        }
        assert all(keyword.arg in parameters for keyword in call.keywords)
        assert len(call.args) <= len(callee.args.posonlyargs) + len(callee.args.args)
    return copy.deepcopy(frozen)


_RISK_ASSESSMENT_HELPERS = (
    "_assess_base_risk",
    "_base_recovery_stages",
    "_base_capital_stages",
    "_continuous_risk_evidence",
    "_acute_base_resolution",
    "_protected_base_resolution",
    "_confirmed_break_resolution",
    "_final_base_resolution",
)
_RISK_ASSESSMENT_TRANSPORT_GRAPH: Mapping[str, tuple[str, ...]] = {
    "_assess_base_risk": (
        "_base_recovery_stages",
        "_base_capital_stages",
        "_continuous_risk_evidence",
        "_BaseRiskContext",
        "_acute_base_resolution",
        "_protected_base_resolution",
        "_confirmed_break_resolution",
        "_final_base_resolution",
    ),
    "_base_recovery_stages": ("_BaseRecoveryStages",),
    "_base_capital_stages": ("_BaseCapitalStages",),
}
_RISK_ASSESSMENT_TRANSPORT_CALLS = Counter(
    name for names in _RISK_ASSESSMENT_TRANSPORT_GRAPH.values() for name in names
)
_RISK_ASSESSMENT_TRANSPORT_CALLS["dict"] = 1
_ANCHOR_LOCALS = {
    "symbols": "anchor_symbols",
    "groups": "anchor_groups",
    "reference_armed": "reference_anchor_armed",
    "reference_break": "reference_anchor_break",
    "break_key": "anchor_break_key",
    "immediate_reference_break": "immediate_reference_break",
}


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join((node.id, *reversed(parts)))


class _RiskAssessmentTransportNames(ast.NodeTransformer):
    def __init__(self, *, collapse_observation: bool) -> None:
        self._collapse_observation = collapse_observation

    def _local(self, node: ast.Attribute, name: str) -> ast.Name:
        return ast.copy_location(ast.Name(id=name, ctx=node.ctx), node)

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        dotted = _dotted_name(node)
        if dotted is not None:
            for prefix in ("ctx.recovery.anchor.", "recovery.anchor.", "anchor."):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    field = dotted[len(prefix) :]
                    return self._local(node, _ANCHOR_LOCALS.get(field, field))
            for prefix in (
                "ctx.recovery.breaks.",
                "recovery.breaks.",
                "breaks.",
            ):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    return self._local(node, dotted[len(prefix) :])
            for prefix in ("ctx.recovery.recovery.", "recovery.recovery."):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    return self._local(node, dotted[len(prefix) :])
            if dotted.startswith("recovery.") and "." not in dotted[9:]:
                return self._local(node, dotted[9:])
            for prefix in ("ctx.capital.observation.", "capital.observation."):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    return self._local(node, dotted[len(prefix) :])
            if self._collapse_observation and dotted.startswith("observation.") and "." not in dotted[12:]:
                return self._local(node, dotted[12:])
            for prefix in (
                "ctx.capital.overlays.",
                "capital.overlays.",
                "overlays.",
            ):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    return self._local(node, dotted[len(prefix) :])
            if dotted in {
                "ctx.capital.strategic_damage_guard",
                "capital.strategic_damage_guard",
            }:
                return self._local(node, "strategic_damage_guard")
            if dotted == "ctx.continuous":
                return self._local(node, "continuous_evidence")
            for prefix in ("ctx.market.", "market."):
                if dotted.startswith(prefix) and "." not in dotted[len(prefix) :]:
                    return self._local(node, dotted[len(prefix) :])
            if dotted.startswith("ctx.") and "." not in dotted[4:]:
                return self._local(node, dotted[4:])
            if dotted.startswith("resolution.") and "." not in dotted[11:]:
                return self._local(node, dotted[11:])
        return self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        replacements = {
            "market": "market_book",
            "acute": "transition_short_circuit",
            "protected": "protected_recovery",
            "confirmed": "confirmed_break",
            "continuous": "continuous_evidence",
            "strategic_guard": "strategic_damage_guard",
            "overlays": "capital_overlays",
        }
        if node.id in replacements:
            return ast.copy_location(ast.Name(id=replacements[node.id], ctx=node.ctx), node)
        return node


def _validate_risk_transport_call(call: ast.Call) -> None:
    assert isinstance(call.func, ast.Name)
    name = call.func.id
    if name == "_BaseRecoveryStages":
        assert not call.keywords
        assert [ast.unparse(value) for value in call.args] == [
            "anchor",
            "breaks",
            "recovery",
        ]
    elif name == "_BaseCapitalStages":
        assert not call.keywords
        assert [ast.unparse(value) for value in call.args] == [
            "observation",
            "strategic_guard",
            "overlays",
        ]
    elif name == "_BaseRiskContext":
        assert not call.args
        expected = (
            "date",
            "broad",
            "tech",
            "user_panel",
            "leaders",
            "account",
            "equity",
            "cfg",
            "market",
            "recovery",
            "capital",
            "continuous",
        )
        assert tuple(keyword.arg for keyword in call.keywords) == expected
        assert tuple(ast.unparse(keyword.value) for keyword in call.keywords) == expected
    else:
        assert name in set(_RISK_ASSESSMENT_HELPERS) - {"_assess_base_risk"}


def expand_architecture_risk_assessment(
    *,
    root: Path,
    candidate: ast.FunctionDef,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Expand exact assessment carriers back to the 85 frozen statements."""
    frozen_source = subprocess.check_output(
        ["git", "show", f"{ARCHITECTURE_REFERENCE_TREE}:uquant/risk/assessment.py"],
        cwd=root,
        text=True,
    )
    frozen = _definitions(frozen_source)["_assess_base_risk"]
    current_definitions = _definitions(_source(root, "uquant/risk/assessment.py", overrides))
    current = current_definitions["_assess_base_risk"]
    assert ast.dump(candidate, include_attributes=False) == ast.dump(current, include_attributes=False)
    assert set(_RISK_ASSESSMENT_HELPERS) <= set(current_definitions)

    transport_names = set(_RISK_ASSESSMENT_TRANSPORT_CALLS)
    for owner, expected in _RISK_ASSESSMENT_TRANSPORT_GRAPH.items():
        calls = _function_calls(current_definitions[owner], transport_names)
        assert tuple(call.func.id for call in calls if isinstance(call.func, ast.Name)) == expected
        for call in calls:
            assert isinstance(call.func, ast.Name)
            if call.func.id in current_definitions:
                _validate_bound_call(call, current_definitions[call.func.id])
            else:
                _validate_risk_transport_call(call)

    return copy.deepcopy(frozen)
