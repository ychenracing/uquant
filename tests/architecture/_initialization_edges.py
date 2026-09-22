"""Distinguish initialization dependencies and exact compatibility delegation."""
from __future__ import annotations

import ast
from pathlib import Path

from ._analysis_debt import _longest_internal_module, _resolve_from, _strongly_connected_components


class _InitializationImports(ast.NodeVisitor):
    def __init__(self, module: str, path: Path, modules: set[str], tree: ast.Module) -> None:
        self.module, self.path, self.modules = module, path, modules
        self.edges: set[str] = set()
        self.functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        self.visited: set[str] = set()
        self.typing_guard = any(
            isinstance(node, ast.ImportFrom) and node.module == "typing"
            and any(alias.name == "TYPE_CHECKING" and alias.asname is None for alias in node.names)
            for node in tree.body
        ) and not any(
            isinstance(node, ast.Name) and node.id == "TYPE_CHECKING"
            and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(tree)
        )

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        only_types = self.typing_guard and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        for statement in node.orelse if only_types else [*node.body, *node.orelse]:
            self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for expression in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if expression is not None:
                self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in self.functions and node.func.id not in self.visited:
            self.visited.add(node.func.id)
            for statement in self.functions[node.func.id].body:
                self.visit(statement)

    def _add(self, name: str) -> None:
        target = _longest_internal_module(name, self.modules)
        if target is not None and target != self.module:
            self.edges.add(target)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _resolve_from(self.module, is_package=self.path.name == "__init__.py", level=node.level, imported=node.module)
        for name in [base, *(f"{base}.{alias.name}" for alias in node.names)]:
            self._add(name)


def initialization_cycles(root: Path, overrides: dict[str, str] | None = None) -> list[dict[str, object]]:
    """Retain eager cycles, including module-called local bootstrap functions."""
    sources = {
        ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(".__init__"):
        (path, (overrides or {}).get(path.relative_to(root).as_posix(), path.read_text()))
        for path in (root / "uquant").rglob("*.py")
    }
    graph = {}
    for module, (path, source) in sources.items():
        tree = ast.parse(source)
        visitor = _InitializationImports(module, path, set(sources), tree)
        visitor.visit(tree)
        graph[module] = visitor.edges
    return [{"id": "scc:" + ",".join(group), "modules": group}
            for group in _strongly_connected_components(graph) if len(group) > 1]


def assert_cache_facade_delegation(root: Path, override: str | None = None) -> None:
    """A cache facade binds only its schema; no duplicated implementation is excused."""
    expected = ast.parse('''
def _load_risk_timeline_disk_cache(path: Path, *, key: tuple[str, str, str, str]) -> RiskEvidenceTimeline | None:
    return _application.load_risk_timeline_disk_cache(path, _RISK_TIMELINE_CACHE_SCHEMA, key=key)

def _write_risk_timeline_disk_cache(path: Path, *, key: tuple[str, str, str, str], timeline: RiskEvidenceTimeline) -> None:
    _application.write_risk_timeline_disk_cache(path, _RISK_TIMELINE_CACHE_SCHEMA, key=key, timeline=timeline)
''')
    source = override if override is not None else (root / 'uquant/engine.py').read_text()
    actual = ast.parse(source)
    for function in expected.body:
        matches = [node for node in actual.body if isinstance(node, ast.FunctionDef) and node.name == function.name]
        assert len(matches) == 1
        assert ast.dump(matches[0], include_attributes=False) == ast.dump(function, include_attributes=False)


def blocking_architecture_debt(root: Path, debt: dict[str, list[dict[str, object]]]) -> dict[str, list[dict[str, object]]]:
    """Keep raw source metrics intact; resolve only proven non-runtime dependencies."""
    result = dict(debt)
    result['internal_import_cycles'] = initialization_cycles(root)
    duplicates = []
    for group in debt['duplicate_private_helper_groups']:
        members = group['members']
        assert isinstance(members, list)
        facade = (group['name'] in {'_load_risk_timeline_disk_cache', '_write_risk_timeline_disk_cache'}
                  and {row['module'] for row in members} == {'uquant.engine', 'uquant.application.risk_timeline_cache'}
                  and len(members) == 2)
        if facade:
            assert_cache_facade_delegation(root)
        else:
            duplicates.append(group)
    result['duplicate_private_helper_groups'] = duplicates
    return result
