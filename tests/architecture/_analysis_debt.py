"""Architecture debt measurement and deterministic source analysis."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import tarfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from ._analysis_authorities import (
    _CONTRACT_RELOCATIONS as _CONTRACT_RELOCATIONS,
)
from ._analysis_authorities import (
    _DEBT_RELOCATIONS as _DEBT_RELOCATIONS,
)
from ._analysis_authorities import (
    _MODULE_AUTHORITY_VALUES as _MODULE_AUTHORITY_VALUES,
)
from ._analysis_authorities import (
    _NONPRODUCTION_IMPORT_AUTHORITIES as _NONPRODUCTION_IMPORT_AUTHORITIES,
)
from ._analysis_authorities import (
    _RUNNER_AUTHORITIES as _RUNNER_AUTHORITIES,
)
from ._analysis_authorities import (
    _TASK5_RELOCATED_PRIVATE_IMPORT_GROUPS as _TASK5_RELOCATED_PRIVATE_IMPORT_GROUPS,
)
from ._analysis_authorities import (
    _TASK5_RELOCATED_PRIVATE_IMPORTS as _TASK5_RELOCATED_PRIVATE_IMPORTS,
)
from ._analysis_authorities import (
    _TASK6_RELOCATED_PRIVATE_IMPORT_GROUPS as _TASK6_RELOCATED_PRIVATE_IMPORT_GROUPS,
)
from ._analysis_authorities import (
    _TASK6_RELOCATED_PRIVATE_IMPORTS as _TASK6_RELOCATED_PRIVATE_IMPORTS,
)
from ._analysis_authorities import (
    _TASK7_RELOCATED_FUNCTION_DEBT as _TASK7_RELOCATED_FUNCTION_DEBT,
)
from ._analysis_authorities import (
    _TASK7_RELOCATED_PRIVATE_IMPORT_GROUPS as _TASK7_RELOCATED_PRIVATE_IMPORT_GROUPS,
)
from ._analysis_authorities import (
    _TASK7_RELOCATED_PRIVATE_IMPORTS as _TASK7_RELOCATED_PRIVATE_IMPORTS,
)
from ._analysis_authorities import (
    FINAL_BUDGETS as FINAL_BUDGETS,
)
from ._analysis_authorities import (
    INVENTORY_PATH as INVENTORY_PATH,
)
from ._analysis_authorities import (
    MODULE_AUTHORITIES as MODULE_AUTHORITIES,
)
from ._analysis_authorities import (
    PUBLIC_API_PATH as PUBLIC_API_PATH,
)
from ._analysis_authorities import (
    ROOT as ROOT,
)
from ._analysis_relocations import (
    _MUTABLE_CALLS as _MUTABLE_CALLS,
)
from ._analysis_relocations import (
    _MUTATING_METHODS as _MUTATING_METHODS,
)
from ._analysis_relocations import (
    _PUBLIC_API_FACADE_PATHS as _PUBLIC_API_FACADE_PATHS,
)
from ._analysis_relocations import (
    _PUBLIC_API_IMPLEMENTATIONS as _PUBLIC_API_IMPLEMENTATIONS,
)
from ._analysis_relocations import (
    _TASK6_RELOCATED_FUNCTION_DEBT as _TASK6_RELOCATED_FUNCTION_DEBT,
)
from ._analysis_relocations import (
    _TASK6_RELOCATED_GLOBAL_DEBT as _TASK6_RELOCATED_GLOBAL_DEBT,
)
from ._analysis_relocations import (
    _TASK8_ALLOCATE_STRATEGY_DEBT as _TASK8_ALLOCATE_STRATEGY_DEBT,
)
from ._analysis_relocations import (
    _TASK8_RELOCATED_FUNCTION_DEBT as _TASK8_RELOCATED_FUNCTION_DEBT,
)
from ._analysis_relocations import (
    _TASK8_RELOCATED_FUNCTION_NAMES as _TASK8_RELOCATED_FUNCTION_NAMES,
)
from ._analysis_relocations import (
    _TASK8_RELOCATED_PRIVATE_IMPORT_GROUPS as _TASK8_RELOCATED_PRIVATE_IMPORT_GROUPS,
)
from ._analysis_relocations import (
    _TASK8_RELOCATED_PRIVATE_IMPORTS as _TASK8_RELOCATED_PRIVATE_IMPORTS,
)
from ._analysis_relocations import (
    _TASK8_RELOCATED_TYPE_IGNORES as _TASK8_RELOCATED_TYPE_IGNORES,
)
from ._analysis_relocations import (
    _TASK9_RELOCATED_FUNCTION_DEBT as _TASK9_RELOCATED_FUNCTION_DEBT,
)
from ._analysis_relocations import (
    _TASK9_RELOCATED_GLOBAL_DEBT as _TASK9_RELOCATED_GLOBAL_DEBT,
)
from ._analysis_relocations import (
    _TASK9_RELOCATED_PRIVATE_IMPORT_GROUPS as _TASK9_RELOCATED_PRIVATE_IMPORT_GROUPS,
)
from ._analysis_relocations import (
    _TASK9_RELOCATED_PRIVATE_IMPORTS as _TASK9_RELOCATED_PRIVATE_IMPORTS,
)
from ._task10_private_imports import scan_analysis_governed_private_edges

_TASK10_REVIEWED_PRIVATE_TRANSPORTS = frozenset(
    {
        "uquant.attribution.builder:uquant.attribution.concentration:_empty_pnl_bucket",
        "uquant.attribution.validation:uquant.attribution.concentration:_group_lot_pnl",
        "uquant.attribution.validation:uquant.attribution.concentration:_holding_summary",
        "uquant.attribution.validation:uquant.attribution.replay_evidence:_LEDGER_FIELDS",
        "uquant.attribution.validation:uquant.attribution.replay_evidence:_require_exact_fields",
        "uquant.risk.assessment:uquant.risk.capital:_portfolio_drawdowns",
        "uquant.risk.assessment:uquant.risk.recovery_state:_reset_recovery_owner_rearm",
        "uquant.risk.transitions:uquant.risk.recovery_state:_persistent_crisis_cap",
        "uquant.risk.transitions:uquant.risk.strategic_guard:_strategic_crisis_severity",
        "uquant.portfolio.pipeline:uquant.portfolio.recovery.admission:_recovery_admission_targets",
        "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_awaiting_recovery_cohort_targets",
        "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_controlled_oversold_rebound_targets",
        "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_locked_recovery_cohort_targets",
        "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_overextended_pullback_targets",
        "uquant.portfolio.recovery.admission:uquant.portfolio.recovery.targets:_recovery_cohort_targets",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.projection:_attribution_neutral_equality_sha256",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.projection:_candidate_contract_sha256",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_ARTIFACT_FIELDS_V1",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_ARTIFACT_FIELDS_V2",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_ATTRIBUTION_DEFINITION",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_CELL_FIELDS_V1",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_CELL_FIELDS_V2",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_EVIDENCE_FIELDS",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_ROOT",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_artifact_equality_sha256",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_metric_payload",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_metrics_reconciled_from_raw",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_provenance_schema_failures",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_replay_error",
        "uquant.validation.generalization_policy.evaluator:uquant.validation.generalization_policy.schema:_schema_failures",
    }
)

def canonical_json(value: object) -> str:
    """Return the canonical encoding used for inventory integrity hashes."""

    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def sha256_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()

def module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)

def production_sources(root: Path = ROOT) -> tuple[Path, ...]:
    return tuple(sorted((root / "uquant").rglob("*.py")))

def git_python_sources(root: Path, commit: str) -> dict[str, str]:
    """Read the tracked production Python bytes directly from an immutable Git tree."""

    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit, "--", "uquant"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    result: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as payload:
        for member in payload.getmembers():
            if not member.isfile() or not member.name.endswith(".py"):
                continue
            extracted = payload.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"Git archive member has no content: {member.name}")
            result[member.name] = extracted.read().decode("utf-8")
    return {path: result[path] for path in sorted(result)}

def production_source_surface(source_texts: Mapping[str, str]) -> dict[str, object]:
    entries = [
        {
            "path": path,
            "bytes": len(source.encode("utf-8")),
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }
        for path, source in sorted(source_texts.items())
    ]
    return {
        "entries": entries,
        "entry_count": len(entries),
        "canonical_sha256": canonical_sha256(entries),
        "tree_sha256": canonical_sha256(
            [(entry["path"], entry["sha256"]) for entry in entries]
        ),
    }

def _definition_start(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    return min([node.lineno, *decorator_lines])

class _BranchCounter(ast.NodeVisitor):
    """Count control-flow branch points without entering nested scopes."""

    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.count = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_If(self, node: ast.If) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.count += 1
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.count += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.count += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.count += len(node.cases)
        self.generic_visit(node)

def _function_rows(
    *,
    module: str,
    path: str,
    body: Sequence[ast.stmt],
    prefix: str = "",
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{node.name}"
            counter = _BranchCounter(node)
            counter.visit(node)
            start = _definition_start(node)
            row = {
                "id": f"{module}:{qualname}",
                "module": module,
                "path": path,
                "qualname": qualname,
                "line": start,
                "lines": int(node.end_lineno or node.lineno) - start + 1,
                "branch_points": counter.count,
            }
            rows.append(row)
            rows.extend(
                _function_rows(
                    module=module,
                    path=path,
                    body=node.body,
                    prefix=f"{qualname}.<locals>.",
                )
            )
        elif isinstance(node, ast.ClassDef):
            rows.extend(
                _function_rows(
                    module=module,
                    path=path,
                    body=node.body,
                    prefix=f"{prefix}{node.name}.",
                )
            )
    return rows

def _assigned_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _assigned_names(item)]
    return []

def _call_name(node: ast.Call) -> str:
    value: ast.expr = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))

def _mutable_initializer(value: ast.expr | None) -> bool:
    if isinstance(value, (ast.Dict, ast.DictComp, ast.List, ast.ListComp, ast.Set, ast.SetComp)):
        return True
    return isinstance(value, ast.Call) and _call_name(value) in _MUTABLE_CALLS

def _module_global_rows(
    *, module: str, path: str, tree: ast.Module, source: str
) -> list[dict[str, object]]:
    candidates: dict[str, dict[str, object]] = {}
    for statement in tree.body:
        values: list[tuple[str, ast.expr | None]] = []
        if isinstance(statement, ast.Assign):
            values = [
                (name, statement.value)
                for target in statement.targets
                for name in _assigned_names(target)
            ]
        elif isinstance(statement, ast.AnnAssign):
            values = [
                (name, statement.value) for name in _assigned_names(statement.target)
            ]
        for name, value in values:
            candidates[name] = {
                "id": f"{module}:{name}",
                "module": module,
                "path": path,
                "name": name,
                "line": statement.lineno,
                "value_kind": type(value).__name__ if value is not None else "None",
                "mutable_initializer": _mutable_initializer(value),
                "mutation_sites": [],
            }

    mutation_sites: dict[str, set[int]] = defaultdict(set)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id in candidates
                and node.func.attr in _MUTATING_METHODS
            ):
                mutation_sites[owner.id].add(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in candidates
                ):
                    mutation_sites[target.value.id].add(node.lineno)
    for name, sites in mutation_sites.items():
        candidates[name]["mutation_sites"] = sorted(sites)
    del source
    return [candidates[name] for name in sorted(candidates)]

def _private_helpers(
    *, module: str, path: str, tree: ast.Module
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            normalized = ast.FunctionDef(
                name="_helper",
                args=node.args,
                body=node.body,
                decorator_list=[],
                returns=node.returns,
                type_comment=node.type_comment,
                type_params=getattr(node, "type_params", []),
            )
            rows.append(
                {
                    "module": module,
                    "path": path,
                    "name": node.name,
                    "line": node.lineno,
                    "body_sha256": hashlib.sha256(
                        ast.dump(normalized, annotate_fields=True, include_attributes=False).encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                }
            )
    return rows

def _resolve_from(module: str, *, is_package: bool, level: int, imported: str | None) -> str:
    package = module if is_package else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    if level:
        keep = len(parts) - (level - 1)
        parts = parts[: max(0, keep)]
    elif imported:
        parts = []
    if imported:
        parts.extend(imported.split("."))
    return ".".join(parts)

def _longest_internal_module(candidate: str, modules: set[str]) -> str | None:
    parts = candidate.split(".")
    while parts:
        value = ".".join(parts)
        if value in modules:
            return value
        parts.pop()
    return None

def _strongly_connected_components(graph: Mapping[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            result.append(sorted(component))

    for item in sorted(graph):
        if item not in indices:
            visit(item)
    return sorted(result, key=lambda group: (-len(group), group))

def _external_authority(target: str) -> str:
    top = target.split(".", 1)[0]
    return {
        "research": "research",
        "scripts": "operator_script",
        "tests": "test",
    }.get(top, "external_dependency")

def _forbidden_authority_edge(importer_authority: str, target_authority: str) -> bool:
    return target_authority in _NONPRODUCTION_IMPORT_AUTHORITIES or (
        importer_authority == "production_safe" and target_authority in _RUNNER_AUTHORITIES
    )

def _row_id(row: Mapping[str, object]) -> str:
    return str(row["id"])


def _historical_private_import_rows(
    groups: Sequence[tuple[str, str, Sequence[str]]],
) -> list[dict[str, object]]:
    """Project frozen relocation identities without consulting current imports."""

    return [
        {
            "id": f"{importer}:{imported_from}:{name}",
            "importer": importer,
            "imported_from": imported_from,
            "name": name,
            "line": 0,
        }
        for importer, imported_from, names in groups
        for name in names
        if f"{importer}:{imported_from}:{name}"
        not in _TASK10_REVIEWED_PRIVATE_TRANSPORTS
    ]

def architecture_snapshot(
    root: Path = ROOT,
    *,
    source_texts: Mapping[str, str] | None = None,
    governed_source_texts: Mapping[str, str] | None = None,
    module_authorities: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Measure the live production module graph and all explicit debt dimensions."""

    governed_private_edges = scan_analysis_governed_private_edges(
        root, source_texts, governed_source_texts
    )
    parsed: dict[str, tuple[Path, str, ast.Module]] = {}
    selected_sources = (
        {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in production_sources(root)
        }
        if source_texts is None
        else dict(source_texts)
    )
    for relative, source in sorted(selected_sources.items()):
        path = root / relative
        parsed[module_name(root, path)] = (
            path,
            source,
            ast.parse(source, filename=str(path), type_comments=True),
        )
    modules = set(parsed)
    authorities = dict(MODULE_AUTHORITIES if module_authorities is None else module_authorities)
    if set(authorities) != modules:
        missing = sorted(modules - set(authorities))
        stale = sorted(set(authorities) - modules)
        raise AssertionError(
            f"module authority map must be explicit and complete; missing={missing}, stale={stale}"
        )
    invalid = sorted(set(authorities.values()) - _MODULE_AUTHORITY_VALUES)
    if invalid:
        raise AssertionError(f"unknown module authorities: {invalid}")
    graph: dict[str, set[str]] = {module: set() for module in modules}
    private_module_calls: list[dict[str, object]] = []
    task5_relocated_private_imports = _historical_private_import_rows(
        _TASK5_RELOCATED_PRIVATE_IMPORT_GROUPS
    )
    task6_relocated_private_imports = _historical_private_import_rows(
        _TASK6_RELOCATED_PRIVATE_IMPORT_GROUPS
    )
    task7_relocated_private_imports = _historical_private_import_rows(
        _TASK7_RELOCATED_PRIVATE_IMPORT_GROUPS
    )
    task8_relocated_private_imports = _historical_private_import_rows(
        _TASK8_RELOCATED_PRIVATE_IMPORT_GROUPS
    )
    task9_relocated_private_imports = _historical_private_import_rows(
        _TASK9_RELOCATED_PRIVATE_IMPORT_GROUPS
    )
    forbidden_imports: list[dict[str, object]] = []
    function_rows: list[dict[str, object]] = []
    global_rows: list[dict[str, object]] = []
    type_ignores: list[dict[str, object]] = []
    helper_rows: list[dict[str, object]] = []
    module_rows: dict[str, dict[str, object]] = {}

    for module, (path, source, tree) in sorted(parsed.items()):
        relative = path.relative_to(root).as_posix()
        is_package = path.name == "__init__.py"
        functions = _function_rows(module=module, path=relative, body=tree.body)
        function_rows.extend(functions)
        global_rows.extend(_module_global_rows(module=module, path=relative, tree=tree, source=source))
        helper_rows.extend(_private_helpers(module=module, path=relative, tree=tree))
        source_lines = source.splitlines()
        module_rows[module] = {
            "path": relative,
            "lines": len(source_lines),
            "nonblank_noncomment_lines": sum(
                bool(line.strip()) and not line.lstrip().startswith("#") for line in source_lines
            ),
            "function_count": len(functions),
            "class_count": sum(isinstance(node, ast.ClassDef) for node in tree.body),
        }
        module_aliases: dict[str, str] = {}
        for imported_node in ast.walk(tree):
            if isinstance(imported_node, ast.Import):
                for alias in imported_node.names:
                    target = _longest_internal_module(alias.name, modules)
                    if alias.asname is not None and target is not None and target != module:
                        module_aliases[alias.asname] = target
            elif isinstance(imported_node, ast.ImportFrom):
                target_base = _resolve_from(
                    module,
                    is_package=is_package,
                    level=imported_node.level,
                    imported=imported_node.module,
                )
                for alias in imported_node.names:
                    alias_target = _longest_internal_module(
                        f"{target_base}.{alias.name}".strip("."),
                        modules,
                    )
                    if alias_target is not None and alias_target != module:
                        module_aliases[alias.asname or alias.name] = alias_target
        for ignored in tree.type_ignores:
            text = source_lines[ignored.lineno - 1].strip()
            tag = ignored.tag or ""
            occurrence = sum(
                1
                for prior in type_ignores
                if prior["path"] == relative and prior["source"] == text and prior["tag"] == tag
            )
            type_ignores.append(
                {
                    "id": f"{relative}:{tag}:{text}:{occurrence}",
                    "path": relative,
                    "line": ignored.lineno,
                    "tag": tag,
                    "source": text,
                }
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = _longest_internal_module(alias.name, modules)
                    if target is not None and target != module:
                        graph[module].add(target)
                    target_authority = (
                        authorities[target]
                        if target is not None
                        else _external_authority(alias.name)
                    )
                    if _forbidden_authority_edge(authorities[module], target_authority):
                        forbidden_imports.append(
                            {
                                "id": f"{module}:{node.lineno}:{alias.name}",
                                "importer": module,
                                "importer_authority": authorities[module],
                                "target": alias.name,
                                "target_authority": target_authority,
                                "line": node.lineno,
                            }
                        )
            elif isinstance(node, ast.ImportFrom):
                target_base = _resolve_from(
                    module,
                    is_package=is_package,
                    level=node.level,
                    imported=node.module,
                )
                target = _longest_internal_module(target_base, modules)
                if target is not None and target != module:
                    graph[module].add(target)
                authority_targets: dict[str, str] = {}
                for alias in node.names:
                    alias_target = _longest_internal_module(
                        f"{target_base}.{alias.name}".strip("."), modules
                    )
                    if alias_target is not None and alias_target not in {module, target}:
                        graph[module].add(alias_target)
                        authority_targets[alias_target] = authorities[alias_target]
                    else:
                        authority_targets[target_base] = (
                            authorities[target]
                            if target is not None
                            else _external_authority(target_base)
                        )
                for authority_target, target_authority in authority_targets.items():
                    if _forbidden_authority_edge(authorities[module], target_authority):
                        forbidden_imports.append(
                            {
                                "id": f"{module}:{node.lineno}:{authority_target}",
                                "importer": module,
                                "importer_authority": authorities[module],
                                "target": authority_target,
                                "target_authority": target_authority,
                                "line": node.lineno,
                            }
                        )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("_")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in module_aliases
            ):
                imported_from = module_aliases[node.func.value.id]
                private_module_calls.append(
                    {
                        "id": f"{module}:{imported_from}:{node.func.attr}",
                        "importer": module,
                        "imported_from": imported_from,
                        "name": node.func.attr,
                        "line": node.lineno,
                    }
                )

    fan_in: dict[str, set[str]] = {module: set() for module in modules}
    for importer, targets in graph.items():
        for target in targets:
            fan_in[target].add(importer)
    for module in modules:
        module_rows[module]["fan_out"] = sorted(graph[module])
        module_rows[module]["fan_out_count"] = len(graph[module])
        module_rows[module]["fan_in"] = sorted(fan_in[module])
        module_rows[module]["fan_in_count"] = len(fan_in[module])

    helper_names: dict[str, list[dict[str, object]]] = defaultdict(list)
    helper_bodies: dict[str, list[dict[str, object]]] = defaultdict(list)
    for helper in helper_rows:
        helper_names[str(helper["name"])].append(helper)
        helper_bodies[str(helper["body_sha256"])].append(helper)
    duplicate_names = [
        {
            "id": f"duplicate-helper:{name}",
            "name": name,
            "members": [
                {"module": row["module"], "path": row["path"], "line": row["line"]}
                for row in sorted(
                    rows,
                    key=lambda item: (str(item["module"]), cast(int, item["line"])),
                )
            ],
        }
        for name, rows in sorted(helper_names.items())
        if len({str(row["module"]) for row in rows}) > 1
    ]
    identical_bodies = [
        {
            "body_sha256": body,
            "members": [
                {
                    "module": row["module"],
                    "path": row["path"],
                    "name": row["name"],
                    "line": row["line"],
                }
                for row in sorted(
                    rows,
                    key=lambda item: (
                        str(item["module"]),
                        str(item["name"]),
                        cast(int, item["line"]),
                    ),
                )
            ],
        }
        for body, rows in sorted(helper_bodies.items())
        if len({str(row["module"]) for row in rows}) > 1
    ]
    components = _strongly_connected_components(graph)
    cycles = [
        {"id": "scc:" + ",".join(component), "modules": component}
        for component in components
        if len(component) > 1
        or (len(component) == 1 and component[0] in graph[component[0]])
    ]
    sorted_private_imports = sorted(
        [
            *governed_private_edges["direct"],
            *governed_private_edges["qualified"],
            *governed_private_edges["dynamic"],
        ],
        key=_row_id,
    )
    sorted_private_module_calls = sorted(private_module_calls, key=_row_id)
    sorted_task5_private_imports = sorted(task5_relocated_private_imports, key=_row_id)
    sorted_task6_private_imports = sorted(task6_relocated_private_imports, key=_row_id)
    sorted_task7_private_imports = sorted(task7_relocated_private_imports, key=_row_id)
    sorted_task8_private_imports = sorted(task8_relocated_private_imports, key=_row_id)
    sorted_task9_private_imports = sorted(task9_relocated_private_imports, key=_row_id)
    sorted_forbidden_imports = sorted(forbidden_imports, key=_row_id)
    return {
        "modules": {module: module_rows[module] for module in sorted(module_rows)},
        "functions": sorted(
            function_rows,
            key=lambda row: (str(row["path"]), cast(int, row["line"])),
        ),
        "import_graph": {
            "module_authorities": {
                module: authorities[module] for module in sorted(authorities)
            },
            "edges": [
                {"importer": importer, "imported": target}
                for importer in sorted(graph)
                for target in sorted(graph[importer])
            ],
            "strongly_connected_components": components,
            "cycles": cycles,
            "cross_module_private_imports": sorted_private_imports,
            "cross_module_private_module_calls": sorted_private_module_calls,
            "task5_relocated_private_imports": sorted_task5_private_imports,
            "task6_relocated_private_imports": sorted_task6_private_imports,
            "task7_relocated_private_imports": sorted_task7_private_imports,
            "task8_relocated_private_imports": sorted_task8_private_imports,
            "task9_relocated_private_imports": sorted_task9_private_imports,
            "forbidden_imports": sorted_forbidden_imports,
        },
        "module_globals": sorted(global_rows, key=lambda row: str(row["id"])),
        "type_ignores": sorted(type_ignores, key=lambda row: str(row["id"])),
        "duplicate_helpers": {
            "same_private_name": duplicate_names,
            "identical_bodies": identical_bodies,
        },
    }

def measured_debt(snapshot: Mapping[str, object]) -> dict[str, list[dict[str, object]]]:
    modules = snapshot["modules"]
    functions = snapshot["functions"]
    globals_ = snapshot["module_globals"]
    imports = snapshot["import_graph"]
    helpers = snapshot["duplicate_helpers"]
    type_ignores = snapshot["type_ignores"]
    assert isinstance(modules, Mapping)
    assert isinstance(functions, list)
    assert isinstance(globals_, list)
    assert isinstance(imports, Mapping)
    assert isinstance(helpers, Mapping)
    assert isinstance(type_ignores, list)

    def debt_id(identifier: object) -> str:
        value = str(identifier)
        relocated_global = _TASK6_RELOCATED_GLOBAL_DEBT.get(value)
        if relocated_global is not None:
            return relocated_global
        task9_relocated_global = _TASK9_RELOCATED_GLOBAL_DEBT.get(value)
        if task9_relocated_global is not None:
            return task9_relocated_global
        relocated = _TASK6_RELOCATED_FUNCTION_DEBT.get(value)
        if relocated is not None:
            return relocated[0]
        task7_relocated = _TASK7_RELOCATED_FUNCTION_DEBT.get(value)
        if task7_relocated is not None:
            return task7_relocated
        task8_relocated = _TASK8_RELOCATED_FUNCTION_DEBT.get(value)
        if task8_relocated is not None:
            return task8_relocated
        task9_relocated = _TASK9_RELOCATED_FUNCTION_DEBT.get(value)
        if task9_relocated is not None:
            return task9_relocated[0]
        module, separator, suffix = value.partition(":")
        legacy = _DEBT_RELOCATIONS.get(module, module)
        return f"{legacy}{separator}{suffix}"

    def function_lines(row: Mapping[str, object]) -> int:
        relocated = _TASK6_RELOCATED_FUNCTION_DEBT.get(str(row["id"]))
        task9_relocated = _TASK9_RELOCATED_FUNCTION_DEBT.get(str(row["id"]))
        overhead = (
            relocated[1]
            if relocated is not None
            else (task9_relocated[1] if task9_relocated is not None else 0)
        )
        return cast(int, row["lines"]) - overhead

    oversized = [
        {"id": debt_id(module), "path": row["path"], "measured_lines": row["lines"]}
        for module, row in modules.items()
        if isinstance(row, Mapping) and int(row["lines"]) > FINAL_BUDGETS["max_module_lines"]
    ]
    long_functions = [
        {
            "id": debt_id(row["id"]),
            "path": row["path"],
            "qualname": row["qualname"],
            "measured_lines": function_lines(row),
        }
        for row in functions
        if int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
        and str(row["id"]) not in _TASK7_RELOCATED_FUNCTION_DEBT
        and str(row["id"]) not in _TASK8_ALLOCATE_STRATEGY_DEBT
    ]
    task7_long = [
        row
        for row in functions
        if str(row["id"]) in _TASK7_RELOCATED_FUNCTION_DEBT
        and int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
    ]
    if task7_long:
        largest = max(task7_long, key=lambda row: int(row["lines"]))
        long_functions.append(
            {
                "id": "uquant.risk:_assess_base_risk",
                "path": largest["path"],
                "qualname": largest["qualname"],
                "measured_lines": largest["lines"],
            }
        )
    task8_pipeline_long = [
        row
        for row in functions
        if str(row["id"]) in _TASK8_ALLOCATE_STRATEGY_DEBT
        and int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
    ]
    if task8_pipeline_long:
        largest = max(task8_pipeline_long, key=lambda row: int(row["lines"]))
        long_functions.append(
            {
                "id": "uquant.portfolio:PortfolioAllocator._allocate_strategy",
                "path": largest["path"],
                "qualname": largest["qualname"],
                "measured_lines": largest["lines"],
            }
        )
    branchy_functions = [
        {
            "id": debt_id(row["id"]),
            "path": row["path"],
            "qualname": row["qualname"],
            "measured_branch_points": row["branch_points"],
        }
        for row in functions
        if int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
        and str(row["id"]) not in _TASK7_RELOCATED_FUNCTION_DEBT
        and str(row["id"]) not in _TASK8_ALLOCATE_STRATEGY_DEBT
    ]
    task7_branchy = [
        row
        for row in functions
        if str(row["id"]) in _TASK7_RELOCATED_FUNCTION_DEBT
        and int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
    ]
    if task7_branchy:
        branchiest = max(task7_branchy, key=lambda row: int(row["branch_points"]))
        branchy_functions.append(
            {
                "id": "uquant.risk:_assess_base_risk",
                "path": branchiest["path"],
                "qualname": branchiest["qualname"],
                "measured_branch_points": branchiest["branch_points"],
            }
        )
    task8_pipeline_branchy = [
        row
        for row in functions
        if str(row["id"]) in _TASK8_ALLOCATE_STRATEGY_DEBT
        and int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
    ]
    if task8_pipeline_branchy:
        branchiest = max(
            task8_pipeline_branchy,
            key=lambda row: int(row["branch_points"]),
        )
        branchy_functions.append(
            {
                "id": "uquant.portfolio:PortfolioAllocator._allocate_strategy",
                "path": branchiest["path"],
                "qualname": branchiest["qualname"],
                "measured_branch_points": branchiest["branch_points"],
            }
        )
    mutable_globals = [
        {
            "id": debt_id(row["id"]),
            "path": row["path"],
            "name": row["name"],
            "mutable_initializer": row["mutable_initializer"],
            "mutation_sites": row["mutation_sites"],
        }
        for row in globals_
        if bool(row["mutable_initializer"]) or bool(row["mutation_sites"])
    ]
    return {
        "oversized_modules": oversized,
        "long_functions": long_functions,
        "branchy_functions": branchy_functions,
        "cross_module_private_imports": list(imports["cross_module_private_imports"]),
        "mutable_module_globals": mutable_globals,
        "production_type_ignores": [
            {
                **row,
                "id": _TASK8_RELOCATED_TYPE_IGNORES.get(str(row["id"]), row["id"]),
            }
            for row in type_ignores
        ],
        "duplicate_private_helper_groups": list(helpers["same_private_name"]),
        "internal_import_cycles": list(imports["cycles"]),
    }
