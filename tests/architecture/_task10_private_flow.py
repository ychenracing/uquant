"""Cross-module object-flow proof for Task 10 private-edge governance."""

from __future__ import annotations

import ast
from collections.abc import Mapping

from . import _task10_private_ast as _private_ast

ParsedSources = Mapping[str, tuple[str, bool, ast.Module]]


def parse_governed_sources(
    source_texts: Mapping[str, str],
    governed_roots: tuple[str, ...],
) -> dict[str, tuple[str, bool, ast.Module]]:
    parsed: dict[str, tuple[str, bool, ast.Module]] = {}
    for relative, source in sorted(source_texts.items()):
        if (
            not relative.endswith(".py")
            or relative.split("/", 1)[0] not in governed_roots
        ):
            continue
        module, is_package = _private_ast.module_for_path(relative)
        if module in parsed:
            raise AssertionError(f"duplicate governed module: {module}")
        parsed[module] = (
            relative,
            is_package,
            ast.parse(source, filename=relative, type_comments=True),
        )
    return parsed


def _target_names(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.List, ast.Tuple)):
        return {
            name
            for element in node.elts
            for name in _target_names(element)
        }
    return set()


def _literal_module_call(
    node: ast.expr,
    bindings: Mapping[str, str],
    loader_names: set[str],
    universe: set[str],
) -> str | None:
    if not isinstance(node, ast.Call) or not node.args:
        return None
    target = _private_ast.literal_string(node.args[0])
    if target not in universe:
        return None
    parts = _private_ast.expression_parts(node.func)
    if parts is None:
        return None
    if len(parts) == 1 and parts[0] in loader_names:
        return target
    owner = bindings.get(parts[0])
    if owner == "importlib" and parts[1:] == ("import_module",):
        return target
    if owner == "builtins" and parts[1:] == ("__import__",):
        return target
    return None


def _literal_registry_lookup(
    node: ast.expr,
    bindings: Mapping[str, str],
    universe: set[str],
) -> str | None:
    lookup = _private_ast.mapping_lookup(node)
    if lookup is None:
        return None
    parts = _private_ast.expression_parts(lookup[0])
    if (
        parts is None
        or bindings.get(parts[0]) != "sys"
        or parts[1:] != ("modules",)
    ):
        return None
    target = _private_ast.literal_string(lookup[1])
    return target if target in universe else None


def _resolved_value(
    node: ast.expr,
    bindings: Mapping[str, str],
    loader_names: set[str],
    universe: set[str],
) -> str | None:
    parts = _private_ast.expression_parts(node)
    if parts is not None:
        expressions = {(name,): target for name, target in bindings.items()}
        resolved = _private_ast.static_module_expression(parts, expressions, universe)
        if resolved is not None:
            return resolved
    return _literal_module_call(
        node, bindings, loader_names, universe
    ) or _literal_registry_lookup(node, bindings, universe)


def _module_bindings(
    module: str,
    is_package: bool,
    tree: ast.Module,
    known: Mapping[str, Mapping[str, str]],
    parsed: ParsedSources,
) -> dict[str, str]:
    universe = set(parsed) | set(_private_ast.CAPABILITY_MODULES)
    bindings: dict[str, str] = {}
    loader_names = {"__import__"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                local = alias.asname or target.split(".", 1)[0]
                capability = _private_ast.imported_capability_module(
                    target, has_alias=alias.asname is not None
                )
                if capability in _private_ast.CAPABILITY_MODULES:
                    bindings[local] = capability
                    continue
                if not alias.asname:
                    target = local
                if target in universe:
                    bindings[local] = target
            continue
        if isinstance(node, ast.ImportFrom):
            owner = _private_ast.resolve_from(
                module,
                is_package=is_package,
                level=node.level,
                imported=node.module,
            )
            for alias in node.names:
                if alias.name == "*":
                    exports = (
                        _private_ast.literal_all(parsed[owner][2])
                        if owner in parsed
                        else None
                    )
                    for name in exports or ():
                        if (target := known.get(owner, {}).get(name)) is not None:
                            bindings[name] = target
                    continue
                local = alias.asname or alias.name
                candidate = f"{owner}.{alias.name}".strip(".")
                target = (
                    candidate
                    if candidate in universe
                    else known.get(owner, {}).get(alias.name)
                )
                if target is None:
                    bindings.pop(local, None)
                else:
                    bindings[local] = target
                if owner == "importlib" and alias.name == "import_module":
                    loader_names.add(local)
                if owner == "builtins" and alias.name == "__import__":
                    loader_names.add(local)
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            names = {name for target in targets for name in _target_names(target)}
            resolved = (
                _resolved_value(value, bindings, loader_names, universe)
                if value is not None
                else None
            )
            for name in names:
                if resolved is None:
                    bindings.pop(name, None)
                else:
                    bindings[name] = resolved
                if isinstance(value, ast.Name) and value.id in loader_names:
                    loader_names.add(name)
            continue
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef)):
            bindings.pop(node.name, None)
    return bindings


def module_object_bindings(parsed: ParsedSources) -> dict[str, dict[str, str]]:
    known = {module: {} for module in parsed}
    for _round in range(len(parsed) + 1):
        current = {
            module: _module_bindings(
                module, is_package, tree, known, parsed
            )
            for module, (_relative, is_package, tree) in parsed.items()
        }
        if current == known:
            return current
        known = current
    raise AssertionError("module-object export analysis did not converge")


def expanded_binding_expressions(
    expression: tuple[str, ...],
    target: str,
    bindings: Mapping[str, Mapping[str, str]],
) -> tuple[dict[tuple[str, ...], str], dict[tuple[str, ...], str]]:
    modules: dict[tuple[str, ...], str] = {}
    capabilities: dict[tuple[str, ...], str] = {}
    pending = [(expression, target, frozenset())]
    while pending:
        current_expression, current_target, seen = pending.pop()
        if current_target in _private_ast.CAPABILITY_MODULES:
            capabilities[current_expression] = current_target
            continue
        modules[current_expression] = current_target
        if current_target in seen:
            continue
        next_seen = seen | {current_target}
        pending.extend(
            ((*current_expression, name), nested, next_seen)
            for name, nested in bindings.get(current_target, {}).items()
        )
    return modules, capabilities
