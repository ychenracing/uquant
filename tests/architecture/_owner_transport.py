"""Fail-closed owner projections for immutable risk and portfolio gates."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence, Set
from pathlib import Path

from ._governance_inventory import ARCHITECTURE_REFERENCE_TREE

# The combined allocator supersedes these internal coordination owners. Historical
# artifacts remain sealed; only the current production surface drops their paths.
RETIRED_ALLOCATION_SOURCES = frozenset(
    {
        "uquant/portfolio/allocation_closure.py",
        "uquant/portfolio/allocation_opening.py",
        "uquant/portfolio/allocation_protected.py",
        "uquant/portfolio/allocation_recovery.py",
        "uquant/portfolio/allocation_tactical.py",
        "uquant/portfolio/context.py",
        "uquant/portfolio/leaders/extensions.py",
    }
)
_RETIRED_PORTFOLIO_PRIVATE_EDGES = frozenset(
    {
        "uquant.portfolio.pipeline:uquant.portfolio.recovery.admission:_recovery_admission_targets",
    }
)

_ECONOMIC_ADDITIONS = frozenset(
    {
        "uquant/account/validation_attribution.py",
        "uquant/application/target_attribution.py",
        "uquant/attribution/validation_artifact.py",
        "uquant/attribution/validation_lots.py",
        "uquant/holding_history.py",
        "uquant/portfolio/capital.py",
        "uquant/portfolio/leaders/extensions.py",
        "uquant/portfolio/recovery/cohort_admission.py",
        "uquant/portfolio/recovery/tactical_admission.py",
        "uquant/portfolio/strategic/authority.py",
        "uquant/portfolio/strategic/grant_lifecycle.py",
        "uquant/portfolio/strategic/ownership.py",
        "uquant/portfolio/strategic/qualification_candidates.py",
        "uquant/portfolio/strategic/quorum.py",
        "uquant/portfolio/strategic/rearm.py",
        "uquant/portfolio/strategic/rearm_predicates.py",
        "uquant/models/strategic_epoch.py",
        "uquant/models/strategic_grant.py",
        "uquant/models/strategic_rearm.py",
        "uquant/models/strategic_universe.py",
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
        "uquant/models/strategic_epoch.py",
        "uquant/models/strategic_grant.py",
        "uquant/models/strategic_rearm.py",
        "uquant/models/strategic_universe.py",
    }
)
_SENTINEL_ADDITIONS = frozenset(
    {
        "uquant/account/validation_attribution.py",
        "uquant/models/strategic_epoch.py",
        "uquant/models/strategic_grant.py",
        "uquant/models/strategic_rearm.py",
        "uquant/models/strategic_universe.py",
        "uquant/risk_sentinel/history_cache.py",
        "uquant/risk_sentinel/source_identity_archive.py",
    }
)
_VALIDATION_ADDITIONS = frozenset(
    {
        "research/cross_ai_acceptance.py",
        "research/cross_ai_benchmark.py",
        "research/cross_ai_robustness.py",
        "research/cross_ai_strategy.py",
        "research/current_heads_competitor_matrix.py",
        "research/five_window_outperformance.py",
        "research/future_holdout_cli.py",
        "research/performance_diagnostic.py",
        "research/generalization_ablation_cli.py",
        "research/post_generalization_trust_closure_checkpoint_b.py",
        "research/post_generalization_trust_closure_checkpoint_c.py",
        "research/post_generalization_trust_closure_checkpoint_c_adjudication.py",
        "research/risk_counterfactual_cli.py",
        "research/risk_differential_analysis.py",
        "research/risk_differential_cli.py",
        "research/strategic_evidence/__init__.py",
        "research/strategic_evidence/absolute_policy.py",
        "research/strategic_evidence/checkpoint2_verifier.py",
        "research/strategic_evidence/contract.py",
        "research/strategic_evidence/forced_owner.py",
        "research/strategic_evidence/forced_owner_runner.py",
        "research/strategic_evidence/intervention.py",
        "research/strategic_evidence/models.py",
        "research/strategic_evidence/provenance.py",
        "research/strategic_evidence/reachability.py",
        "research/strategic_evidence/reachability_runner.py",
        "research/strategic_evidence/replay.py",
        "research/strategic_evidence/report.py",
        "research/strategic_evidence/trace.py",
        "research/strategic_evidence/witness_ablation.py",
        "research/strategic_evidence/witness_ablation_runner.py",
        "research/tencent_history_adapter.py",
        "research/window_competitor_adapter.py",
        "research/window_outperformance.py",
        "scripts/build_reproducible_wheel.py",
        "scripts/__init__.py",
        "scripts/run_absolute_generalization_acceptance.py",
        "scripts/run_strategic_evidence_closure.py",
        "scripts/run_strategic_grant_acceptance.py",
        "scripts/run_strategic_ownership_acceptance.py",
        "uquant/models/strategic_epoch.py",
        "uquant/models/strategic_grant.py",
        "uquant/models/strategic_rearm.py",
        "uquant/models/strategic_universe.py",
        "uquant/portfolio/strategic/authority.py",
        "uquant/portfolio/strategic/grant_lifecycle.py",
        "uquant/portfolio/strategic/ownership.py",
        "uquant/portfolio/strategic/qualification_candidates.py",
        "uquant/portfolio/strategic/quorum.py",
        "uquant/portfolio/strategic/rearm.py",
        "uquant/portfolio/strategic/rearm_predicates.py",
        "uquant/risk_sentinel/source_identity_archive.py",
        "uquant/validation/competitor_reference.py",
        "uquant/validation/absolute_generalization/__init__.py",
        "uquant/validation/absolute_generalization/_account_payload.py",
        "uquant/validation/absolute_generalization/_acceptance_evidence.py",
        "uquant/validation/absolute_generalization/_champion_runtime_reconciliation.py",
        "uquant/validation/absolute_generalization/champion_physical.py",
        "uquant/validation/absolute_generalization/evidence_codec.py",
        "uquant/validation/absolute_generalization/_execution_chain_reconciliation.py",
        "uquant/validation/absolute_generalization/_metric_primitives.py",
        "uquant/validation/absolute_generalization/_metrics_reconciliation.py",
        "uquant/validation/absolute_generalization/_physical_identity.py",
        "uquant/validation/absolute_generalization/_reachability_codec.py",
        "uquant/validation/absolute_generalization/_reachability_graph.py",
        "uquant/validation/absolute_generalization/_reachability_recovery.py",
        "uquant/validation/absolute_generalization/_recovery_runtime_fixtures.py",
        "uquant/validation/absolute_generalization/_replay_codec.py",
        "uquant/validation/absolute_generalization/aggregation.py",
        "uquant/validation/absolute_generalization/artifacts.py",
        "uquant/validation/absolute_generalization/contract.py",
        "uquant/validation/absolute_generalization/metrics.py",
        "uquant/validation/absolute_generalization/policy.py",
        "uquant/validation/absolute_generalization/reachability.py",
        "uquant/validation/absolute_generalization/recovery_runtime.py",
        "uquant/validation/absolute_generalization/replay.py",
        "uquant/validation/absolute_generalization/runtime.py",
        "uquant/validation/absolute_generalization/scenarios.py",
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
        "uquant/validation/statistics.py",
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
        - {
            "scripts/build_reproducible_wheel.py",
            "scripts/__init__.py",
            "scripts/run_absolute_generalization_acceptance.py",
            "scripts/run_strategic_evidence_closure.py",
            "scripts/run_strategic_grant_acceptance.py",
            "scripts/run_strategic_ownership_acceptance.py",
        }
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
    "uquant/account/migrations.py": "uquant/account/code_identity.py",
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

_RESOURCE_SURFACE_ADDITIONS: Mapping[str, frozenset[str]] = {
    "economic_decision_v1": frozenset(),
    "execution_account_v1": frozenset(),
    "sentinel_v1": frozenset(),
    "validation_runner_v1": frozenset(
        {"benchmarks/absolute_generalization_acceptance_contract.json"}
    ),
    "full_package_v1": frozenset(),
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
    return (projected | set(additions)) - RETIRED_ALLOCATION_SOURCES


def architecture_resource_surface_projection(
    identifier: str, historical: Sequence[str]
) -> list[str]:
    """Project current resource paths without changing resource bytes or protocol IDs."""

    assert identifier in _RESOURCE_SURFACE_ADDITIONS
    projected = {_CURRENT_RESOURCE_PATHS.get(path, path) for path in historical}
    return sorted(projected | _RESOURCE_SURFACE_ADDITIONS[identifier])


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
    """Prove retained public-owner moves and explicitly retired pipeline edges."""
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
    else:
        validate_combined_allocator_topology(root=root, overrides=overrides)
        expected_missing.update(_RETIRED_PORTFOLIO_PRIVATE_EDGES)
    assert not (set(observed) - set(expected)) and missing == expected_missing
    for legacy, (owner, public, private, importer, imported_module) in moves.items():
        _alias_is_exact(_source(root, owner, overrides), public, private)
        _imports_exact_public(
            _source(root, importer, overrides),
            imported_module=imported_module,
            public=public,
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


def _assert_transfer_feasibility_order(definitions: Mapping[str, ast.FunctionDef]) -> None:
    """A prospective release may authorize only a reduction, never funded entry."""
    transfer = definitions["_degraded_transfer"]
    expected = ast.parse('''
remaining = max(0.0, proposed.get(weakest, 0.0) - self.cfg.replacement_transfer_cap)
released = weights_now[weakest] - remaining
detail = diagnostics if diagnostics is not None else {}
detail.update(released_weight=released, required_weight=self.cfg.core_admission_weight)
if released + 1e-12 < self.cfg.min_trade_weight:
    detail["block"] = "TRANSFER_BELOW_TRADE_MINIMUM"
    return None
feasible = funded_increment(
    cfg=self.cfg, symbol=challenger, desired=self.cfg.core_admission_weight,
    current=weights_now.get(challenger, 0.0),
    committed={**committed, weakest: remaining}, cash_room=cash_room + released,
    leaders=leaders, user_panel=user_panel, date=date, gross_cap=gross_cap,
    diagnostics=detail,
)
if feasible + 1e-12 < self.cfg.core_admission_weight:
    detail["block"] = "TRANSFER_CANNOT_FUND_ADMISSION"
    return None
detail["block"] = "FEASIBLE_AFTER_SETTLEMENT"
proposed[weakest] = remaining
''').body
    starts = [index for index, statement in enumerate(transfer.body)
              if isinstance(statement, ast.Assign)
              and [ast.unparse(target) for target in statement.targets] == ["remaining"]]
    assert len(starts) == 1
    observed = transfer.body[starts[0]:starts[0] + len(expected)]
    assert [ast.dump(statement) for statement in observed] == [ast.dump(statement) for statement in expected]
    writes = [ast.unparse(node) for node in ast.walk(transfer)
              if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del))
              and ast.unparse(node.value) in {"proposed", "committed"}]
    assert writes == ["proposed[weakest]"]
    assert not any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                   and node.id in {"committed", "cash_room", "gross_cap"} for node in ast.walk(transfer))
    mutators = {"clear", "pop", "popitem", "setdefault", "update", "__setitem__", "__delitem__"}
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func.value) in {"committed", "proposed"} and node.func.attr in mutators
        for node in ast.walk(transfer)
    )
    admission = definitions["_admit_new_cores"]
    expected_admission = ast.parse('''
weak = _degraded_transfer(
    book.policy, challenger=symbol, proposed=book.proposed, weights_now=book.weights_now,
    leaders=book.leaders, user_panel=book.user_panel, date=book.date, account=book.account,
    committed=book.committed, cash_room=book.cash_room, gross_cap=book.gross_cap,
    diagnostics=book.record(symbol).setdefault("transfer_budget", {}),
)
if weak is not None:
    book.reasons[weak] = "leader rotation: bounded transfer after confirmed deterioration"
    book.mechanisms[weak] = AttributionMechanism.LEADER_ROTATION
    book.record(weak)["allocation_reason"] = "CONFIRMED_BOUNDED_TRANSFER"
    book.record(symbol)["entry_gate"] = "AWAIT_REDUCTION_SETTLEMENT"
    break
''').body
    loops = [statement for statement in admission.body if isinstance(statement, ast.For)]
    assert len(loops) == 1
    assert [ast.dump(statement) for statement in loops[0].body[-2:]] == [
        ast.dump(statement) for statement in expected_admission
    ]
    for node in ast.walk(admission):
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            assert not ast.unparse(node).startswith(("book.cash_room", "book.committed", "book.proposed"))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) in {"book.committed", "book.proposed"}):
            assert node.func.attr not in mutators


def validate_combined_allocator_topology(
    *,
    root: Path,
    overrides: Mapping[str, str] | None = None,
) -> ast.FunctionDef:
    """Validate current ownership without claiming the retired strategy is AST-exact.

    Runtime capital, risk and next-open fill invariants are exercised separately;
    this gate prevents split books, misrouted authority and settlement side effects.
    """
    reviewed = architecture_portfolio_reviewed_sources(root=root, overrides=overrides)
    assert RETIRED_ALLOCATION_SOURCES.isdisjoint(reviewed)
    retired_modules = {Path(path).stem for path in RETIRED_ALLOCATION_SOURCES}
    for source in reviewed.values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[-1] not in retired_modules

    source = reviewed["uquant/portfolio/pipeline.py"]
    _alias_is_exact(source, "allocate_strategy", "_allocate_strategy")
    definitions = _definitions(source)
    _assert_transfer_feasibility_order(definitions)
    pipeline = definitions["_allocate_strategy"]
    calls = [node for node in ast.walk(pipeline) if isinstance(node, ast.Call)]
    for owner, receiver, method, arguments in (
        (pipeline, "self", "_strategic_cohort_targets", {name: name for name in ("risk", "account", "prices", "weights_now")}),
        (definitions["_book_targets"], "book.policy", "_targets", {name: f"book.{name}" for name in ("proposed", "leaders", "account")}),
        (pipeline, "self", "_frozen_existing_targets", {name: name for name in ("strategy_targets", "leaders", "account", "weights_now")}
         | {"strategy_targets": "targets"}),
    ):
        selected = [
            call
            for call in ast.walk(owner)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and ast.unparse(call.func.value) == receiver
            and call.func.attr == method
        ]
        assert len(selected) == 1, method
        keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in selected[0].keywords}
        assert all(keywords.get(argument) == value for argument, value in arguments.items()), method
    books = [call for call in calls if ast.unparse(call.func) == "_AllocationBook"]
    assert len(books) == 1 and not books[0].keywords
    assert tuple(ast.unparse(argument) for argument in books[0].args) == (
        "self", "date", "risk", "user_panel", "leaders", "account", "prices", "weights_now",
        "owned", "strategic_targets", "proposed", "committed", "cash_room",
    )
    for helper in ("_fund_strategic_owners", "_ordinary_exits", "_pending_intents",
                   "_restore_ordinary_holdings", "_admit_new_cores", "_book_targets"):
        selected = [call for call in calls if ast.unparse(call.func) == helper]
        assert len(selected) == 1
        assert [ast.unparse(argument) for argument in selected[0].args] == ["book"]
    returns = [node for node in ast.walk(pipeline) if isinstance(node, ast.Return)]
    assert len(returns) == 1 and returns[0].value is not None
    assert ast.unparse(returns[0].value) == "targets"
    capital = _definitions(reviewed["uquant/portfolio/capital.py"])
    for relative, level, helpers in (
        ("uquant/portfolio/pipeline.py", 1, {
            "committed_capital": {"account": "account", "prices": "prices", "proposed": "proposed"},
            "funded_increment": {
                "cfg": "self.policy.cfg", "symbol": "symbol", "desired": "desired", "current": "current",
                "committed": "self.committed", "cash_room": "self.cash_room", "leaders": "self.leaders",
                "user_panel": "self.user_panel", "date": "self.date", "gross_cap": "self.gross_cap",
                "symbol_cap": "symbol_cap", "concentration_cap": "concentration_cap",
                "diagnostics": "diagnostic",
            },
        }),
        ("uquant/portfolio/strategic/ownership.py", 2, {
            "committed_capital": {"account": "account", "prices": "prices", "proposed": "weights"},
            "admission_room": {
                "cfg": "self.cfg", "symbol": "symbol", "committed": "committed", "leaders": "leaders",
                "user_panel": "user_panel", "date": "date", "gross_cap": "min(self.cfg.max_gross, risk.target_gross_cap)",
            },
        }),
    ):
        tree = ast.parse(reviewed[relative])
        for helper, arguments in helpers.items():
            assert helper in capital and helper not in _definitions(reviewed[relative])
            imports = [
                (node.level, node.module, alias.asname)
                for node in tree.body if isinstance(node, ast.ImportFrom)
                for alias in node.names if alias.name == helper
            ]
            assert imports == [(level, "capital", None)]
            helper_calls = [
                call for call in ast.walk(tree)
                if isinstance(call, ast.Call) and ast.unparse(call.func) == helper
            ]
            assert helper_calls
            if helper == "funded_increment":
                transfer = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                                and node.name == "_degraded_transfer")
                book_type = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                                 and node.name == "_AllocationBook")
                fund = next(node for node in book_type.body if isinstance(node, ast.FunctionDef)
                            and node.name == "fund")
                # Match calls by their actual owning AST, not just argument shape.
                actual_funding = [call for call in ast.walk(fund) if call in helper_calls]
                transfer_funding = [call for call in ast.walk(transfer) if call in helper_calls]
                assert len(actual_funding) == len(transfer_funding) == 1
                assert set(helper_calls) == {*actual_funding, *transfer_funding}
            for call in helper_calls:
                keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
                if helper == "funded_increment" and call in transfer_funding:
                    expected = {
                        "cfg": "self.cfg", "symbol": "challenger", "desired": "self.cfg.core_admission_weight",
                        "current": "weights_now.get(challenger, 0.0)",
                        "committed": "{**committed, weakest: remaining}", "cash_room": "cash_room + released",
                        **{name: name for name in ("leaders", "user_panel", "date", "gross_cap")},
                        "diagnostics": "detail",
                    }
                    assert not call.args and keywords == expected
                elif helper == "funded_increment":
                    assert not call.args and keywords == arguments
                else:
                    assert all(keywords.get(name) == value for name, value in arguments.items()), helper
    funding_calls = [call for call in ast.walk(capital["funded_increment"])
                     if isinstance(call, ast.Call) and ast.unparse(call.func) == "admission_room"]
    assert len(funding_calls) == 1
    funding_arguments = {keyword.arg: ast.unparse(keyword.value) for keyword in funding_calls[0].keywords}
    assert funding_arguments == {
        **{name: name for name in ("cfg", "symbol", "leaders", "user_panel", "date", "gross_cap", "symbol_cap", "concentration_cap")},
        "committed": "{**committed, symbol: current}", "diagnostics": "detail",
    }
    book = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "_AllocationBook")
    gross_cap = next(node for node in book.body if isinstance(node, ast.FunctionDef) and node.name == "gross_cap")
    assert len(gross_cap.body) == 1 and isinstance(gross_cap.body[0], ast.Return)
    assert ast.unparse(gross_cap.body[0].value) == "min(self.policy.cfg.max_gross, self.risk.target_gross_cap)"
    authority_calls = [
        call for call in calls if ast.unparse(call.func) == "assess_strategic_capital_authority"
    ]
    assert authority_calls
    assert all([ast.unparse(argument) for argument in call.args] == ["account"] for call in authority_calls)
    settled_fields = tuple(f"{owner}.{field}" for owner in ("account", "book.account", "self.account")
                           for field in ("cash", "positions", "pending_orders"))
    mutation_methods = {"append", "clear", "extend", "insert", "pop", "remove", "setdefault", "update"}
    capital_sources = (
        source,
        reviewed["uquant/portfolio/capital.py"],
        reviewed["uquant/portfolio/strategic/ownership.py"],
    )
    for node in (node for text in capital_sources for node in ast.walk(ast.parse(text))):
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            target = ast.unparse(node)
            assert not target.startswith(settled_fields)
            assert target not in {"position.shares", "position.avg_cost"}
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value).startswith(settled_fields)
        ):
            assert node.func.attr not in mutation_methods
    return copy.deepcopy(pipeline)


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


def _assert_current_holding_protection_surface(
    *, root: Path, overrides: Mapping[str, str] | None,
) -> None:
    """Assert the current protection semantics before projecting historical topology."""
    contract_path = root / "benchmarks/cross_ai_core_strategy_contract.json"
    contract_bytes = contract_path.read_bytes()
    assert hashlib.sha256(contract_bytes).hexdigest() == (
        "9ec5992df69d4466cb2b26cea0e67bbe93f4c6317ba5b8a500ca7b89a75d78b4"
    )
    assert json.loads(contract_bytes)["contract_id"] == "cross-ai-core-strategy-20260905-v1"
    # These are current executable expectations, not a replacement historical blob.
    # Matching the whole facts module also prevents new imports, account writes,
    # risk caps or target construction from acquiring authority in this helper.
    expected_history = ast.parse('''
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .types import AccountState

def holding_spans_date(account: AccountState, symbol: str, boundary: str) -> bool:
    position = account.positions.get(symbol)
    if position is None or position.shares <= 0 or not boundary or not position.entry_date:
        return False
    if position.entry_date <= boundary:
        return True
    shares = position.shares
    for fill in reversed(account.fills):
        if fill.symbol != symbol:
            continue
        shares += fill.shares if fill.side == "SELL" else -fill.shares
        if shares == 0:
            return fill.fill_date <= boundary
        if shares < 0:
            return False
    return False

def protected_weights_for_current_episode(account: AccountState) -> dict[str, float]:
    strategic = (
        set(account.protected_weight_epoch_ids)
        | set(account.strategic_cohort_symbols)
        | set(account.strategic_cohort_targets)
        | {s for s, p in account.positions.items() if p.grant_id or p.epoch_id}
        | {o.symbol for o in account.pending_orders if o.grant_id or o.epoch_id}
    )
    return {
        symbol: weight for symbol, weight in account.protected_weights.items()
        if weight > 0 and (symbol in strategic or holding_spans_date(account, symbol, account.last_shock_date))
    }
''')
    history = ast.parse(_source(root, "uquant/holding_history.py", overrides))
    for node in (history, *[node for node in history.body if isinstance(node, ast.FunctionDef)]):
        if ast.get_docstring(node) is not None:
            node.body.pop(0)
    assert ast.dump(history) == ast.dump(expected_history)
    expected_capture = _definitions('''
def capture_protected_holdings(
    *, account: AccountState, date: pd.Timestamp, user_panel: dict[str, pd.DataFrame],
    equity: float, use_anchors: bool = True,
) -> None:
    retained = (
        {} if account.candidate_tenure.get("post_shock_restore_complete", 0) == 1
        else protected_weights_for_current_episode(account)
    )
    if use_anchors and not retained:
        retained = dict(account.anchor_weights)
    for symbol, position in account.positions.items():
        if symbol in user_panel and date in user_panel[symbol].index and position.shares > 0:
            retained.setdefault(symbol, position.shares * scalar(user_panel[symbol].loc[date], "close") / equity)
    account.protected_weights = retained
    account.candidate_tenure["post_shock_restore_complete"] = 0
''')["capture_protected_holdings"]
    capture = _definitions(
        _source(root, "uquant/risk/protected_recovery.py", overrides)
    )["capture_protected_holdings"]
    if ast.get_docstring(capture) is not None:
        capture.body.pop(0)
    assert ast.dump(capture) == ast.dump(expected_capture)
    for path in ("uquant/risk/protected_recovery.py", "uquant/risk/transitions.py"):
        tree = ast.parse(_source(root, path, overrides))
        imported = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and any((alias.asname or alias.name) == "protected_weights_for_current_episode"
                            for alias in node.names)]
        assert len(imported) == 1
        assert imported[0].level == 2 and imported[0].module == "holding_history"
        assert any(alias.name == "protected_weights_for_current_episode" and alias.asname is None
                   for alias in imported[0].names)
        assert not any(
            (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
             and node.id == "protected_weights_for_current_episode")
            or (isinstance(node, ast.FunctionDef) and node.name == "protected_weights_for_current_episode")
            for node in ast.walk(tree)
        )

    # Expand the same asserted capture into all three callers with their exact
    # context bindings. Anchor fallback intentionally differs for a new crisis.
    for path, function_name, reset, use_anchors in (
        ("uquant/risk/confirmed_break.py", "_prepare_confirmed_break", "reset_recovery_owner_rearm", True),
        ("uquant/risk/transitions.py", "_acute_evacuation_assessment", "_reset_recovery_owner_rearm", True),
        ("uquant/risk/transition_resolution.py", "_prepare_new_crisis", "reset_recovery_owner_rearm", False),
    ):
        tree = ast.parse(_source(root, path, overrides))
        imported = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                    and any(alias.name == "capture_protected_holdings" for alias in node.names)]
        assert len(imported) == 1
        assert imported[0].level == 1 and imported[0].module == "protected_recovery"
        assert any(alias.name == "capture_protected_holdings" and alias.asname is None
                   for alias in imported[0].names)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "capture_protected_holdings"]
        assert len(calls) == 1
        expected_call = ast.parse(
            "capture_protected_holdings(account=account, date=ctx.date, "
            "user_panel=ctx.user_panel, equity=ctx.equity"
            + (")" if use_anchors else ", use_anchors=False)"), mode="eval",
        ).body
        assert ast.dump(calls[0]) == ast.dump(expected_call)
        function = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}[function_name]
        call_index = next(index for index, statement in enumerate(function.body)
                          if isinstance(statement, ast.Expr) and statement.value is calls[0])
        assert ast.unparse(function.body[call_index - 1]) == f"{reset}(account)"
        assert [ast.unparse(statement) for statement in function.body[call_index + 1:call_index + 3]] == [
            "account.shock_start_date = str(ctx.date.date())",
            "account.last_shock_date = str(ctx.date.date())",
        ]


def _current_holding_predicate_projection(stage: ast.FunctionDef) -> ast.FunctionDef:
    """Project only the two explicitly reviewed predicates onto historical topology."""
    current = copy.deepcopy(stage)
    predicates = [node for node in ast.walk(current) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == "protected_weights_for_current_episode"]
    assert len(predicates) == 2
    expected = ast.parse("protected_weights_for_current_episode(account)", mode="eval").body
    assert all(ast.dump(predicate) == ast.dump(expected) for predicate in predicates)

    class HistoricalPredicate(ast.NodeTransformer):
        def visit_Call(self, node: ast.Call) -> ast.expr:
            if node in predicates:
                return ast.parse("account.protected_weights", mode="eval").body
            return self.generic_visit(node)

    HistoricalPredicate().visit(current)
    return current


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
    if stage_name == "_assess_break_conditions":
        _assert_current_holding_protection_surface(root=root, overrides=overrides)
        current = _current_holding_predicate_projection(current)
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
