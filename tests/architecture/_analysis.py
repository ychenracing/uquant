"""Deterministic architecture and public-contract analysis used by Task 1 gates.

This module deliberately lives under ``tests``: it measures production code but
is not part of the production strategy surface or its code fingerprint.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import importlib
import inspect
import io
import json
import math
import subprocess
import tarfile
import tomllib
import types
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_API_PATH = ROOT / "benchmarks" / "architecture_refactor_public_api.json"
INVENTORY_PATH = ROOT / "artifacts" / "architecture_refactor" / "baseline_inventory.json"

FINAL_BUDGETS = {
    "max_module_lines": 1000,
    "max_function_lines": 120,
    "max_function_branch_points": 20,
    "max_cross_module_private_imports": 0,
    "max_mutable_module_globals": 0,
    "max_production_type_ignores": 0,
    "max_duplicate_private_helper_groups": 0,
    "max_internal_scc_size": 1,
}

MODULE_AUTHORITIES = {
    "uquant": "production_safe",
    "uquant.__main__": "cli_runner",
    "uquant.account": "production_safe",
    "uquant.atomic_io": "production_safe",
    "uquant.attribution": "production_safe",
    "uquant.broker": "production_safe",
    "uquant.cli": "cli_runner",
    "uquant.config": "production_safe",
    "uquant.config_governance": "production_safe",
    "uquant.contracts": "production_safe",
    "uquant.contracts.json": "production_safe",
    "uquant.contracts.source_surfaces": "production_safe",
    "uquant.data": "production_safe",
    "uquant.engine": "production_safe",
    "uquant.execution": "production_safe",
    "uquant.execution_journal": "production_safe",
    "uquant.features": "production_safe",
    "uquant.industry": "production_safe",
    "uquant.infrastructure": "production_safe",
    "uquant.infrastructure.atomic_io": "production_safe",
    "uquant.infrastructure.file_lock": "production_safe",
    "uquant.leader": "production_safe",
    "uquant.market_risk": "production_safe",
    "uquant.opportunity": "production_safe",
    "uquant.portfolio": "production_safe",
    "uquant.portfolio_core": "production_safe",
    "uquant.portfolio_leaders": "production_safe",
    "uquant.portfolio_recovery": "production_safe",
    "uquant.portfolio_strategic": "production_safe",
    "uquant.provenance": "production_safe",
    "uquant.provenance.source_surfaces": "production_safe",
    "uquant.reference": "production_safe",
    "uquant.reference_registry": "production_safe",
    "uquant.report": "production_safe",
    "uquant.risk": "production_safe",
    "uquant.risk_sector": "production_safe",
    "uquant.risk_sentinel": "production_safe",
    "uquant.risk_sentinel.__main__": "cli_runner",
    "uquant.risk_sentinel.calibration": "validation_runner",
    "uquant.risk_sentinel.cli": "cli_runner",
    "uquant.risk_sentinel.coverage": "production_safe",
    "uquant.risk_sentinel.evidence": "production_safe",
    "uquant.risk_sentinel.history": "production_safe",
    "uquant.risk_sentinel.integration": "production_safe",
    "uquant.risk_sentinel.models": "production_safe",
    "uquant.risk_sentinel.opinion": "production_safe",
    "uquant.risk_sentinel.service": "production_safe",
    "uquant.risk_sentinel.validation": "validation_runner",
    "uquant.types": "production_safe",
    "uquant.validation": "validation_runner",
    "uquant.validation.__main__": "cli_runner",
    "uquant.validation.ai_era": "production_safe",
    "uquant.validation.ci_artifacts": "cli_runner",
    "uquant.validation.cli": "cli_runner",
    "uquant.validation.competitor": "validation_runner",
    "uquant.validation.control_plane": "validation_runner",
    "uquant.validation.equivalence": "validation_runner",
    "uquant.validation.execution_journal": "validation_runner",
    "uquant.validation.generalization": "validation_runner",
    "uquant.validation.generalization_contract": "validation_runner",
    "uquant.validation.generalization_matrix": "validation_runner",
    "uquant.validation.generalization_reference": "validation_runner",
    "uquant.validation.holdout": "validation_runner",
    "uquant.validation.holdout_lanes": "validation_runner",
    "uquant.validation.holdout_runtime": "validation_runner",
    "uquant.validation.manifest": "validation_runner",
    "uquant.validation.promotion": "validation_runner",
    "uquant.validation.replay_evidence": "validation_runner",
    "uquant.validation.universe": "production_safe",
}

_MODULE_AUTHORITY_VALUES = {"production_safe", "validation_runner", "cli_runner"}
_NONPRODUCTION_IMPORT_AUTHORITIES = {"operator_script", "research", "test"}
_RUNNER_AUTHORITIES = {"cli_runner", "validation_runner"}

_MUTABLE_CALLS = {
    "collections.defaultdict",
    "defaultdict",
    "dict",
    "list",
    "set",
}
_MUTATING_METHODS = {
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
}


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


def _row_id(row: dict[str, object]) -> str:
    return str(row["id"])


def architecture_snapshot(
    root: Path = ROOT,
    *,
    source_texts: Mapping[str, str] | None = None,
    module_authorities: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Measure the live production module graph and all explicit debt dimensions."""

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
    private_imports: list[dict[str, object]] = []
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
                    if alias.name.startswith("_") and target_base and target_base != module:
                        private_imports.append(
                            {
                                "id": f"{module}:{target_base}:{alias.name}",
                                "importer": module,
                                "imported_from": target_base,
                                "name": alias.name,
                                "line": node.lineno,
                            }
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
    sorted_private_imports = sorted(private_imports, key=_row_id)
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
    oversized = [
        {"id": module, "path": row["path"], "measured_lines": row["lines"]}
        for module, row in modules.items()
        if isinstance(row, Mapping) and int(row["lines"]) > FINAL_BUDGETS["max_module_lines"]
    ]
    long_functions = [
        {
            "id": row["id"],
            "path": row["path"],
            "qualname": row["qualname"],
            "measured_lines": row["lines"],
        }
        for row in functions
        if int(row["lines"]) > FINAL_BUDGETS["max_function_lines"]
    ]
    branchy_functions = [
        {
            "id": row["id"],
            "path": row["path"],
            "qualname": row["qualname"],
            "measured_branch_points": row["branch_points"],
        }
        for row in functions
        if int(row["branch_points"]) > FINAL_BUDGETS["max_function_branch_points"]
    ]
    mutable_globals = [
        {
            "id": row["id"],
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
        "production_type_ignores": list(type_ignores),
        "duplicate_private_helper_groups": list(helpers["same_private_name"]),
        "internal_import_cycles": list(imports["cycles"]),
    }


def _stable_default(value: object) -> dict[str, object]:
    if value is inspect.Parameter.empty:
        return {"kind": "required"}
    if isinstance(value, float) and not math.isfinite(value):
        return {"kind": "float", "value": str(value)}
    if value is None or isinstance(value, (bool, int, float, str)):
        return {"kind": "value", "value": value}
    if isinstance(value, Enum):
        return {
            "kind": "enum",
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": "dataclass",
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "sha256": canonical_sha256(dataclasses.asdict(value)),
        }
    if isinstance(value, Path):
        return {"kind": "path", "value": value.as_posix()}
    if isinstance(value, (tuple, list)) and all(
        item is None or isinstance(item, (bool, int, float, str)) for item in value
    ):
        return {"kind": type(value).__name__, "value": list(value)}
    return {
        "kind": "object",
        "type": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }


def _annotation(value: object) -> str | None:
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return None
    if isinstance(value, str):
        return value
    return inspect.formatannotation(value)


def signature_contract(callable_: Callable[..., object]) -> dict[str, object]:
    try:
        signature = inspect.signature(callable_)
    except (TypeError, ValueError):
        return {"parameters": None, "return": None, "unavailable": True}
    return {
        "parameters": [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "annotation": _annotation(parameter.annotation),
                "default": _stable_default(parameter.default),
            }
            for parameter in signature.parameters.values()
        ],
        "return": _annotation(signature.return_annotation),
    }


def _field_default(field: dataclasses.Field[object]) -> dict[str, object]:
    if field.default is not dataclasses.MISSING:
        return _stable_default(field.default)
    if field.default_factory is not dataclasses.MISSING:
        factory = cast(Callable[..., object], field.default_factory)
        return {
            "kind": "factory",
            "callable": f"{factory.__module__}.{factory.__qualname__}",
        }
    return {"kind": "required"}


def _source_public_names(path: Path, module: types.ModuleType) -> list[str]:
    explicit = getattr(module, "__all__", None)
    if explicit is not None:
        return list(explicit)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _assigned_names(target):
                    if name.isupper():
                        names.add(name)
    return sorted(names)


def _class_contract(value: type[object]) -> dict[str, object]:
    methods: dict[str, object] = {}
    properties: list[str] = []
    for name, member in value.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(member, property):
            properties.append(name)
            continue
        if isinstance(member, (staticmethod, classmethod)):
            member = member.__func__
        if inspect.isfunction(member):
            methods[name] = signature_contract(member)
    return {
        "signature": signature_contract(value),
        "methods": {name: methods[name] for name in sorted(methods)},
        "properties": sorted(properties),
    }


def public_module_contract(module: str, root: Path = ROOT) -> dict[str, object]:
    imported = importlib.import_module(module)
    relative = Path(*module.split("."))
    module_path = root / relative.with_suffix(".py")
    if not module_path.exists():
        module_path = root / relative / "__init__.py"
    names = _source_public_names(module_path, imported)
    callables: dict[str, object] = {}
    classes: dict[str, object] = {}
    dataclass_contracts: dict[str, object] = {}
    enum_contracts: dict[str, object] = {}
    for name in names:
        value = getattr(imported, name)
        if inspect.isclass(value):
            classes[name] = _class_contract(value)
            if dataclasses.is_dataclass(value):
                dataclass_contracts[name] = {
                    "fields": [
                        {
                            "name": field.name,
                            "type": _annotation(field.type),
                            "default": _field_default(field),
                        }
                        for field in dataclasses.fields(value)
                    ]
                }
            if issubclass(value, Enum):
                enum_contracts[name] = [
                    {"name": member.name, "value": member.value} for member in value
                ]
        elif inspect.isfunction(value):
            callables[name] = signature_contract(value)
    return {
        "path": module_path.relative_to(root).as_posix(),
        "public_names": names,
        "functions": {name: callables[name] for name in sorted(callables)},
        "classes": {name: classes[name] for name in sorted(classes)},
        "dataclasses": {
            name: dataclass_contracts[name] for name in sorted(dataclass_contracts)
        },
        "enums": {name: enum_contracts[name] for name in sorted(enum_contracts)},
    }


def public_module_names(root: Path = ROOT) -> tuple[str, ...]:
    return tuple(
        module_name(root, path)
        for path in production_sources(root)
        if path.name != "__main__.py"
    )


def cli_help_snapshot(root: Path = ROOT) -> dict[str, str]:
    from argparse import ArgumentParser, _SubParsersAction

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    scripts = project["scripts"]
    if not isinstance(scripts, dict) or not scripts:
        raise AssertionError("[project.scripts] must declare at least one CLI")
    result: dict[str, str] = {}

    def collect(current: ArgumentParser, command: str) -> None:
        current.prog = command
        result[command] = current.format_help()
        for action in current._actions:
            if not isinstance(action, _SubParsersAction):
                continue
            for name, child in action.choices.items():
                collect(child, f"{command} {name}")

    for script, entrypoint in sorted(scripts.items()):
        if not isinstance(script, str) or not isinstance(entrypoint, str):
            raise AssertionError("project script names and entrypoints must be strings")
        module_name_, separator, _ = entrypoint.partition(":")
        if separator != ":":
            raise AssertionError(f"project script entrypoint is malformed: {entrypoint}")
        module = importlib.import_module(module_name_)
        parser_factory = getattr(module, "_parser", None)
        if not callable(parser_factory):
            raise AssertionError(f"project script {script} has no complete _parser registry")
        collect(parser_factory(), script)
    return {name: result[name] for name in sorted(result)}


def _captured_exception(call: Callable[[], object]) -> dict[str, object]:
    try:
        call()
    except Exception as exc:
        return {"type": type(exc).__name__, "message": str(exc)}
    raise AssertionError("exception characterization case did not raise")


def typical_exception_snapshot() -> dict[str, object]:
    from uquant.account import account_from_dict
    from uquant.config import DEFAULT_CONFIG
    from uquant.data import normalize_symbol
    from uquant.engine import ProductionEngine
    from uquant.types import ACCOUNT_SCHEMA_VERSION

    return {
        "account_future_schema": _captured_exception(
            lambda: account_from_dict({"schema_version": ACCOUNT_SCHEMA_VERSION + 1})
        ),
        "config_unknown_override": _captured_exception(
            lambda: DEFAULT_CONFIG.override(not_a_governed_parameter=True)
        ),
        "invalid_symbol": _captured_exception(lambda: normalize_symbol("not-a-symbol")),
        "pre_ai_backtest": _captured_exception(
            lambda: ProductionEngine(ROOT / "data" / "frozen").backtest(
                symbols=("sz300308",), start="2022-12-30", end="2023-01-10"
            )
        ),
    }


def decision_fill_account_trace(root: Path = ROOT) -> dict[str, object]:
    from uquant.config import config_fingerprint
    from uquant.engine import ProductionEngine
    from uquant.types import AccountState

    symbols = ("sz300308", "sz300502", "sz300394")
    engine = ProductionEngine(root / "data" / "frozen")
    account = AccountState.empty(2_000_000.0)
    initial_payload = account.to_dict()
    engine.decide(symbols=symbols, as_of="2023-01-03", account=account)
    decision = engine.decide(symbols=symbols, as_of="2023-01-04", account=account)
    account.pending_orders = list(decision.pending_orders)
    fills = engine.execution.execute_open(
        date=importlib.import_module("pandas").Timestamp("2023-01-05"),
        account=account,
        panel={symbol: engine._raw[symbol] for symbol in symbols},
    )
    return {
        "inputs": {
            "symbols": list(symbols),
            "warmup_decision_date": "2023-01-03",
            "signal_date": "2023-01-04",
            "fill_date": "2023-01-05",
            "initial_cash": 2_000_000.0,
        },
        "initial_account_sha256": canonical_sha256(initial_payload),
        "decision": decision.canonical_payload(
            effective_config_sha256=config_fingerprint(engine.cfg)
        ),
        "fills": [dataclasses.asdict(fill) for fill in fills],
        "account_after": account.to_dict(),
        "account_after_sha256": canonical_sha256(account.to_dict()),
    }


def public_api_snapshot(
    modules: Iterable[str] | None = None, root: Path = ROOT
) -> dict[str, object]:
    from uquant import __version__
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.types import ACCOUNT_SCHEMA_VERSION, AccountState

    selected = tuple(modules or public_module_names(root))
    empty_account = AccountState.empty(DEFAULT_CONFIG.initial_cash).to_dict()
    return {
        "package_version": __version__,
        "modules": {module: public_module_contract(module, root) for module in selected},
        "flat_config_serialization": {
            "field_order": [field.name for field in dataclasses.fields(DEFAULT_CONFIG)],
            "values": dataclasses.asdict(DEFAULT_CONFIG),
            "sha256": config_fingerprint(DEFAULT_CONFIG),
        },
        "account_state_schema": {
            "schema_version": ACCOUNT_SCHEMA_VERSION,
            "field_order": [field.name for field in dataclasses.fields(AccountState)],
            "serialized_key_order": list(empty_account),
            "empty_state": empty_account,
            "empty_state_sha256": canonical_sha256(empty_account),
        },
        "cli_help": cli_help_snapshot(),
        "typical_exceptions": typical_exception_snapshot(),
        "decision_fill_account_trace": decision_fill_account_trace(root),
    }


def _authority(path: str) -> str:
    if path.startswith("uquant/"):
        return "production"
    if path.startswith("data/frozen/"):
        return "frozen_data"
    if path.startswith("benchmarks/"):
        return "reviewed_contract"
    if path.startswith("artifacts/"):
        return "machine_evidence"
    if path.startswith("tests/"):
        return "test"
    if path.startswith("research/"):
        return "research"
    if path.startswith("scripts/"):
        return "operator_script"
    if path.startswith("docs/") or path in {"README.md", "LICENSE", "AGENTS.md"}:
        return "documentation"
    if path.startswith(".github/"):
        return "ci"
    if path in {"pyproject.toml", "uv.lock", "requirements.txt"}:
        return "dependency_or_build"
    return "repository_metadata"


def tracked_file_inventory(root: Path, commit: str) -> dict[str, object]:
    output = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--long", commit],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    entries: list[dict[str, object]] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        header, path_bytes = raw.split(b"\t", 1)
        mode, kind, oid, size = header.decode("ascii").split()
        path = path_bytes.decode("utf-8")
        entries.append(
            {
                "path": path,
                "mode": mode,
                "kind": kind,
                "git_oid": oid,
                "bytes": None if size == "-" else int(size),
                "authority": _authority(path),
            }
        )
    entries.sort(key=lambda row: str(row["path"]))
    counts: dict[str, int] = defaultdict(int)
    for row in entries:
        counts[str(row["authority"])] += 1
    return {
        "commit": commit,
        "entries": entries,
        "entry_count": len(entries),
        "authority_counts": dict(sorted(counts.items())),
        "canonical_sha256": canonical_sha256(entries),
    }


def representative_replay(
    *,
    name: str,
    start: str,
    end: str,
    symbols: Sequence[str],
    root: Path = ROOT,
    account_code_hash: str | None = None,
) -> dict[str, object]:
    from uquant.engine import ProductionEngine

    result = ProductionEngine(root / "data" / "frozen").backtest(
        symbols=symbols,
        start=start,
        end=end,
    )
    metrics = {
        key: result[key]
        for key in (
            "start",
            "end",
            "final_wealth",
            "final_equity",
            "max_drawdown",
            "account_orders",
            "submitted_account_orders",
            "gross_turnover",
            "annual_turnover",
            "effective_config_sha256",
        )
    }
    raw_final_account = result["final_account"]
    if not isinstance(raw_final_account, dict):
        raise AssertionError("representative replay final account must be a mapping")
    final_account = dict(raw_final_account)
    if account_code_hash is not None:
        if not isinstance(final_account.get("code_hash"), str):
            raise AssertionError("representative account lacks its source identity")
        final_account["code_hash"] = account_code_hash
    return {
        "name": name,
        "symbols": list(symbols),
        "requested_start": start,
        "requested_end": end,
        "metrics": metrics,
        "decision_digests_sha256": canonical_sha256(result["decision_digests"]),
        "final_account_sha256": canonical_sha256(final_account),
        "daily_replay_evidence_sha256": canonical_sha256(result["daily_replay_evidence"]),
    }


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def quiet_stderr() -> contextlib.AbstractContextManager[io.StringIO]:
    return contextlib.redirect_stderr(io.StringIO())
