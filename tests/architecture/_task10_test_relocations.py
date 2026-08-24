"""Fail-closed transport for the Task 10 governed-test relocation inventory."""

from __future__ import annotations

import ast
import copy
import hashlib
import subprocess
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from ._task10_inventory import semantic_units

_TASK5_REVIEW_COMMIT = "5cca5a3f1cc1866d15c86d674098f800c1420995"

TEST_RELOCATION_PATHS: Mapping[str, tuple[str, ...]] = {
    "tests/architecture/_analysis.py": (
        "tests/architecture/_analysis.py",
        "tests/architecture/_analysis_authorities.py",
        "tests/architecture/_analysis_relocations.py",
        "tests/architecture/_analysis_debt.py",
    ),
    "tests/architecture/_task3_baseline.py": (
        "tests/architecture/_task3_baseline.py",
        "tests/architecture/_task3_validation_runtime.py",
    ),
    "tests/architecture/test_task7_risk_boundaries.py": (
        "tests/architecture/test_task7_risk_boundaries.py",
        "tests/architecture/_task7_risk_import_boundaries.py",
    ),
    "tests/test_attribution_identity.py": (
        "tests/test_attribution_identity.py",
        "tests/_attribution_identity_retention_cases.py",
        "tests/_attribution_identity_reconciliation_cases.py",
        "tests/_attribution_identity_schema_cases.py",
    ),
    "tests/test_engine_contracts.py": (
        "tests/test_engine_contracts.py",
        "tests/_engine_account_and_metrics_cases.py",
        "tests/_engine_decision_state_cases.py",
    ),
    "tests/test_engineering_gate_edges.py": (
        "tests/test_engineering_gate_edges.py",
        "tests/_engineering_holdout_observation_cases.py",
        "tests/_engineering_provenance_universe_cases.py",
    ),
    "tests/test_execution.py": (
        "tests/test_execution.py",
        "tests/_execution_order_lifecycle_cases.py",
        "tests/_execution_risk_and_fill_cases.py",
    ),
    "tests/test_future_holdout_runtime.py": (
        "tests/test_future_holdout_runtime.py",
        "tests/_future_holdout_transaction_recovery_cases.py",
        "tests/_future_holdout_carrier_identity_cases.py",
        "tests/_future_holdout_replay_binding_cases.py",
    ),
    "tests/test_generalization.py": (
        "tests/test_generalization.py",
        "tests/_generalization_validation_metrics_cases.py",
        "tests/_generalization_policy_cases.py",
        "tests/_generalization_runner_cases.py",
    ),
    "tests/test_generalization_matrix.py": (
        "tests/test_generalization_matrix.py",
        "tests/_generalization_matrix_replay_cases.py",
        "tests/_generalization_matrix_projection_cases.py",
        "tests/_generalization_matrix_validation_cases.py",
        "tests/_generalization_matrix_provenance_cases.py",
    ),
    "tests/test_lifecycle_and_risk.py": (
        "tests/test_lifecycle_and_risk.py",
        "tests/_lifecycle_strategic_discovery_cases.py",
        "tests/_lifecycle_strategic_cohort_cases.py",
        "tests/_lifecycle_freeze_tactical_probe_cases.py",
        "tests/_lifecycle_recovery_admission_cases.py",
        "tests/_lifecycle_protected_repair_cases.py",
        "tests/_lifecycle_freeze_execution_cases.py",
        "tests/_lifecycle_strategic_restore_cases.py",
        "tests/_lifecycle_strategic_guard_cases.py",
        "tests/_lifecycle_leader_recovery_cases.py",
        "tests/_lifecycle_restoration_risk_cases.py",
    ),
    "tests/test_phase2_ablation.py": (
        "tests/test_phase2_ablation.py",
        "tests/_phase2_carrier_worker_cases.py",
        "tests/_phase2_checkpoint_evidence_cases.py",
        "tests/_phase2_trust_boundary_cases.py",
    ),
    "tests/test_recovery_contracts.py": (
        "tests/test_recovery_contracts.py",
        "tests/_recovery_restore_completion_cases.py",
        "tests/_recovery_post_shock_cases.py",
    ),
    "tests/test_risk_transitions.py": (
        "tests/test_risk_transitions.py",
        "tests/_risk_transition_strategic_cap_cases.py",
        "tests/_risk_transition_overlay_budget_cases.py",
    ),
}


def _ast_sha256(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _unit_node(source: str, name: str) -> ast.AST:
    for node in ast.parse(source, type_comments=True).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            assigned = {
                child.id
                for target in targets
                for child in ast.walk(target)
                if isinstance(child, ast.Name)
            }
            if name in assigned:
                return node
    raise AssertionError(f"missing semantic unit {name}")


def _expression_value(
    node: ast.AST,
    environment: Mapping[str, object],
    local: Mapping[str, object] | None = None,
) -> object:
    bindings = {} if local is None else local
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in bindings:
            return bindings[node.id]
        assert node.id in environment, node.id
        return environment[node.id]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = [_expression_value(item, environment, bindings) for item in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else set(values) if isinstance(node, ast.Set) else values
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                spread = _expression_value(value, environment, bindings)
                assert isinstance(spread, Mapping)
                result.update(spread)
            else:
                result[_expression_value(key, environment, bindings)] = _expression_value(
                    value, environment, bindings
                )
        return result
    if isinstance(node, ast.DictComp):
        assert len(node.generators) == 1
        generator = node.generators[0]
        assert isinstance(generator.target, ast.Name) and not generator.ifs and not generator.is_async
        values = _expression_value(generator.iter, environment, bindings)
        assert isinstance(values, (tuple, list, set))
        result = {}
        for item in values:
            nested = {**bindings, generator.target.id: item}
            result[_expression_value(node.key, environment, nested)] = _expression_value(
                node.value, environment, nested
            )
        return result
    raise AssertionError(f"unsupported governance expression {type(node).__name__}")


def _mapping_assignment(source: str, name: str, environment: Mapping[str, object]) -> dict[str, str]:
    node = _unit_node(source, name)
    assert isinstance(node, (ast.Assign, ast.AnnAssign))
    value = _expression_value(node.value, environment)
    assert isinstance(value, Mapping)
    assert all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    return {str(key): str(item) for key, item in value.items()}


def _nearest_immutable_value(name: str, immutable: Mapping[str, str]) -> str:
    ranked: list[tuple[int, str]] = []
    for candidate, value in immutable.items():
        common = 0
        for left, right in zip(name, candidate, strict=False):
            if left != right:
                break
            common += 1
        ranked.append((common, value))
    maximum = max(score for score, _ in ranked)
    values = {value for score, value in ranked if score == maximum}
    assert maximum >= len("uquant.") and len(values) == 1, (name, values)
    return values.pop()


def _module_names(root: Path) -> set[str]:
    result: set[str] = set()
    for path in (root / "uquant").rglob("*.py"):
        relative = path.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        result.add(".".join(parts))
    return result


def analysis_legacy_unit_counts(
    *,
    immutable_source: str,
    current_sources: Mapping[str, str],
    root: Path,
) -> Counter[str]:
    authority_source = current_sources["tests/architecture/_analysis_authorities.py"]
    core_source = current_sources["tests/architecture/_analysis_debt.py"]
    immutable_contracts = _mapping_assignment(immutable_source, "_CONTRACT_RELOCATIONS", {})
    current_contracts = _mapping_assignment(authority_source, "_CONTRACT_RELOCATIONS", {})
    assert current_contracts == immutable_contracts

    immutable_authorities = _mapping_assignment(immutable_source, "MODULE_AUTHORITIES", {})
    current_authorities = _mapping_assignment(authority_source, "MODULE_AUTHORITIES", {})
    assert set(current_authorities) == _module_names(root)
    assert {name: current_authorities[name] for name in immutable_authorities} == immutable_authorities
    for name in set(current_authorities) - set(immutable_authorities):
        expected_authority = (
            "validation_runner" if name.startswith("uquant.validation.") else "production_safe"
        )
        assert current_authorities[name] == expected_authority

    immutable_debt = _mapping_assignment(
        immutable_source,
        "_DEBT_RELOCATIONS",
        {"_CONTRACT_RELOCATIONS": immutable_contracts},
    )
    current_debt = _mapping_assignment(
        authority_source,
        "_DEBT_RELOCATIONS",
        {"_CONTRACT_RELOCATIONS": current_contracts},
    )
    assert set(current_debt) <= _module_names(root)
    assert {name: current_debt[name] for name in immutable_debt} == immutable_debt
    for name in set(current_debt) - set(immutable_debt):
        assert current_debt[name] == _nearest_immutable_value(name, immutable_debt)

    immutable_snapshot = _unit_node(immutable_source, "architecture_snapshot")
    current_snapshot = _unit_node(core_source, "architecture_snapshot")
    reviewed_source = subprocess.check_output(
        [
            "git",
            "show",
            f"{_TASK5_REVIEW_COMMIT}:tests/architecture/_analysis_debt.py",
        ],
        cwd=root,
        text=True,
    )
    for name in (
        "_TASK10_REVIEWED_PRIVATE_TRANSPORTS",
        "_row_id",
        "_historical_private_import_rows",
        "measured_debt",
    ):
        assert ast.dump(_unit_node(core_source, name), include_attributes=False) == ast.dump(
            _unit_node(reviewed_source, name), include_attributes=False
        )
    reviewed_snapshot = _unit_node(reviewed_source, "architecture_snapshot")
    projected_snapshot = _analysis_snapshot_reviewed_projection(current_snapshot)
    assert ast.dump(projected_snapshot, include_attributes=False) == ast.dump(
        reviewed_snapshot, include_attributes=False
    )
    reconstructed_snapshot = _analysis_snapshot_legacy_projection(projected_snapshot)
    assert ast.dump(reconstructed_snapshot, include_attributes=False) == ast.dump(
        immutable_snapshot, include_attributes=False
    )
    return Counter(
        {
            _ast_sha256(_unit_node(immutable_source, name)): 1
            for name in (
                "MODULE_AUTHORITIES",
                "_DEBT_RELOCATIONS",
                "_row_id",
                "architecture_snapshot",
                "measured_debt",
            )
        }
    )


def _projected_analysis_counts(
    *, immutable_source: str, current_sources: Mapping[str, str], root: Path
) -> Counter[str]:
    current = Counter(
        str(unit["ast_sha256"])
        for source in current_sources.values()
        for unit in semantic_units(source)
    )
    for relative, name in (
        ("tests/architecture/_analysis_authorities.py", "MODULE_AUTHORITIES"),
        ("tests/architecture/_analysis_authorities.py", "_DEBT_RELOCATIONS"),
        (
            "tests/architecture/_analysis_debt.py",
            "_TASK10_REVIEWED_PRIVATE_TRANSPORTS",
        ),
        ("tests/architecture/_analysis_debt.py", "_row_id"),
        ("tests/architecture/_analysis_debt.py", "_historical_private_import_rows"),
        ("tests/architecture/_analysis_debt.py", "architecture_snapshot"),
        ("tests/architecture/_analysis_debt.py", "measured_debt"),
    ):
        digest = _ast_sha256(_unit_node(current_sources[relative], name))
        assert current[digest] == 1
        del current[digest]
    current.update(
        analysis_legacy_unit_counts(
            immutable_source=immutable_source,
            current_sources=current_sources,
            root=root,
        )
    )
    return current


def _statement(source: str) -> ast.stmt:
    body = ast.parse(source).body
    assert len(body) == 1
    return body[0]


def _rewrite_exact_statement(
    node: ast.AST,
    *,
    current: ast.stmt,
    replacement: ast.stmt | None,
) -> int:
    """Replace one exact statement anywhere in an owned semantic unit."""

    expected = ast.dump(current, include_attributes=False)
    rewrites = 0
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            rewritten: list[object] = []
            for item in value:
                if isinstance(item, ast.stmt) and ast.dump(
                    item, include_attributes=False
                ) == expected:
                    rewrites += 1
                    if replacement is not None:
                        rewritten.append(replacement)
                    continue
                if isinstance(item, ast.AST):
                    rewrites += _rewrite_exact_statement(
                        item,
                        current=current,
                        replacement=replacement,
                    )
                rewritten.append(item)
            setattr(node, field, rewritten)
        elif isinstance(value, ast.AST):
            rewrites += _rewrite_exact_statement(
                value,
                current=current,
                replacement=replacement,
            )
    return rewrites


def _analysis_snapshot_reviewed_projection(current: ast.AST) -> ast.AST:
    """Project the exact governed-root scanner extension onto Task-5 review."""

    reconstructed = copy.deepcopy(current)
    assert isinstance(reconstructed, ast.FunctionDef)
    keyword_names = [argument.arg for argument in reconstructed.args.kwonlyargs]
    assert keyword_names == [
        "source_texts",
        "governed_source_texts",
        "module_authorities",
    ]
    governed_default = reconstructed.args.kw_defaults[1]
    assert isinstance(governed_default, ast.Constant) and governed_default.value is None
    del reconstructed.args.kwonlyargs[1]
    del reconstructed.args.kw_defaults[1]
    rewrites = _rewrite_exact_statement(
        reconstructed,
        current=_statement(
            "governed_private_edges = scan_analysis_governed_private_edges("
            "root, source_texts, governed_source_texts)"
        ),
        replacement=None,
    )
    assert rewrites == 1

    graph_statement = _statement(
        "graph: dict[str, set[str]] = {module: set() for module in modules}"
    )
    graph_dump = ast.dump(graph_statement, include_attributes=False)
    graph_indexes = [
        index
        for index, node in enumerate(reconstructed.body)
        if ast.dump(node, include_attributes=False) == graph_dump
    ]
    assert len(graph_indexes) == 1
    reconstructed.body.insert(
        graph_indexes[0] + 1,
        _statement("private_imports: list[dict[str, object]] = []"),
    )

    alias_loops = [
        node
        for node in ast.walk(reconstructed)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "alias"
        and {name.id for name in ast.walk(node) if isinstance(name, ast.Name)}
        >= {"authority_targets", "alias_target", "target_base"}
    ]
    assert len(alias_loops) == 1
    alias_loops[0].body.extend(
        ast.parse(
            """private_import_id = f"{module}:{target_base}:{alias.name}"
if alias.name.startswith("_") and target_base and target_base != module:
    row = {
        "id": private_import_id,
        "importer": module,
        "imported_from": target_base,
        "name": alias.name,
        "line": node.lineno,
    }
    private_imports.append(row)
"""
        ).body
    )
    rewrites = _rewrite_exact_statement(
        reconstructed,
        current=_statement(
            'sorted_private_imports = sorted([*governed_private_edges["direct"], '
            '*governed_private_edges["qualified"], '
            '*governed_private_edges["dynamic"]], key=_row_id)'
        ),
        replacement=_statement(
            "sorted_private_imports = sorted(private_imports, key=_row_id)"
        ),
    )
    assert rewrites == 1
    return ast.fix_missing_locations(reconstructed)


_LEGACY_SNAPSHOT_SORTS: Mapping[str, str] = {
    "sorted_private_module_calls": "private_module_calls",
    "sorted_task5_private_imports": "task5_relocated_private_imports",
    "sorted_task6_private_imports": "task6_relocated_private_imports",
    "sorted_task7_private_imports": "task7_relocated_private_imports",
    "sorted_task8_private_imports": "task8_relocated_private_imports",
    "sorted_task9_private_imports": "task9_relocated_private_imports",
}


class _LegacySnapshotSorts(ast.NodeTransformer):

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def visit_Name(self, node: ast.Name) -> ast.AST:
        collection = _LEGACY_SNAPSHOT_SORTS.get(node.id)
        if collection is None or not isinstance(node.ctx, ast.Load):
            return node
        self.counts[node.id] += 1
        replacement = ast.Call(
            func=ast.Name(id="sorted", ctx=ast.Load()),
            args=[ast.Name(id=collection, ctx=ast.Load())],
            keywords=[ast.keyword(arg="key", value=ast.Name(id="_row_id", ctx=ast.Load()))],
        )
        return ast.copy_location(replacement, node)


def _analysis_snapshot_legacy_projection(current: ast.AST) -> ast.AST:
    """Project the exact immutable-bucket split onto the Task-10 start unit."""
    reconstructed = copy.deepcopy(current)
    for task in range(5, 10):
        rewrites = _rewrite_exact_statement(
            reconstructed,
            current=_statement(
                f"task{task}_relocated_private_imports = "
                f"_historical_private_import_rows(_TASK{task}_RELOCATED_PRIVATE_IMPORT_GROUPS)"
            ),
            replacement=_statement(
                f"task{task}_relocated_private_imports: list[dict[str, object]] = []"
            ),
        )
        assert rewrites == 1
    legacy_routing = """if private_import_id in _TASK5_RELOCATED_PRIVATE_IMPORTS:
    task5_relocated_private_imports.append(row)
elif private_import_id in _TASK6_RELOCATED_PRIVATE_IMPORTS:
    task6_relocated_private_imports.append(row)
elif private_import_id in _TASK7_RELOCATED_PRIVATE_IMPORTS:
    task7_relocated_private_imports.append(row)
elif private_import_id in _TASK8_RELOCATED_PRIVATE_IMPORTS:
    task8_relocated_private_imports.append(row)
elif private_import_id in _TASK9_RELOCATED_PRIVATE_IMPORTS:
    task9_relocated_private_imports.append(row)
else:
    private_imports.append(row)"""
    rewrites = _rewrite_exact_statement(
        reconstructed,
        current=_statement("private_imports.append(row)"),
        replacement=_statement(legacy_routing),
    )
    assert rewrites == 1
    for variable, collection in _LEGACY_SNAPSHOT_SORTS.items():
        rewrites = _rewrite_exact_statement(
            reconstructed,
            current=_statement(f"{variable} = sorted({collection}, key=_row_id)"),
            replacement=None,
        )
        assert rewrites == 1
    sorter = _LegacySnapshotSorts()
    reconstructed = sorter.visit(reconstructed)
    assert sorter.counts == Counter({name: 1 for name in _LEGACY_SNAPSHOT_SORTS})
    return ast.fix_missing_locations(reconstructed)


def _task7_legacy_unit(
    *,
    name: str,
    immutable_source: str,
    current_source: str,
) -> tuple[str, str]:
    """Prove one Task 7 test unit is an exact transport-backed old unit."""

    immutable = _unit_node(immutable_source, name)
    current = _unit_node(current_source, name)
    assert isinstance(immutable, (ast.FunctionDef, ast.AsyncFunctionDef))
    assert isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef))
    reconstructed = ast.fix_missing_locations(ast.parse(ast.unparse(current)).body[0])

    if name == "_assert_task7_ownership_surface":
        rewrites = (
            (
                "overrides = task10_task7_reviewed_sources(root=ROOT, overrides=overrides)",
                None,
            ),
            (
                """stage = expand_task10_risk_stage(
    root=ROOT,
    relative=spec.path,
    stage_name=stage_name,
    wrapper=stage,
    overrides=overrides,
)""",
                None,
            ),
            (
                """assessment = expand_task10_risk_assessment(
    root=ROOT,
    candidate=assessment,
    overrides=overrides,
)""",
                None,
            ),
        )
    elif name == "test_task7_source_surface_migration_is_exact_and_requirements_stay_bound":
        rewrites = (
            (
                "expected = task10_source_surface_projection(identifier, expected)",
                None,
            ),
        )
    elif name == "test_task7_private_and_complexity_relocations_are_exact_and_fail_closed":
        rewrites = (
            (
                """assert task10_private_relocation_projection(
    root=ROOT,
    task=7,
    observed={str(row["id"]) for row in relocated},
    expected=set(_TASK7_RELOCATED_PRIVATE_IMPORTS),
) == _TASK7_RELOCATED_PRIVATE_IMPORTS""",
                'assert {str(row["id"]) for row in relocated} == _TASK7_RELOCATED_PRIVATE_IMPORTS',
            ),
            (
                """assert task10_task7_function_debt_projection(
    root=ROOT,
    observed=observed_debt,
    expected=set(_TASK7_RELOCATED_FUNCTION_DEBT),
    function_rows=functions,
) == set(_TASK7_RELOCATED_FUNCTION_DEBT)""",
                "assert observed_debt == set(_TASK7_RELOCATED_FUNCTION_DEBT)",
            ),
            (
                """immutable_authorities = task10_task7_historical_authorities(
    immutable_authorities,
    set(immutable_sources),
)""",
                None,
            ),
            (
                "assert task10_task7_historical_base_lines(root=ROOT, current_row=base) == 391",
                'assert base["lines"] == 391',
            ),
        )
    elif name == "test_task7_facade_preserves_consumed_names_reflection_and_live_anchor_seam":
        rewrites = (
            (
                "assert risk_module.dynamic_anchor_updater() is capture",
                'assert risk_module._risk_runtime_seam("_update_dynamic_anchors") is capture',
            ),
            ('assert "_risk_runtime_seam" not in vars(risk_module)', None),
        )
    else:
        raise AssertionError(f"undeclared Task 7 test transport {name}")

    for current_statement, replacement_source in rewrites:
        replacement = None if replacement_source is None else _statement(replacement_source)
        count = _rewrite_exact_statement(
            reconstructed,
            current=_statement(current_statement),
            replacement=replacement,
        )
        assert count == 1, (name, current_statement, count)
    assert ast.dump(reconstructed, include_attributes=False) == ast.dump(
        immutable, include_attributes=False
    )
    return _ast_sha256(current), _ast_sha256(immutable)


def task7_legacy_unit_counts(
    *,
    immutable_source: str,
    current_sources: Mapping[str, str],
) -> Counter[str]:
    """Project exact Task 10 test transports onto the immutable Task 7 units."""

    relative = "tests/architecture/test_task7_risk_boundaries.py"
    assert set(current_sources) == set(TEST_RELOCATION_PATHS[relative])
    current = Counter(
        str(unit["ast_sha256"])
        for source in current_sources.values()
        for unit in semantic_units(source)
    )
    units = (
        ("_assert_task7_ownership_surface", relative),
        (
            "test_task7_source_surface_migration_is_exact_and_requirements_stay_bound",
            relative,
        ),
        (
            "test_task7_private_and_complexity_relocations_are_exact_and_fail_closed",
            relative,
        ),
        (
            "test_task7_facade_preserves_consumed_names_reflection_and_live_anchor_seam",
            "tests/architecture/_task7_risk_import_boundaries.py",
        ),
    )
    mappings = [
        _task7_legacy_unit(
            name=name,
            immutable_source=immutable_source,
            current_source=current_sources[current_relative],
        )
        for name, current_relative in units
    ]
    assert len({current_digest for current_digest, _ in mappings}) == len(units)
    assert len({immutable_digest for _, immutable_digest in mappings}) == len(units)
    before = sum(current.values())
    for current_digest, immutable_digest in mappings:
        assert current[current_digest] == 1
        current[current_digest] -= 1
        if current[current_digest] == 0:
            del current[current_digest]
        current[immutable_digest] += 1
    assert sum(current.values()) == before
    return current


def _record_units(records: object) -> dict[str, Counter[str]]:
    assert isinstance(records, list)
    result: dict[str, Counter[str]] = {}
    for record in records:
        assert isinstance(record, Mapping)
        path = str(record["path"])
        units = record["semantic_units"]
        assert isinstance(units, list) and path not in result
        result[path] = Counter(str(unit["ast_sha256"]) for unit in units if isinstance(unit, Mapping))
        assert sum(result[path].values()) == len(units)
    return result


def verify_test_relocations(
    *,
    immutable_records: object,
    immutable_analysis_source: str,
    immutable_task7_source: str,
    root: Path,
    relocation_paths: Mapping[str, tuple[str, ...]] = TEST_RELOCATION_PATHS,
) -> None:
    expected = _record_units(immutable_records)
    assert set(relocation_paths) == set(expected)
    flattened = [target for targets in relocation_paths.values() for target in targets]
    assert len(flattened) == len(set(flattened))
    for source, targets in relocation_paths.items():
        assert targets and targets[0] == source
        current_sources = {
            target: (root / target).read_text(encoding="utf-8")
            for target in targets
            if (root / target).is_file()
        }
        assert set(current_sources) == set(targets)
        if source == "tests/architecture/_analysis.py":
            observed = _projected_analysis_counts(
                immutable_source=immutable_analysis_source,
                current_sources=current_sources,
                root=root,
            )
        elif source == "tests/architecture/test_task7_risk_boundaries.py":
            observed = task7_legacy_unit_counts(
                immutable_source=immutable_task7_source,
                current_sources=current_sources,
            )
        else:
            observed = Counter(
                str(unit["ast_sha256"])
                for current_source in current_sources.values()
                for unit in semantic_units(current_source)
            )
        assert observed == expected[source], source
