from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import io
import json
import subprocess
import sys

import pytest

from uquant.contracts.strict_json import canonical_json_sha256

from ._analysis import (
    _TASK7_RELOCATED_FUNCTION_DEBT,
    _TASK7_RELOCATED_PRIVATE_IMPORTS,
    FINAL_BUDGETS,
    MODULE_AUTHORITIES,
    ROOT,
    architecture_snapshot,
    measured_debt,
)
from ._task7_immutable_trace import assert_trace_seals, immutable_trace_from_archive
from ._task7_ownership import (
    OrchestrationCall,
    StageSlice,
    assert_stage_call,
    ast_dump,
    field_unpacks,
    normalized_stage_statements,
    same_fields,
    same_keywords,
)
from ._task7_risk_trace import _RISK_ACCOUNT_FIELDS, risk_trace_replay

_TASK7_START = "36bc6968ee61eb578a8f19ee132aecb9b03fe7ca"
_TASK7_START_TREE = "3cc640cf565e116aa524466485dc7d9e1b511538"
_RISK_BLOB = "96aeaaba421098ed2ec22a045e0b7d7e6da9396b"
_RISK_SHA256 = "74dd564b300e0b48e2a788c7be289e98bb033cf97d5b806c585f04e087ed36dd"
_RISK_BYTES = 94_481
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task7_cleanup_inventory.json"
_DAILY_TRACE = ROOT / "benchmarks" / "task7_daily_risk_trace.json"
_TRACE_RUNNER = ROOT / "tests" / "architecture" / "_task7_risk_trace.py"
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
_TASK8_PORTFOLIO_PACKAGE_PATHS = {
    "uquant/portfolio/__init__.py",
    "uquant/portfolio/allocator.py",
    "uquant/portfolio/context.py",
    "uquant/portfolio/freeze.py",
    "uquant/portfolio/leaders/__init__.py",
    "uquant/portfolio/leaders/admission.py",
    "uquant/portfolio/leaders/lifecycle.py",
    "uquant/portfolio/leaders/targets.py",
    "uquant/portfolio/pipeline.py",
    "uquant/portfolio/risk_reduction.py",
}

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
        ["git", "show", f"{_TASK7_START}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _immutable_python_sources() -> dict[str, bytes]:
    paths = [
        path
        for path in subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", _TASK7_START],
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
        input="".join(f"{_TASK7_START}:{path}\n" for path in paths).encode(),
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


def _assert_task7_ownership_surface(overrides: dict[str, str] | None = None) -> None:
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))[
        "_assess_base_risk"
    ]
    candidate_definitions: dict[str, ast.FunctionDef] = {}
    for stage_name, spec in _STAGE_SLICES.items():
        definitions = _top_level_definitions(
            ast.parse(_stage_source(spec.path, overrides), filename=spec.path)
        )
        stage = definitions[stage_name]
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


def test_task7_inventory_is_bound_to_the_immutable_risk_blob_and_consumers() -> None:
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == _TASK7_START
    assert payload["baseline_tree"] == _TASK7_START_TREE
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
            _TASK7_START,
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


def test_task7_source_surface_migration_is_exact_and_requirements_stay_bound() -> None:
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
            expected.update(_TASK8_PORTFOLIO_PACKAGE_PATHS)
        assert set(candidate[identifier]["source_paths"]) == expected
        assert candidate[identifier]["resource_paths"] == immutable[identifier]["resource_paths"]
    assert (ROOT / "requirements.txt").read_bytes() == _git_source("requirements.txt")


@pytest.fixture(scope="module")
def immutable_task7_trace(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    return immutable_trace_from_archive(
        root=ROOT,
        destination=tmp_path_factory.mktemp("task7-immutable-trace") / "snapshot",
        baseline_commit=_TASK7_START,
        risk_sha256=_RISK_SHA256,
        risk_size=_RISK_BYTES,
        runner=_TRACE_RUNNER,
        runner_sha256=_TRACE_RUNNER_SHA256,
        logic_commit=_TRACE_LOGIC_COMMIT,
        logic_blob=_TRACE_LOGIC_BLOB,
    )


def test_task7_daily_risk_trace_is_sealed_and_bound_to_the_immutable_start(
    immutable_task7_trace: dict[str, object],
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    assert_trace_seals(
        payload,
        baseline_commit=_TASK7_START,
        baseline_tree=_TASK7_START_TREE,
        account_fields=_RISK_ACCOUNT_FIELDS,
    )
    assert payload == immutable_task7_trace


def test_task7_resigned_trace_tamper_is_rejected(
    immutable_task7_trace: dict[str, object],
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
        baseline_commit=_TASK7_START,
        baseline_tree=_TASK7_START_TREE,
        account_fields=_RISK_ACCOUNT_FIELDS,
    )
    with pytest.raises(AssertionError):
        assert payload == immutable_task7_trace


@pytest.mark.parametrize("scenario_index", range(3))
def test_task7_daily_risk_control_and_ordered_account_trace_are_exact(
    scenario_index: int,
) -> None:
    payload = json.loads(_DAILY_TRACE.read_text(encoding="utf-8"))
    expected = payload["scenarios"][scenario_index]
    observed = risk_trace_replay(
        name=expected["name"],
        start=expected["requested_start"],
        end=expected["requested_end"],
        symbols=tuple(expected["symbols"]),
        root=ROOT,
    )
    records = [
        {
            "date": record["date"],
            "ordered_checkpoint_sha256": record["ordered_checkpoint_sha256"],
        }
        for record in observed["records"]
    ]
    assert records == expected["records"]
    assert canonical_json_sha256(records) == expected["records_sha256"]


def test_task7_real_owners_replace_the_risk_monolith_and_keep_a_thin_facade() -> None:
    assert not (ROOT / "uquant/risk.py").exists()
    assert all((ROOT / path).is_file() for path in _RISK_PACKAGE_PATHS)
    assert len((ROOT / "uquant/risk/__init__.py").read_text(encoding="utf-8").splitlines()) < 120


def test_task7_moved_helper_bodies_are_exactly_bound_to_immutable_source() -> None:
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))
    for name, owner in _MOVED_HELPER_OWNERS.items():
        candidate = _top_level_definitions(
            ast.parse((ROOT / owner).read_text(encoding="utf-8"), filename=owner)
        )
        assert ast.dump(_normalized_definition(candidate[name]), include_attributes=False) == ast.dump(
            _normalized_definition(immutable[name]), include_attributes=False
        )


def test_task7_ownership_slices_are_real_and_assessment_order_is_fixed() -> None:
    _assert_task7_ownership_surface()


@pytest.mark.parametrize("kind", ("compare", "threshold"))
def test_task7_ownership_slice_gate_rejects_economic_mutation(kind: str) -> None:
    with pytest.raises(AssertionError):
        _assert_task7_ownership_surface(
            {"uquant/risk/capital.py": _mutated_capital_stage(kind)}
        )


def test_task7_private_and_complexity_relocations_are_exact_and_fail_closed() -> None:
    candidate = architecture_snapshot()
    graph = candidate["import_graph"]
    functions = candidate["functions"]
    assert isinstance(graph, dict)
    assert isinstance(functions, list)
    relocated = graph["task7_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    assert isinstance(relocated, list)
    assert isinstance(ordinary, list)
    assert {str(row["id"]) for row in relocated} == _TASK7_RELOCATED_PRIVATE_IMPORTS
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
    assert observed_debt == set(_TASK7_RELOCATED_FUNCTION_DEBT)
    assert set(_TASK7_RELOCATED_FUNCTION_DEBT.values()) == {"uquant.risk:_assess_base_risk"}

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
    }
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
    assert base["lines"] == 391

    source_texts = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "uquant").rglob("*.py")
    }
    source_texts["uquant/risk/assessment.py"] += (
        "\nfrom .capital import _unreviewed_task7_edge\n\n"
        "def _unreviewed_task7_debt() -> int:\n"
        + "".join(f"    value = {index}\n" for index in range(121))
        + "    return value\n"
    )
    mutation = architecture_snapshot(source_texts=source_texts)
    mutation_graph = mutation["import_graph"]
    assert isinstance(mutation_graph, dict)
    assert "uquant.risk.assessment:uquant.risk.capital:_unreviewed_task7_edge" in {
        str(row["id"]) for row in mutation_graph["cross_module_private_imports"]
    }
    mutation_debt = measured_debt(mutation)
    assert "uquant.risk:_unreviewed_task7_debt" in {str(row["id"]) for row in mutation_debt["long_functions"]}


def test_task7_facade_preserves_consumed_names_reflection_and_live_anchor_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uquant.risk as risk_module

    assert set(vars(risk_module)) >= _COMPATIBILITY_NAMES
    immutable = _top_level_definitions(ast.parse(_git_source("uquant/risk.py")))
    inventory = json.loads(_INVENTORY.read_text(encoding="utf-8"))["entries"][0]
    reflection = inventory["reflection_contract"]
    for name in immutable:
        function = getattr(risk_module, name)
        assert function.__module__ == "uquant.risk"
        assert function.__name__ == name
        assert function.__qualname__ == name
        assert str(inspect.signature(function)) == reflection[name]["signature"]
        assert function.__doc__ == reflection[name]["raw_docstring"]

    observed: list[bool] = []
    original = risk_module._update_dynamic_anchors

    def capture(*args: object, **kwargs: object) -> object:
        observed.append(bool(kwargs["allow_reanchor"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(risk_module, "_update_dynamic_anchors", capture)
    # Existing focused risk tests exercise the full path; this gate pins the
    # facade lookup itself so owner imports cannot silently capture a stale function.
    assert risk_module._risk_runtime_seam("_update_dynamic_anchors") is capture
    assert observed == []


def test_task7_risk_package_has_no_reverse_owner_or_platform_imports() -> None:
    forbidden = {
        "fcntl",
        "research",
        "scripts",
        "tests",
        "uquant.account",
        "uquant.application",
        "uquant.execution",
        "uquant.portfolio",
        "uquant.validation",
    }
    for path in sorted((ROOT / "uquant/risk").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module)
        assert not any(
            name == blocked or name.startswith(f"{blocked}.") for name in imports for blocked in forbidden
        ), (path, imports)

    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, dict)
    cycles = graph["cycles"]
    assert isinstance(cycles, list)
    assert len(cycles) <= 2
    assert all(
        not any(
            str(module) == "uquant.risk" or str(module).startswith("uquant.risk.")
            for module in cycle["modules"]
        )
        for cycle in cycles
    )


def test_task7_risk_imports_under_optimized_and_windows_style_smoke() -> None:
    command = (
        "import builtins; real=builtins.__import__; "
        "builtins.__import__=lambda name,*a,**k: "
        "(_ for _ in ()).throw(ImportError('blocked fcntl')) "
        "if name=='fcntl' else real(name,*a,**k); "
        "import uquant.risk; assert uquant.risk.assess_risk.__module__=='uquant.risk'"
    )
    completed = subprocess.run(
        [sys.executable, "-OO", "-c", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
