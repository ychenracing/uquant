# ruff: noqa: E402, F401, I001
from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.contracts.strict_json import canonical_json_sha256
from uquant.types import Risk
from uquant.validation.absolute_generalization._acceptance_evidence import current_candidate_contract

from ._analysis import (
    _RISK_RELOCATED_FUNCTION_DEBT,
    _RISK_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS,
    MODULE_AUTHORITIES,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._risk_trace_reference import assert_trace_seals, immutable_trace_from_archive
from ._risk_ownership import (
    OrchestrationCall,
    StageSlice,
    assert_stage_call,
    ast_dump,
    field_unpacks,
    normalized_stage_statements,
    same_fields,
    same_keywords,
)
from ._risk_trace import _RISK_ACCOUNT_FIELDS, risk_trace_replay
from ._owner_transport import (
    ARCHITECTURE_SOURCE_SURFACE_ADDITIONS,
    architecture_resource_surface_projection,
    expand_architecture_risk_assessment,
    expand_architecture_risk_stage,
    architecture_private_relocation_projection,
    architecture_risk_reviewed_sources,
    architecture_source_surface_projection,
)
from ._risk_debt_transport import (
    architecture_risk_function_debt_projection,
    architecture_risk_historical_authorities,
    architecture_risk_historical_base_lines,
)
from ._validation_relocation import (
    GENERALIZATION_OWNERS,
    HOLDOUT_LANES_FACADE,
    HOLDOUT_OWNERS,
    HOLDOUT_RUNTIME_FACADE,
    POLICY_OWNERS,
)

_RISK_REFERENCE_COMMIT = "36bc6968ee61eb578a8f19ee132aecb9b03fe7ca"
_RISK_REFERENCE_TREE = "3cc640cf565e116aa524466485dc7d9e1b511538"
_RISK_BLOB = "96aeaaba421098ed2ec22a045e0b7d7e6da9396b"
_RISK_SHA256 = "74dd564b300e0b48e2a788c7be289e98bb033cf97d5b806c585f04e087ed36dd"
_RISK_BYTES = 94_481
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task7_cleanup_inventory.json"
_DAILY_TRACE = ROOT / "benchmarks" / "daily_risk_behavior_reference.json"
_TRACE_RUNNER = ROOT / "tests" / "architecture" / "_risk_trace.py"
_TRACE_RUNNER_SHA256 = "cc81e38b79296746d473406be8a649657a8a35efa42cbe7b8b845dd9767d5a2f"
_TRACE_LOGIC_COMMIT = "13feebe2f68fda0815a3cf507c3d7e15b4c5db14"
_TRACE_LOGIC_BLOB = "81c805b8bc39d30b86911484ee266dec156260be"
_RISK_PACKAGE_PATHS = {
    "uquant/risk/__init__.py",
    "uquant/risk/anchors.py",
    "uquant/risk/assessment.py",
    "uquant/risk/capital.py",
    "uquant/risk/recovery_state.py",
    "uquant/risk/strategic_guard.py",
    "uquant/risk/transitions.py",
}
_PORTFOLIO_PORTFOLIO_PACKAGE_PATHS = {
    "uquant/portfolio/__init__.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/context.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/leaders/__init__.py",
    "uquant/portfolio/leaders/admission.py",
    "uquant/portfolio/leaders/lifecycle.py",
    "uquant/portfolio/leaders/targets.py",
    "uquant/portfolio/pipeline.py",
    "uquant/portfolio/recovery/__init__.py",
    "uquant/portfolio/recovery/admission.py",
    "uquant/portfolio/recovery/substitution.py",
    "uquant/portfolio/recovery/targets.py",
    "uquant/portfolio/risk_reduction.py",
    "uquant/portfolio/strategic/__init__.py",
    "uquant/portfolio/strategic/discovery.py",
    "uquant/portfolio/strategic/lifecycle.py",
    "uquant/portfolio/strategic/targets.py",
}
_REGISTERED_ABSOLUTE_GENERALIZATION_OWNER_MODULES = frozenset(
    path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
    for path in ARCHITECTURE_SOURCE_SURFACE_ADDITIONS["validation_runner_v1"]
    if path.startswith("uquant/validation/absolute_generalization/")
    or path == "uquant/validation/statistics.py"
)
_ABSOLUTE_GENERALIZATION_OWNER_MODULES = frozenset(
    {
        "uquant.validation.absolute_generalization",
        "uquant.validation.absolute_generalization._account_payload",
        "uquant.validation.absolute_generalization._acceptance_evidence",
        "uquant.validation.absolute_generalization._champion_runtime_reconciliation",
        "uquant.validation.absolute_generalization._execution_chain_reconciliation",
        "uquant.validation.absolute_generalization._metric_primitives",
        "uquant.validation.absolute_generalization._metrics_reconciliation",
        "uquant.validation.absolute_generalization._physical_identity",
        "uquant.validation.absolute_generalization._reachability_codec",
        "uquant.validation.absolute_generalization._reachability_graph",
        "uquant.validation.absolute_generalization._reachability_recovery",
        "uquant.validation.absolute_generalization._recovery_runtime_fixtures",
        "uquant.validation.absolute_generalization._replay_codec",
        "uquant.validation.absolute_generalization.aggregation",
        "uquant.validation.absolute_generalization.artifacts",
        "uquant.validation.absolute_generalization.champion_physical",
        "uquant.validation.absolute_generalization.contract",
        "uquant.validation.absolute_generalization.evidence_codec",
        "uquant.validation.absolute_generalization.metrics",
        "uquant.validation.absolute_generalization.policy",
        "uquant.validation.absolute_generalization.reachability",
        "uquant.validation.absolute_generalization.recovery_runtime",
        "uquant.validation.absolute_generalization.replay",
        "uquant.validation.absolute_generalization.runtime",
        "uquant.validation.absolute_generalization.scenarios",
        "uquant.validation.statistics",
    }
)
_VALIDATION_NEW_OWNER_MODULES = frozenset(
    path.removesuffix("/__init__.py").removesuffix(".py").replace("/", ".")
    for path in (*GENERALIZATION_OWNERS, *POLICY_OWNERS, *HOLDOUT_OWNERS)
) - {
    "uquant.validation.generalization",
    "uquant.validation.generalization_reference",
    "uquant.validation.holdout",
} | _ABSOLUTE_GENERALIZATION_OWNER_MODULES | {"uquant.risk_sentinel.provenance"}
_MOVED_HELPER_OWNERS = {
    "_acute_sector_evacuation_required": "uquant/risk/transitions.py",
    "_reset_recovery_owner_rearm": "uquant/risk/recovery_state.py",
    "_strategic_grace_supported": "uquant/risk/strategic_guard.py",
    "_strategic_damage_guard_required": "uquant/risk/strategic_guard.py",
    "_strategic_damage_guard_persists": "uquant/risk/strategic_guard.py",
    "_strategic_guard_level2_overlay_required": "uquant/risk/strategic_guard.py",
    "_strategic_damage_guard_active": "uquant/risk/strategic_guard.py",
    "_persistent_crisis_cap": "uquant/risk/recovery_state.py",
    "_strategic_crisis_severity": "uquant/risk/strategic_guard.py",
    "_dynamic_anchor_candidate": "uquant/risk/anchors.py",
    "_update_dynamic_anchors": "uquant/risk/anchors.py",
    "_portfolio_drawdowns": "uquant/risk/capital.py",
    "_update_capital_budget_ladder": "uquant/risk/capital.py",
    "_capital_budget_repair_drawdown_confirmed": "uquant/risk/capital.py",
}

_COMPATIBILITY_NAMES = {
    "REFERENCE_ANCHORS",
    "_acute_sector_evacuation_required",
    "_assess_base_risk",
    "_capital_budget_repair_drawdown_confirmed",
    "_dynamic_anchor_candidate",
    "_evidence_family_votes",
    "_persistent_crisis_cap",
    "_portfolio_drawdowns",
    "_reset_recovery_owner_rearm",
    "_strategic_crisis_severity",
    "_strategic_damage_guard_active",
    "_strategic_damage_guard_persists",
    "_strategic_damage_guard_required",
    "_strategic_grace_supported",
    "_strategic_guard_level2_overlay_required",
    "_update_capital_budget_ladder",
    "_update_dynamic_anchors",
    "assess_risk",
    "build_base_market_family_snapshot",
}


_MARKET_BOOK_FIELDS = same_fields(
    "market_context average_fast declining below sector_stress correlation vol_ratio "
    "leader_failure operating_dd capital_dd tech_speed broad_speed transition_damage "
    "trend_health breadth20 breadth60 declining_name declining_group below_name below_group "
    "reasons family_votes votes sector_guard held_damage held_ret5 held_damage_ratio "
    "held_loss_ratio held_repair_ratio"
)
_ANCHOR_FIELDS = (
    ("anchor_symbols", "symbols"),
    ("anchor_groups", "groups"),
    ("reference_anchor_armed", "reference_armed"),
    ("reference_anchor_break", "reference_break"),
    ("anchor_break_key", "break_key"),
    ("immediate_reference_break", "immediate_reference_break"),
)
_BREAK_FIELDS = same_fields(
    "shock_rearmed concentrated_structure_break emergency_tail_break narrow_anchor_guard "
    "immediate_severe_break persistent_market_break strategic_active recovery_anchor_elapsed "
    "held_cohort_break_confirmed strategic_current_gross strategic_tail_break"
)
_RECOVERY_FIELDS = same_fields(
    "credible_reserve incomplete_universe_tail_break reference_anchor_confirmed "
    "capital_impaired_restoration_relapse market_backed_restoration_relapse "
    "terminal_market_backed_restoration_relapse capital_drawdown_relapse concentrated_confirmed"
)
_CAPITAL_OBSERVATION_FIELDS = same_fields(
    "independent_damage worsening_damage observed_budget_level"
)
_CAPITAL_OVERLAY_FIELDS = same_fields(
    "strategic_guard_level2_overlay freeze_new_risk overlay_cap overlay_reduction_level"
)
_TRANSITION_FIELDS = same_fields("state shock cap sector_guard_forced observation")

_STAGE_SLICES = {
    "_assess_market_and_book_evidence": StageSlice(
        "uquant/risk/assessment.py",
        3,
        78,
        terminal_constructor="MarketBookEvidence",
        terminal_fields=_MARKET_BOOK_FIELDS,
    ),
    "_assess_dynamic_anchors": StageSlice(
        "uquant/risk/anchors.py",
        78,
        91,
        terminal_constructor="AnchorAssessment",
        terminal_fields=(
            ("symbols", "anchor_symbols"),
            ("groups", "anchor_groups"),
            ("reference_armed", "reference_anchor_armed"),
            ("reference_break", "reference_anchor_break"),
            ("break_key", "anchor_break_key"),
            ("immediate_reference_break", "immediate_reference_break"),
        ),
        transport="live_anchor_callee",
    ),
    "_assess_break_conditions": StageSlice(
        "uquant/risk/transitions.py",
        91,
        117,
        terminal_constructor="BreakConditions",
        terminal_fields=_BREAK_FIELDS,
    ),
    "_assess_recovery_state": StageSlice(
        "uquant/risk/recovery_state.py",
        117,
        136,
        terminal_constructor="RecoveryAssessment",
        terminal_fields=_RECOVERY_FIELDS,
    ),
    "_observe_capital_budget": StageSlice(
        "uquant/risk/capital.py",
        136,
        140,
        terminal_constructor="CapitalObservation",
        terminal_fields=_CAPITAL_OBSERVATION_FIELDS,
    ),
    "_update_strategic_damage_guard": StageSlice(
        "uquant/risk/strategic_guard.py",
        140,
        144,
        terminal_expression="strategic_damage_guard",
        transport="operating_drawdown_parameter",
    ),
    "_apply_capital_overlays": StageSlice(
        "uquant/risk/capital.py",
        144,
        153,
        terminal_constructor="CapitalOverlays",
        terminal_fields=_CAPITAL_OVERLAY_FIELDS,
    ),
    "_assess_acute_and_cooldown": StageSlice(
        "uquant/risk/transitions.py",
        154,
        162,
        terminal_expression="(previous, acute_sector_evacuation)",
    ),
    "_assess_protected_recovery": StageSlice(
        "uquant/risk/recovery_state.py",
        162,
        178,
        terminal_expression="None",
    ),
    "_assess_confirmed_concentrated_break": StageSlice(
        "uquant/risk/transitions.py",
        178,
        179,
        terminal_expression="None",
    ),
    "_resolve_risk_transition": StageSlice(
        "uquant/risk/transitions.py",
        179,
        202,
        terminal_constructor="RiskTransitionResolution",
        terminal_fields=_TRANSITION_FIELDS,
    ),
}

_ORCHESTRATION_CALLS = {
    "_assess_market_and_book_evidence": OrchestrationCall(
        3,
        "market_book",
        same_keywords(
            "date broad tech reference_panel reference_returns user_panel leaders account equity "
            "cfg reference_context"
        ),
        (
            "if isinstance(market_book, RiskAssessment):\n    return market_book",
            *field_unpacks("market_book", _MARKET_BOOK_FIELDS),
        ),
    ),
    "_assess_dynamic_anchors": OrchestrationCall(
        34,
        "anchor_assessment",
        (
            *same_keywords(
                "date reference_panel leaders account cfg transition_damage votes"
            ),
            (
                "update_dynamic_anchors",
                "cast(Callable[..., tuple[str, ...]], "
                "_risk_runtime_seam('_update_dynamic_anchors'))",
            ),
        ),
        field_unpacks("anchor_assessment", _ANCHOR_FIELDS),
    ),
    "_assess_break_conditions": OrchestrationCall(
        41,
        "break_conditions",
        same_keywords(
            "date tech user_panel account equity cfg held_damage held_damage_ratio held_ret5 "
            "operating_dd votes sector_stress transition_damage market_context"
        ),
        field_unpacks("break_conditions", _BREAK_FIELDS),
    ),
    "_assess_recovery_state": OrchestrationCall(
        53,
        "recovery_assessment",
        same_keywords(
            "date tech user_panel leaders account equity cfg shock_rearmed strategic_active "
            "operating_dd capital_dd recovery_anchor_elapsed emergency_tail_break "
            "concentrated_structure_break immediate_severe_break persistent_market_break "
            "reference_anchor_armed held_damage_ratio votes sector_stress immediate_reference_break "
            "anchor_break_key held_cohort_break_confirmed strategic_tail_break"
        ),
        field_unpacks("recovery_assessment", _RECOVERY_FIELDS),
    ),
    "_observe_capital_budget": OrchestrationCall(
        62,
        "capital_observation",
        same_keywords(
            "account cfg sector_guard reference_anchor_break held_damage_ratio transition_damage "
            "votes capital_dd operating_dd sector_stress strategic_active"
        ),
        field_unpacks(
            "capital_observation",
            (
                ("independent_damage", "independent_damage"),
                ("observed_budget_level", "observed_budget_level"),
            ),
        ),
    ),
    "_update_strategic_damage_guard": OrchestrationCall(
        65,
        "strategic_damage_guard",
        (
            ("account", "account"),
            ("operating_drawdown", "operating_dd"),
            ("transition_damage", "transition_damage"),
            ("votes", "votes"),
            ("cfg", "cfg"),
        ),
        (),
    ),
    "_apply_capital_overlays": OrchestrationCall(
        66,
        "capital_overlays",
        same_keywords(
            "account cfg observed_budget_level transition_damage votes held_damage_ratio capital_dd "
            "operating_dd strategic_damage_guard"
        ),
        field_unpacks("capital_overlays", _CAPITAL_OVERLAY_FIELDS),
    ),
    "_assess_acute_and_cooldown": OrchestrationCall(
        72,
        "transition_short_circuit",
        same_keywords(
            "date user_panel account equity cfg market_context sector_guard concentrated_confirmed "
            "held_ret5 votes continuous_evidence average_fast declining below sector_stress "
            "correlation vol_ratio leader_failure held_damage_ratio held_loss_ratio "
            "held_repair_ratio tech_speed broad_speed operating_dd capital_dd strategic_active "
            "strategic_current_gross"
        ),
        (
            "if isinstance(transition_short_circuit, RiskAssessment):\n"
            "    return transition_short_circuit",
            "previous, acute_sector_evacuation = transition_short_circuit",
        ),
    ),
    "_assess_protected_recovery": OrchestrationCall(
        75,
        "protected_recovery",
        same_keywords(
            "date broad tech user_panel leaders account equity cfg previous votes continuous_evidence "
            "market_context average_fast declining below sector_stress correlation vol_ratio "
            "leader_failure held_damage_ratio held_repair_ratio tech_speed broad_speed operating_dd "
            "capital_dd credible_reserve freeze_new_risk overlay_cap overlay_reduction_level "
            "sector_guard shock_rearmed strategic_active"
        ),
        ("if protected_recovery is not None:\n    return protected_recovery",),
    ),
    "_assess_confirmed_concentrated_break": OrchestrationCall(
        77,
        "confirmed_break",
        same_keywords(
            "date user_panel leaders account equity cfg previous concentrated_confirmed votes "
            "continuous_evidence market_context average_fast declining below sector_stress "
            "correlation vol_ratio leader_failure held_damage_ratio held_repair_ratio held_ret5 "
            "tech_speed broad_speed operating_dd capital_dd strategic_active "
            "strategic_current_gross overlay_cap credible_reserve "
            "capital_impaired_restoration_relapse market_backed_restoration_relapse "
            "terminal_market_backed_restoration_relapse incomplete_universe_tail_break "
            "reference_anchor_confirmed held_cohort_break_confirmed capital_drawdown_relapse "
            "immediate_reference_break"
        ),
        ("if confirmed_break is not None:\n    return confirmed_break",),
    ),
    "_resolve_risk_transition": OrchestrationCall(
        79,
        "transition_resolution",
        same_keywords(
            "date user_panel account equity cfg previous shock_rearmed capital_dd votes sector_stress "
            "narrow_anchor_guard operating_dd independent_damage reasons sector_guard held_ret5 "
            "credible_reserve strategic_active overlay_cap"
        ),
        field_unpacks(
            "transition_resolution",
            (
                ("state", "state"),
                ("shock", "shock"),
                ("cap", "cap"),
                ("observation", "observation"),
            ),
        ),
    ),
}


def _git_source(path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{_RISK_REFERENCE_TREE}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _immutable_python_sources() -> dict[str, bytes]:
    paths = [
        path
        for path in subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", _RISK_REFERENCE_TREE],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if path.endswith(".py")
    ]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=ROOT,
        input="".join(f"{_RISK_REFERENCE_TREE}:{path}\n" for path in paths).encode(),
        check=True,
        capture_output=True,
    ).stdout
    stream = io.BytesIO(batch)
    sources: dict[str, bytes] = {}
    for path in paths:
        header = stream.readline().decode("ascii").split()
        assert len(header) == 3 and header[1] == "blob"
        size = int(header[2])
        sources[path] = stream.read(size)
        assert stream.read(1) == b"\n"
    assert not stream.read()
    return sources


def _immutable_module(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolved_import_from(path: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module = _immutable_module(path)
    package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - (node.level - 1))]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _immutable_import_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, object]]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[dict[str, object]] = []
    for path, source in sources.items():
        symbols: list[str] = []
        seen: set[str] = set()
        tree = ast.parse(source, filename=path)
        imports = sorted(
            (node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in imports:
            if isinstance(node, ast.Import):
                candidates = (f"{leaf} module" for alias in node.names if alias.name == target)
            else:
                imported_from = _resolved_import_from(path, node)
                candidates = (
                    alias.name if imported_from == target else f"{leaf} module"
                    for alias in node.names
                    if imported_from == target or (imported_from == parent and alias.name == leaf)
                )
            for symbol in candidates:
                if symbol not in seen:
                    seen.add(symbol)
                    symbols.append(symbol)
        if symbols:
            consumers.append({"path": path, "symbols": symbols})
    return consumers


def _immutable_module_attribute_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, object]]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[dict[str, object]] = []
    for path, source in sources.items():
        tree = ast.parse(source, filename=path)
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update(
                    alias.asname for alias in node.names if alias.name == target and alias.asname is not None
                )
            elif isinstance(node, ast.ImportFrom) and _resolved_import_from(path, node) == parent:
                aliases.update(alias.asname or alias.name for alias in node.names if alias.name == leaf)
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        }
        attributes.update(
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in aliases
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and (
                (isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr", "delattr"})
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"getattr", "setattr", "delattr"}
                )
            )
        )
        attributes = sorted(attributes)
        if attributes:
            consumers.append({"path": path, "attributes": attributes})
    return consumers


def _immutable_dotted_identity_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, object]]:
    consumers: list[dict[str, object]] = []
    for path, source in sources.items():
        values = sorted(
            {
                node.value
                for node in ast.walk(ast.parse(source, filename=path))
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (node.value == target or node.value.startswith(f"{target}."))
            }
        )
        if values:
            consumers.append({"path": path, "values": values})
    return consumers


def _top_level_definitions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _normalized_definition(node: ast.FunctionDef) -> ast.FunctionDef:
    normalized = copy.deepcopy(node)
    normalized.decorator_list = []
    return normalized


def _stage_source(path: str, overrides: dict[str, str] | None) -> str:
    if overrides is not None and path in overrides:
        return overrides[path]
    return (ROOT / path).read_text(encoding="utf-8")


def _assert_risk_ownership_surface(overrides: dict[str, str] | None = None) -> None:
    holding_override = {} if overrides is None else {
        path: source for path, source in overrides.items() if path == "uquant/holding_history.py"
    }
    risk_overrides = None if overrides is None else {
        path: source for path, source in overrides.items() if path != "uquant/holding_history.py"
    }
    overrides = architecture_risk_reviewed_sources(root=ROOT, overrides=risk_overrides)
    overrides.update(holding_override)
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))[
        "_assess_base_risk"
    ]
    candidate_definitions: dict[str, ast.FunctionDef] = {}
    for stage_name, spec in _STAGE_SLICES.items():
        definitions = _top_level_definitions(
            ast.parse(_stage_source(spec.path, overrides), filename=spec.path)
        )
        if stage_name in definitions:
            stage = definitions[stage_name]
        else:
            source_tree = ast.parse(
                _stage_source(spec.path, overrides),
                filename=spec.path,
            )
            aliases = [
                node
                for node in source_tree.body
                if isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == stage_name
                and isinstance(node.value, ast.Name)
            ]
            assert len(aliases) == 1
            public = aliases[0].value.id
            imports = [
                node
                for node in source_tree.body
                if isinstance(node, ast.ImportFrom)
                and any(
                    alias.name == public and alias.asname is None
                    for alias in node.names
                )
            ]
            assert len(imports) == 1 and imports[0].level == 1 and imports[0].module
            imported_path = (
                Path(spec.path).parent
                / f"{imports[0].module.replace('.', '/')}.py"
            ).as_posix()
            stage = _top_level_definitions(
                ast.parse(_stage_source(imported_path, overrides), filename=imported_path)
            )[public]
        stage = expand_architecture_risk_stage(
            root=ROOT,
            relative=spec.path,
            stage_name=stage_name,
            wrapper=stage,
            overrides=overrides,
        )
        candidate_definitions[stage_name] = stage
        statements = normalized_stage_statements(stage, spec)
        expected = immutable.body[spec.source_start : spec.source_stop]
        assert len(statements) == len(expected)
        for offset, (observed, source) in enumerate(zip(statements, expected, strict=True)):
            assert ast_dump(observed) == ast_dump(source), (
                stage_name,
                spec.source_start + offset,
            )

    assessment_path = "uquant/risk/assessment.py"
    assessment = _top_level_definitions(
        ast.parse(_stage_source(assessment_path, overrides), filename=assessment_path)
    )["_assess_base_risk"]
    assessment = expand_architecture_risk_assessment(
        root=ROOT,
        candidate=assessment,
        overrides=overrides,
    )
    assert len(assessment.body) == 85
    residual = {0: 0, 1: 1, 2: 2, 71: 153, 84: 202}
    for candidate_index, source_index in residual.items():
        assert ast_dump(assessment.body[candidate_index]) == ast_dump(
            immutable.body[source_index]
        )

    covered = set(residual)
    for stage_name, call_spec in _ORCHESTRATION_CALLS.items():
        stage = candidate_definitions[stage_name]
        assert stage.args.posonlyargs == []
        assert stage.args.args == []
        assert stage.args.vararg is None
        assert stage.args.kwarg is None
        assert tuple(argument.arg for argument in stage.args.kwonlyargs) == tuple(
            keyword for keyword, _ in call_spec.keywords
        )
        assert all(default is None for default in stage.args.kw_defaults)
        call_coverage = assert_stage_call(
            body=assessment.body,
            stage_name=stage_name,
            spec=call_spec,
        )
        assert not covered & call_coverage
        covered.update(call_coverage)
    assert covered == set(range(len(assessment.body)))


def _mutated_capital_stage(kind: str) -> str:
    path = ROOT / "uquant/risk/capital.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = _top_level_definitions(tree)["_observe_capital_budget"]
    mutated = False
    if kind == "compare":
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Compare)
                and not mutated
                and ast.unparse(node) == "held_damage_ratio >= cfg.concentrated_break_ratio"
            ):
                node.ops[0] = ast.Gt()
                mutated = True
    elif kind == "threshold":
        for node in ast.walk(function):
            if isinstance(node, ast.Constant) and not mutated and node.value == 0.68:
                node.value = 0.67
                mutated = True
    else:
        raise AssertionError(f"unknown mutation kind: {kind}")
    assert mutated
    return ast.unparse(tree)


def test_risk_inventory_is_bound_to_the_immutable_risk_blob_and_consumers() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == _RISK_REFERENCE_COMMIT
    assert payload["baseline_tree"] == _RISK_REFERENCE_TREE
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["path"] == "uquant/risk.py"
    assert entry["git_blob_sha1"] == _RISK_BLOB
    source = subprocess.run(
        ["git", "cat-file", "blob", _RISK_BLOB],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert len(source) == entry["size_bytes"] == _RISK_BYTES
    assert hashlib.sha256(source).hexdigest() == entry["content_sha256"] == _RISK_SHA256

    references = subprocess.run(
        [
            "git",
            "grep",
            "-l",
            "--fixed-strings",
            "uquant/risk.py",
            _RISK_REFERENCE_TREE,
            "--",
            ".",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    observed = {line.split(":", 1)[1] for line in references}
    live = entry["live_references"]
    assert observed == set(live["immutable_fixed_path_consumers"])

    sources = _immutable_python_sources()
    assert _immutable_import_consumers(sources, "uquant.risk") == live["ast_import_consumers"]
    assert (
        _immutable_module_attribute_consumers(sources, "uquant.risk")
        == live["runtime_module_attribute_consumers"]
    )
    assert (
        _immutable_dotted_identity_consumers(sources, "uquant.risk")
        == live["dotted_runtime_identity_consumers"]
    )

    immutable_registry = json.loads(_git_source("benchmarks/source_surface_registry.json"))
    expected_surfaces = [
        surface["id"]
        for surface in immutable_registry["surfaces"]
        if "uquant/risk.py" in surface["source_paths"]
    ]
    assert expected_surfaces == live["current_source_surface_registry"]


def test_risk_source_surface_migration_is_exact_and_requirements_stay_bound() -> None:
    immutable_registry = json.loads(_git_source("benchmarks/source_surface_registry.json"))
    candidate_registry = json.loads(
        (ROOT / "benchmarks/source_surface_registry.json").read_text(encoding="utf-8")
    )
    assert candidate_registry["canonical_sha256"] == canonical_json_sha256(
        {key: value for key, value in candidate_registry.items() if key != "canonical_sha256"}
    )
    immutable = {surface["id"]: surface for surface in immutable_registry["surfaces"]}
    candidate = {surface["id"]: surface for surface in candidate_registry["surfaces"]}
    identifiers = (
        "economic_decision_v1",
        "execution_account_v1",
        "sentinel_v1",
        "validation_runner_v1",
        "full_package_v1",
    )
    assert tuple(candidate) == identifiers
    for identifier in identifiers:
        expected = set(immutable[identifier]["source_paths"])
        if "uquant/risk.py" in expected:
            expected.remove("uquant/risk.py")
            expected.update(_RISK_PACKAGE_PATHS)
        if "uquant/portfolio.py" in expected:
            expected.remove("uquant/portfolio.py")
            expected.update(_PORTFOLIO_PORTFOLIO_PACKAGE_PATHS)
        if "uquant/validation/generalization.py" in expected:
            expected.remove("uquant/validation/generalization.py")
            expected.update(GENERALIZATION_OWNERS)
        if "uquant/validation/generalization_reference.py" in expected:
            expected.update(POLICY_OWNERS)
        if "uquant/validation/holdout.py" in expected:
            expected.remove("uquant/validation/holdout.py")
            expected.update(HOLDOUT_OWNERS)
        if HOLDOUT_RUNTIME_FACADE in expected:
            expected.update(HOLDOUT_OWNERS[5:])
        if HOLDOUT_LANES_FACADE in expected:
            expected.add(HOLDOUT_OWNERS[4])
        if {
            "uquant/risk_sentinel/cli.py",
            "uquant/risk_sentinel/validation.py",
        } & expected:
            expected.add("uquant/risk_sentinel/provenance.py")
        expected = architecture_source_surface_projection(identifier, expected)
        assert set(candidate[identifier]["source_paths"]) == expected
        assert candidate[identifier]["resource_paths"] == (
            architecture_resource_surface_projection(
                identifier,
                immutable[identifier]["resource_paths"]
            )
        )
    assert (ROOT / "requirements.txt").read_bytes() == _git_source("requirements.txt")


@pytest.fixture(scope="module")
def immutable_risk_trace(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    return immutable_trace_from_archive(
        root=ROOT,
        destination=tmp_path_factory.mktemp("risk-immutable-trace") / "snapshot",
        baseline_commit=_RISK_REFERENCE_COMMIT,
        baseline_tree=_RISK_REFERENCE_TREE,
        risk_sha256=_RISK_SHA256,
        risk_size=_RISK_BYTES,
        runner=_TRACE_RUNNER,
        runner_sha256=_TRACE_RUNNER_SHA256,
        logic_blob=_TRACE_LOGIC_BLOB,
    )


def test_risk_daily_risk_trace_is_sealed_and_bound_to_the_immutable_start(
    immutable_risk_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    assert_trace_seals(
        payload,
        baseline_commit=_RISK_REFERENCE_COMMIT,
        baseline_tree=_RISK_REFERENCE_TREE,
        account_fields=_RISK_ACCOUNT_FIELDS,
    )
    assert payload == immutable_risk_trace


def test_risk_resigned_trace_tamper_is_rejected(
    immutable_risk_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    payload["scenarios"][0]["records"][0]["ordered_checkpoint_sha256"] = "0" * 64
    payload["scenarios"][0]["records_sha256"] = canonical_json_sha256(
        payload["scenarios"][0]["records"]
    )
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    payload["payload_sha256"] = canonical_json_sha256(unsigned)
    assert_trace_seals(
        payload,
        baseline_commit=_RISK_REFERENCE_COMMIT,
        baseline_tree=_RISK_REFERENCE_TREE,
        account_fields=_RISK_ACCOUNT_FIELDS,
    )
    with pytest.raises(AssertionError):
        assert payload == immutable_risk_trace


@pytest.mark.parametrize("scenario_index", range(3))
def test_risk_current_trace_preserves_sessions_controls_and_checkpoint_integrity(
    scenario_index: int,
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    expected = payload["scenarios"][scenario_index]
    contract = current_candidate_contract()
    assert contract["superseded_behavior_contracts"][0] == (
        "Exact old Target/Order/Fill and exclusive economic-owner/epoch trajectories."
    )
    assert expected["requested_end"] < contract["future_holdout_boundary"]
    observed = risk_trace_replay(
        name=expected["name"],
        start=expected["requested_start"],
        end=expected["requested_end"],
        symbols=tuple(expected["symbols"]),
        root=ROOT,
    )
    # Account-dependent risk checkpoints change with the authorized allocation
    # paths. The immutable oracle above still proves the historical values.
    for field in ("name", "requested_start", "requested_end", "symbols"):
        assert observed[field] == expected[field]
    records = observed["records"]
    assert [record["date"] for record in records] == [
        record["date"] for record in expected["records"]
    ]
    assert observed["risk_account_fields"] == list(_RISK_ACCOUNT_FIELDS)
    assert observed["records_sha256"] == canonical_json_sha256(records)
    for record in records:
        control = record["control"]
        assert control["state"] in {state.value for state in Risk}
        assert 0.0 <= control["target_gross_cap"] <= DEFAULT_CONFIG.max_gross
        assert type(control["freeze_new_risk"]) is bool
        assert type(control["votes"]) is int and control["votes"] >= 0
        assert type(control["reduction_level"]) is int and control["reduction_level"] >= 0
        for field in (
            "account_before_sha256", "assessment_sha256", "account_after_sha256",
            "ordered_checkpoint_sha256",
        ):
            digest = record[field]
            assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")


def test_risk_real_owners_replace_the_risk_monolith_and_keep_a_thin_facade() -> None:
    assert not (ROOT / "uquant/risk.py").exists()
    assert all((ROOT / path).is_file() for path in _RISK_PACKAGE_PATHS)
    assert len((ROOT / "uquant/risk/__init__.py").read_text(encoding="utf-8").splitlines()) < 120


def test_risk_moved_helper_bodies_are_exactly_bound_to_immutable_source() -> None:
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))
    # Shared capital must not extend a cohort's grace to unrelated holdings.
    # Project only this reviewed narrowing; every other helper statement stays exact.
    grace_return = immutable["_strategic_grace_supported"].body[-1]
    assert isinstance(grace_return, ast.Return) and isinstance(grace_return.value, ast.Call)
    grace = grace_return.value.args[0]
    assert isinstance(grace, ast.BoolOp) and isinstance(grace.op, ast.And)
    grace.values.append(ast.parse(
        "all(symbol in account.strategic_cohort_symbols "
        "for symbol, position in account.positions.items() if position.shares > 0)",
        mode="eval",
    ).body)
    for name, owner in _MOVED_HELPER_OWNERS.items():
        candidate = _top_level_definitions(
            ast.parse((ROOT / owner).read_text(encoding="utf-8"), filename=owner)
        )
        assert ast.dump(_normalized_definition(candidate[name]), include_attributes=False) == ast.dump(
            _normalized_definition(immutable[name]), include_attributes=False
        )


def test_risk_ownership_slices_are_real_and_assessment_order_is_fixed() -> None:
    _assert_risk_ownership_surface()


@pytest.mark.parametrize("path, before, after", (
    ("uquant/holding_history.py", "if position.entry_date <= boundary:", "if True:"),
    ("uquant/holding_history.py", "position = account.positions.get(symbol)",
     "account.max_exposure = 1.0\n    position = account.positions.get(symbol)"),
    ("uquant/risk/protected_recovery.py", "retained.setdefault(symbol,", "retained.update(symbol,"),
    ("uquant/risk/protected_recovery.py", "else protected_weights_for_current_episode(account)",
     "else account.protected_weights"),
    ("uquant/risk/protected_recovery.py", "from ..holding_history import", "from ..other_history import"),
    ("uquant/risk/confirmed_break.py", "equity=ctx.equity,", "equity=ctx.equity, use_anchors=False,"),
    ("uquant/risk/transition_resolution.py", "use_anchors=False", "use_anchors=True"),
    ("uquant/risk/transitions.py", "account=account, date=ctx.date, user_panel=ctx.user_panel, equity=ctx.equity,",
     "account=account, date=ctx.date, user_panel=ctx.reference_panel, equity=ctx.equity,"),
    ("uquant/risk/transitions.py", "concentrated_break = shock_rearmed and not protected_weights_for_current_episode(account)",
     "concentrated_break = shock_rearmed and not account.protected_weights"),
))
def test_current_holding_protection_gate_rejects_semantic_escape(
    path: str, before: str, after: str,
) -> None:
    source = _stage_source(path, None)
    assert before in source
    with pytest.raises(AssertionError):
        _assert_risk_ownership_surface({path: source.replace(before, after, 1)})


@pytest.mark.parametrize("kind", ("compare", "threshold"))
def test_risk_ownership_slice_gate_rejects_economic_mutation(kind: str) -> None:
    with pytest.raises(AssertionError):
        _assert_risk_ownership_surface(
            {"uquant/risk/capital.py": _mutated_capital_stage(kind)}
        )


def test_risk_private_and_complexity_relocations_are_exact_and_fail_closed() -> None:
    assert (
        _REGISTERED_ABSOLUTE_GENERALIZATION_OWNER_MODULES
        == _ABSOLUTE_GENERALIZATION_OWNER_MODULES
    )
    assert {
        "uquant.validation.generalization.baseline",
        "uquant.validation.generalization.gates",
        "uquant.validation.generalization.metrics",
        "uquant.validation.generalization.models",
        "uquant.validation.generalization.provenance",
        "uquant.validation.generalization.runner",
        "uquant.validation.generalization.scenarios",
        "uquant.validation.generalization_policy",
        "uquant.validation.generalization_policy.cells",
        "uquant.validation.generalization_policy.evaluator",
        "uquant.validation.generalization_policy.projection",
        "uquant.validation.generalization_policy.schema",
        "uquant.validation.holdout.artifact_transaction",
        "uquant.validation.holdout.checkpoints",
        "uquant.validation.holdout.contract",
        "uquant.validation.holdout.lanes",
        "uquant.validation.holdout.manifest",
        "uquant.validation.holdout.replay",
        "uquant.validation.holdout.service",
        "uquant.validation.holdout.snapshots",
        "uquant.validation.holdout.source_identity",
        "uquant.risk_sentinel.provenance",
    } | _ABSOLUTE_GENERALIZATION_OWNER_MODULES == _VALIDATION_NEW_OWNER_MODULES
    assert "uquant.validation.absolute_generalization.unreviewed" not in (
        _VALIDATION_NEW_OWNER_MODULES
    )
    assert "uquant.validation.generalization.unreviewed" not in _VALIDATION_NEW_OWNER_MODULES
    assert "uquant.validation.holdout.unreviewed" not in _VALIDATION_NEW_OWNER_MODULES
    candidate = architecture_snapshot()
    graph = candidate["import_graph"]
    functions = candidate["functions"]
    assert isinstance(graph, dict)
    assert isinstance(functions, list)
    relocated = graph["task7_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    assert isinstance(relocated, list)
    assert isinstance(ordinary, list)
    assert architecture_private_relocation_projection(
        root=ROOT,
        task=7,
        observed={str(row["id"]) for row in relocated},
        expected=set(_RISK_RELOCATED_PRIVATE_IMPORTS),
    ) == _RISK_RELOCATED_PRIVATE_IMPORTS
    assert not {
        str(row["id"])
        for row in ordinary
        if str(row["importer"]) == "uquant.risk"
        or str(row["importer"]).startswith("uquant.risk.")
        or str(row["imported_from"]) == "uquant.risk"
        or str(row["imported_from"]).startswith("uquant.risk.")
    }

    observed_debt = {
        str(row["id"])
        for row in functions
        if str(row["module"]).startswith("uquant.risk.")
        and (
            int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
            or int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
        )
    }
    assert architecture_risk_function_debt_projection(
        root=ROOT,
        observed=observed_debt,
        expected=set(_RISK_RELOCATED_FUNCTION_DEBT),
        function_rows=functions,
    ) == set(_RISK_RELOCATED_FUNCTION_DEBT)
    assert set(_RISK_RELOCATED_FUNCTION_DEBT.values()) == {"uquant.risk:_assess_base_risk"}

    immutable_sources = {
        path: source.decode("utf-8")
        for path, source in _immutable_python_sources().items()
        if path.startswith("uquant/")
    }
    immutable_authorities = {
        module: authority
        for module, authority in MODULE_AUTHORITIES.items()
        if not module.startswith("uquant.risk.")
        and not module.startswith("uquant.portfolio.")
        and module not in _VALIDATION_NEW_OWNER_MODULES
    }
    immutable_authorities = architecture_risk_historical_authorities(
        immutable_authorities,
        set(immutable_sources),
    )
    immutable = architecture_snapshot(
        source_texts=immutable_sources,
        module_authorities=immutable_authorities,
    )
    immutable_functions = immutable["functions"]
    assert isinstance(immutable_functions, list)
    legacy = next(row for row in immutable_functions if row["id"] == "uquant.risk:_assess_base_risk")
    assert legacy["lines"] == 1_802
    for row in functions:
        if row["id"] in observed_debt:
            assert int(row["lines"]) < int(legacy["lines"])
            assert int(row["branch_points"]) < int(legacy["branch_points"])
    base = next(row for row in functions if row["id"] == "uquant.risk.assessment:_assess_base_risk")
    assert architecture_risk_historical_base_lines(root=ROOT, current_row=base) == 391

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/risk/assessment.py"] += (
        "\nfrom .capital import _unreviewed_risk_edge\n\n"
        "def _unreviewed_risk_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert "uquant.risk.assessment:uquant.risk.capital:_unreviewed_risk_edge" in {
        str(row["id"]) for row in mutation_graph["cross_module_private_imports"]
    }
    mutation_debt = measured_debt(mutation)
    assert "uquant.risk:_unreviewed_risk_debt" in {
        str(row["id"]) for row in mutation_debt["long_functions"]
    }


from ._risk_import_boundaries import (
    test_risk_facade_preserves_consumed_names_reflection_and_live_anchor_seam,
    test_risk_package_has_no_reverse_owner_or_platform_imports,
    test_risk_imports_under_optimized_and_windows_style_smoke,
)
