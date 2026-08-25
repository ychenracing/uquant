"""Fail-closed inventory for governed private module coupling.

This scanner deliberately does not consult frozen owner-relocation sets.  Those
sets describe historical proof routes; they are not an allowance for current
imports.  Both direct private imports and qualified private module attributes
are current debt.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import _private_import_ast as _private_ast
from . import _private_import_flow as _private_flow
from . import _private_import_inventory as _private_inventory
from ._analysis_authorities import ROOT

REMEDIATION_START_COMMIT = "c0cde6c60bbf234d08e836f84981aa1b3231279b"
PRIVATE_IMPORT_REFERENCE_TREE = "accd244a1bbbabd8f67a0e390f3b6419bfea8270"
GOVERNED_ROOTS = ("uquant", "research", "scripts")
_dynamic_row = _private_inventory.dynamic_row
_record_nested_module_transport = _private_inventory.record_nested_module_transport


def _resolved_module(
    node: ast.expr,
    module_expressions: Mapping[tuple[str, ...], str],
    dynamic_module_expressions: Mapping[tuple[str, ...], list[tuple[int, str]]],
    *,
    line: int,
    sys_names: set[str],
    sys_module_names: set[str],
    importlib_names: set[str],
    import_module_names: set[str],
    builtins_names: set[str],
    modules: set[str],
) -> str | None:
    parts = _private_ast.expression_parts(node)
    if parts is not None:
        imported = _private_ast.static_module_expression(parts, module_expressions, modules)
        if imported is not None:
            return imported
        candidates = [
            item
            for item in dynamic_module_expressions.get(parts, ())
            if item[0] <= line
        ]
        if candidates:
            return max(candidates)[1]
    return _private_ast.literal_runtime_module(
        node,
        sys_names=sys_names,
        sys_module_names=sys_module_names,
        importlib_names=importlib_names,
        import_module_names=import_module_names,
        builtins_names=builtins_names,
        modules=modules,
    )


@dataclass
class _ModuleContext:
    module: str
    relative: str
    is_package: bool
    tree: ast.Module
    modules: set[str]
    bounded_star_modules: Mapping[str, tuple[str, ...]]
    module_object_bindings: Mapping[str, Mapping[str, str]]
    module_expressions: dict[tuple[str, ...], str] = field(default_factory=dict)
    capability_expressions: dict[tuple[str, ...], str] = field(default_factory=dict)
    dynamic_modules: dict[tuple[str, ...], list[tuple[int, str]]] = field(
        default_factory=dict
    )
    sys_names: set[str] = field(default_factory=set)
    sys_module_names: set[str] = field(default_factory=set)
    importlib_names: set[str] = field(default_factory=set)
    import_module_names: set[str] = field(default_factory=lambda: {"__import__"})
    builtins_names: set[str] = field(default_factory=set)
    callable_names: dict[str, str] = field(default_factory=_private_ast.dynamic_builtin_names)
    parents: dict[ast.AST, ast.AST] = field(init=False)
    imported_names: set[str] = field(init=False)
    runtime_class_expressions: set[tuple[str, ...]] = field(init=False, default_factory=set)
    def __post_init__(self) -> None:
        self.parents = _private_ast.parent_map(self.tree)
        self.imported_names = _private_ast.imported_local_names(self.tree)

    def resolve(self, node: ast.expr, *, line: int | None = None) -> str | None:
        resolved = _resolved_module(
            node,
            self.module_expressions,
            self.dynamic_modules,
            line=getattr(node, "lineno", 0) if line is None else line,
            sys_names=self.sys_names,
            sys_module_names=self.sys_module_names,
            importlib_names=self.importlib_names,
            import_module_names=self.import_module_names,
            builtins_names=self.builtins_names,
            modules=self.modules,
        )
        if resolved is not None:
            return resolved
        return self._resolve_namespace_module(
            node,
            line=getattr(node, "lineno", 0) if line is None else line,
        )

    def callable_role(self, node: ast.expr) -> str | None:
        return _private_ast.dynamic_callable_role(
            node,
            callable_names=self.callable_names,
            builtins_names=self.builtins_names,
        )

    def capability_module(self, node: ast.expr) -> str | None:
        if self.is_bounded_ctypes_kernel_call(node):
            return "ctypes.kernel32"
        direct = _private_ast.capability_module(
            node,
            builtins_names=self.builtins_names,
            importlib_names=self.importlib_names,
            sys_names=self.sys_names,
        )
        parts = _private_ast.expression_parts(node)
        return direct or (
            _private_ast.static_module_expression(
                parts,
                self.capability_expressions,
                set(_private_ast.CAPABILITY_MODULES),
            )
            if parts is not None
            else None
        )

    def _ctypes_loader_owner(self, node: ast.expr) -> ast.expr | None:
        if isinstance(node, ast.Attribute) and node.attr == "WinDLL":
            return node.value
        return _private_ast.bounded_vars_member_owner(
            node,
            member="WinDLL",
            callable_names=self.callable_names,
            builtins_names=self.builtins_names,
        )

    def is_bounded_ctypes_kernel_call(self, node: ast.expr) -> bool:
        if not _private_ast.is_bounded_ctypes_kernel_call(node):
            return False
        owner = self._ctypes_loader_owner(node.func)
        return owner is not None and self.capability_module(owner) == "ctypes"

    def is_bounded_ctypes_loader_lookup(self, node: ast.Call) -> bool:
        loader = self.parents.get(node)
        parent = self.parents.get(loader) if isinstance(loader, ast.Subscript) else None
        return (
            isinstance(parent, ast.Call)
            and parent.func is loader
            and self.is_bounded_ctypes_kernel_call(parent)
        )

    def capability_member(self, node: ast.expr) -> tuple[str, str, str] | None:
        if not isinstance(node, ast.Attribute):
            return None
        capability = self.capability_module(node.value)
        resolved = self.resolve(node.value)
        if capability is None and resolved in _private_ast.CAPABILITY_MODULES:
            capability = resolved
        if capability is None:
            return None
        kind = _private_ast.capability_member_kind(capability, node.attr)
        return None if kind is None else (capability, node.attr, kind)

    def record_capability_name(self, name: str, capability: str) -> None:
        dedicated_names = getattr(self, f"{capability}_names", None)
        if dedicated_names is not None:
            dedicated_names.add(name)
        self.capability_expressions[(name,)] = capability

    def record_module_expression(self, expression: tuple[str, ...], target: str) -> None:
        modules, capabilities = _private_flow.expanded_binding_expressions(
            expression, target, self.module_object_bindings
        )
        self.module_expressions.update(modules)
        self.capability_expressions.update(capabilities)

    def record_module_binding(self, name: str, target: str) -> None:
        self.record_module_expression((name,), target)
        if target in _private_ast.CAPABILITY_MODULES:
            self.record_capability_name(name, target)

    def has_module_binding(self, name: str) -> bool:
        return (
            (name,) in self.module_expressions
            or (name,) in self.dynamic_modules
            or name in self.imported_names
            or name in self.builtins_names
            or name in self.importlib_names
            or name in self.sys_names
            or name in self.sys_module_names
        )

    def _namespace_role(self, node: ast.expr) -> str | None:
        if not isinstance(node, ast.Call) or node.args or node.keywords:
            return None
        role = self.callable_role(node.func)
        return role if role in {"globals", "locals", "vars"} else None

    def _is_bounded_export_reflection(self, node: ast.Subscript) -> bool:
        assignment = self.parents.get(node)
        loop = self.parents.get(assignment) if assignment is not None else None
        exports = _private_ast.literal_all_value(self.tree)
        if exports is None:
            return False
        if not (
            isinstance(node.slice, ast.Name)
            and isinstance(assignment, (ast.Assign, ast.AnnAssign))
            and assignment.value is node
            and isinstance(loop, ast.For)
            and isinstance(loop.target, ast.Name)
            and loop.target.id == node.slice.id
            and isinstance(loop.iter, ast.Name)
            and loop.iter.id == "__all__"
            and self.parents.get(loop) is self.tree
        ):
            return False
        if any(
            self.has_module_binding(name)
            for name in exports
        ):
            return False
        references = [
            item for item in ast.walk(self.tree)
            if isinstance(item, ast.Name) and item.id == "__all__"
        ]
        return (
            len(references) == 2
            and {type(item.ctx) for item in references} == {ast.Load, ast.Store}
            and loop.iter in references
        )

    def is_bounded_namespace_call(self, node: ast.Call) -> bool:
        parent = self.parents.get(node)
        return (
            isinstance(parent, ast.Subscript)
            and parent.value is node
            and self._is_bounded_export_reflection(parent)
        )

    def _resolve_namespace_module(self, node: ast.expr, *, line: int) -> str | None:
        lookup = _private_ast.mapping_lookup(node)
        if lookup is None or self._namespace_role(lookup[0]) is None:
            return None
        if isinstance(node, ast.Subscript) and self._is_bounded_export_reflection(node):
            return None
        if isinstance(lookup[1], ast.Constant) and isinstance(lookup[1].value, str):
            parts = (lookup[1].value,)
            imported = self.module_expressions.get(parts)
            if imported is not None:
                return imported
            candidates = [
                item for item in self.dynamic_modules.get(parts, ()) if item[0] <= line
            ]
            return (
                max(candidates)[1]
                if candidates
                else _private_ast.UNKNOWN_MODULE
            )
        return _private_ast.UNKNOWN_MODULE


def _record_import(
    context: _ModuleContext,
    node: ast.Import,
    dynamic: list[dict[str, object]],
) -> None:
    for alias in node.names:
        local = alias.asname or alias.name
        capability = _private_ast.imported_capability_module(
            alias.name, has_alias=alias.asname is not None
        )
        if capability in _private_ast.CAPABILITY_MODULES:
            context.record_capability_name(
                local if alias.asname else capability.split(".", 1)[0], capability
            )
            _record_nested_module_transport(context, node, capability, dynamic)
        if alias.name not in context.modules or alias.name == context.module:
            continue
        expression = (alias.asname,) if alias.asname else tuple(alias.name.split("."))
        context.record_module_expression(expression, alias.name)
        _record_nested_module_transport(context, node, alias.name, dynamic)


def _record_runtime_from_import(
    context: _ModuleContext,
    node: ast.ImportFrom,
    *,
    from_module: str,
) -> None:
    if from_module == "builtins":
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name in _private_ast.DYNAMIC_BUILTIN_KINDS:
                context.callable_names[local] = alias.name
            elif alias.name == "__import__":
                context.import_module_names.add(local)
    elif from_module == "importlib":
        context.import_module_names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name == "import_module"
        )
    elif from_module == "sys":
        context.sys_module_names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name == "modules"
        )


def _record_from_import(
    context: _ModuleContext,
    node: ast.ImportFrom,
    direct: list[dict[str, object]],
    dynamic: list[dict[str, object]],
) -> None:
    from_module = _private_ast.resolve_from(
        context.module,
        is_package=context.is_package,
        level=node.level,
        imported=node.module,
    )
    _record_runtime_from_import(context, node, from_module=from_module)
    has_star = any(alias.name == "*" for alias in node.names)
    star_exports = context.bounded_star_modules.get(from_module)
    imported_names = [alias.name for alias in node.names if alias.name != "*"]
    if has_star and star_exports is not None:
        imported_names.extend(star_exports)
    if has_star and star_exports is None:
        dynamic.append(
            _dynamic_row(
                context,
                imported_from=from_module or "<unresolved-star>",
                name="*",
                kind="unbounded_namespace",
                line=node.lineno,
            )
        )
    if from_module in _private_ast.CAPABILITY_MODULES:
        dynamic.extend(
            _dynamic_row(
                context,
                imported_from=from_module,
                name=name,
                kind=kind,
                line=node.lineno,
                operation_context="ImportFrom",
            )
            for name in imported_names
            if name not in _private_ast.DYNAMIC_DUNDER_KINDS
            if (
                kind := _private_ast.capability_member_kind(from_module, name)
            ) is not None
        )
    if from_module in context.modules and from_module != context.module:
        direct.extend(
            {
                "id": f"{context.module}:{from_module}:{name}",
                "root": context.module.split(".", 1)[0],
                "path": context.relative,
                "importer": context.module,
                "imported_from": from_module,
                "name": name,
                "line": node.lineno,
            }
            for name in imported_names
            if _private_ast.is_private_name(name)
        )
    if (
        from_module in _private_ast.CAPABILITY_MODULES
        or (
            from_module in context.modules
            and from_module != context.module
        )
    ):
        dynamic.extend(
            _dynamic_row(
                context,
                imported_from=from_module,
                name=name,
                kind=kind,
                line=node.lineno,
                operation_context="ImportFrom",
            )
            for name in imported_names
            if (kind := _private_ast.DYNAMIC_DUNDER_KINDS.get(name)) is not None
        )
    transported = [
        (alias.name, alias.asname or alias.name)
        for alias in node.names
        if alias.name != "*"
    ]
    if has_star and star_exports is not None:
        transported.extend((name, name) for name in star_exports)
    for source_name, local_name in transported:
        target = context.module_object_bindings.get(from_module, {}).get(source_name)
        if target is not None:
            context.record_module_binding(local_name, target)
            _record_nested_module_transport(context, node, target, dynamic)
    for alias in node.names:
        candidate = f"{from_module}.{alias.name}".strip(".")
        if (
            candidate in context.modules
            or candidate in _private_ast.CAPABILITY_MODULES
        ) and candidate != context.module:
            context.record_module_binding(alias.asname or alias.name, candidate)
            _record_nested_module_transport(context, node, candidate, dynamic)


def _scan_imports(
    context: _ModuleContext,
    direct: list[dict[str, object]],
    dynamic: list[dict[str, object]],
) -> None:
    for node in ast.walk(context.tree):
        if isinstance(node, ast.Import):
            _record_import(context, node, dynamic)
        elif isinstance(node, ast.ImportFrom):
            _record_from_import(context, node, direct, dynamic)


def _is_import_module_alias(context: _ModuleContext, value: ast.expr) -> bool:
    if isinstance(value, ast.Name):
        return value.id in context.import_module_names
    return (
        isinstance(value, ast.Attribute)
        and (
            (
                isinstance(value.value, ast.Name)
                and (
                    (
                        value.value.id in context.importlib_names
                        and value.attr == "import_module"
                    )
                    or (
                        value.value.id in context.builtins_names
                        and value.attr == "__import__"
                    )
                )
            )
            or (
                value.attr in _private_ast.MAPPING_METHODS
                and _private_ast.is_sys_module_registry(
                    value.value,
                    sys_names=context.sys_names,
                    sys_module_names=context.sys_module_names,
                )
            )
        )
    )


def _record_binding_alias(
    context: _ModuleContext,
    *,
    value: ast.expr,
    target: ast.Name,
    callable_role: str | None,
) -> None:
    if callable_role is not None:
        context.callable_names[target.id] = callable_role
    capability = context.capability_module(value)
    if capability is not None:
        context.record_capability_name(target.id, capability)
    if _is_import_module_alias(context, value):
        context.import_module_names.add(target.id)
    if isinstance(value, ast.Name):
        for names in (
            context.sys_names,
            context.sys_module_names,
            context.importlib_names,
            context.builtins_names,
        ):
            if value.id in names:
                names.add(target.id)
    if _private_ast.is_sys_module_registry(
        value,
        sys_names=context.sys_names,
        sys_module_names=context.sys_module_names,
    ):
        context.sys_module_names.add(target.id)


def _scan_bindings(context: _ModuleContext) -> None:
    assignments = sorted(
        (
            node
            for node in ast.walk(context.tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in assignments:
        value = node.value
        if value is None:
            continue
        callable_role = context.callable_role(value)
        for target in _private_ast.assignment_targets(node):
            if isinstance(target, ast.Name):
                _record_binding_alias(
                    context,
                    value=value,
                    target=target,
                    callable_role=callable_role,
                )
        dynamic_module = context.resolve(value, line=node.lineno)
        if dynamic_module is None or dynamic_module == context.module:
            continue
        for target in _private_ast.assignment_targets(node):
            if isinstance(target, ast.Name):
                if dynamic_module in _private_ast.CAPABILITY_MODULES:
                    context.record_capability_name(target.id, dynamic_module)
                context.dynamic_modules.setdefault((target.id,), []).append(
                    (node.lineno, dynamic_module)
                )


def _scan_qualified(
    context: _ModuleContext,
    qualified: list[dict[str, object]],
) -> None:
    for node in ast.walk(context.tree):
        if not isinstance(node, ast.Attribute) or not _private_ast.is_private_name(
            node.attr
        ):
            continue
        expression = _private_ast.attribute_parts(node.value)
        if expression is None:
            continue
        imported_from = _private_ast.static_module_expression(
            expression, context.module_expressions, context.modules
        )
        if imported_from is None:
            continue
        qualified.append(
            {
                "id": (
                    f"{context.module}:{imported_from}:"
                    f"{node.attr}:qualified:{node.lineno}"
                ),
                "root": context.module.split(".", 1)[0],
                "path": context.relative,
                "importer": context.module,
                "imported_from": imported_from,
                "name": node.attr,
                "line": node.lineno,
                "context": type(node.ctx).__name__,
            }
        )


def _dynamic_call_edge(
    context: _ModuleContext,
    node: ast.Call,
) -> tuple[str, str, str] | None:
    if context.is_bounded_ctypes_loader_lookup(node):
        return None
    if _is_import_module_alias(context, node.func):
        imported_from = _private_ast.UNKNOWN_MODULE
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            imported_from = node.args[0].value
            if imported_from not in context.modules:
                return (
                    (f"<runtime-{imported_from}>", "import_module", "dynamic_lookup")
                    if imported_from in _private_ast.CAPABILITY_MODULES
                    else None
                )
        return imported_from, "import_module", "dynamic_lookup"
    if context.is_bounded_ctypes_kernel_call(node):
        return None
    capability_member = context.capability_member(node.func)
    if capability_member is not None:
        capability, name, kind = capability_member
        return f"<runtime-{capability}>", name, kind
    role = context.callable_role(node.func)
    runtime_class_edge = _private_ast.runtime_class_lookup_edge(
        node, role, context.runtime_class_expressions,
        context.callable_names, context.builtins_names,
    )
    if runtime_class_edge is not None:
        return runtime_class_edge
    if role in {"globals", "locals", "vars"} and not node.args:
        if context.is_bounded_namespace_call(node):
            return None
        return "<runtime-namespace>", "*", "unbounded_namespace"
    if role == "vars" and len(node.args) == 1:
        imported_from = context.resolve(node.args[0])
        capability = context.capability_module(node.args[0])
        member = _private_ast.bounded_namespace_member(node, context.parents)
        if (
            capability is not None
            and _private_ast.is_bounded_capability_namespace_member(
                capability, member, node, context.parents
            )
        ):
            return None
        if imported_from is None and capability is not None:
            imported_from = f"<runtime-{capability}>"
        return (
            None
            if imported_from is None
            else (imported_from, "*", "unbounded_namespace")
        )
    if role in {"getattr", "setattr", "delattr"} and len(node.args) >= 2:
        imported_from = context.resolve(node.args[0])
        capability = context.capability_module(node.args[0])
        literal_name = _private_ast.literal_string(node.args[1])
        if imported_from is None and capability is not None:
            if role == "getattr" and not _private_ast.capability_member_is_dynamic(
                capability, literal_name
            ):
                return None
            imported_from = f"<runtime-{capability}>"
        if (
            imported_from is None
            and literal_name in _private_ast.UNSCOPED_DYNAMIC_DUNDERS
        ):
            kind = _private_ast.DYNAMIC_DUNDER_KINDS[literal_name]
            name = "*" if kind == "unbounded_namespace" else literal_name
            return "<runtime-namespace>", name, kind
        if imported_from is None:
            return _private_ast.runtime_class_transport_edge(node, context.builtins_names)
        name = literal_name or "<dynamic>"
        return imported_from, name, "dynamic_lookup"
    if role in {"compile", "eval", "exec"}:
        return "<runtime-source>", role, "source_exec"
    return _private_ast.runtime_class_transport_edge(node, context.builtins_names)


def _dynamic_attribute_edge(
    context: _ModuleContext,
    node: ast.Attribute,
) -> tuple[str, str, str] | None:
    if node.attr in _private_ast.RUNTIME_NAMESPACE_ATTRIBUTES:
        return "<runtime-frame>", node.attr, "unbounded_namespace"
    expression = _private_ast.attribute_parts(node)
    if (
        expression is not None
        and len(expression) > 1
        and (capability := context.capability_expressions.get(expression)) is not None
    ):
        return f"<runtime-{capability}>", node.attr, "dynamic_lookup"
    dunder_kind = _private_ast.DYNAMIC_DUNDER_KINDS.get(node.attr)
    if dunder_kind is not None:
        imported_from = context.resolve(node.value)
        capability = context.capability_module(node.value)
        if imported_from is None and capability is not None:
            imported_from = f"<runtime-{capability}>"
        if (
            imported_from is None
            and node.attr in _private_ast.UNSCOPED_DYNAMIC_DUNDERS
        ):
            imported_from = "<runtime-namespace>"
        name = "*" if dunder_kind == "unbounded_namespace" else node.attr
        return (
            None if imported_from is None else (imported_from, name, dunder_kind)
        )
    if not _private_ast.is_private_name(node.attr):
        return None
    value_expression = _private_ast.attribute_parts(node.value)
    if value_expression is not None and _private_ast.static_module_expression(
        value_expression, context.module_expressions, context.modules
    ) is not None:
        return None
    capability = context.capability_module(node.value)
    if capability is not None:
        return f"<runtime-{capability}>", node.attr, "dynamic_lookup"
    imported_from = context.resolve(node.value)
    return None if imported_from is None else (imported_from, node.attr, "dynamic_lookup")


def _scan_dynamic_operations(
    context: _ModuleContext,
    dynamic: list[dict[str, object]],
) -> None:
    literal_bindings = _private_ast.literal_string_bindings(context.tree)
    for node in ast.walk(context.tree):
        edge = (
            _dynamic_call_edge(context, node)
            if isinstance(node, ast.Call)
            else _dynamic_attribute_edge(context, node)
            if isinstance(node, ast.Attribute)
            else _private_ast.literal_private_reference_edge(
                node, context.modules - {context.module}, literal_bindings
            )
            or _private_ast.literal_runtime_recovery_edge(node, literal_bindings)
            or _private_ast.implicit_runtime_edge(node)
        )
        if edge is None:
            continue
        imported_from, name, kind = edge
        dynamic.append(
            _dynamic_row(
                context,
                imported_from=imported_from,
                name=name,
                kind=kind,
                line=getattr(node, "lineno", 0),
            )
        )


def _is_allowed_module_use(
    context: _ModuleContext,
    node: ast.expr,
    parent: ast.AST | None,
) -> bool:
    if _private_ast.is_assignment_target(node, parent):
        return True
    if _private_ast.is_simple_alias_value(node, parent):
        return context.parents.get(parent) is context.tree
    if isinstance(parent, ast.Delete) and any(target is node for target in parent.targets):
        return True
    if isinstance(parent, ast.Attribute) and parent.value is node:
        return True
    if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
        return context.callable_role(parent.func) in {
            "delattr",
            "getattr",
            "setattr",
            "vars",
        }
    return False


def _scan_dynamic_transport(
    context: _ModuleContext,
    dynamic: list[dict[str, object]],
) -> None:
    for node in ast.walk(context.tree):
        if not isinstance(node, ast.expr):
            continue
        parent = context.parents.get(node)
        if _private_ast.is_assignment_target(node, parent):
            continue
        callable_role = context.callable_role(node)
        capability_member = context.capability_member(node)
        transported_capability = (
            _is_import_module_alias(context, node)
            or callable_role is not None
            or capability_member is not None
        )
        if transported_capability and not (
            isinstance(parent, ast.Call) and parent.func is node
        ):
            if (
                capability_member is not None
                and capability_member[:2] == ("sys", "modules")
                and _private_ast.is_bounded_registry_access(node, parent)
            ):
                continue
            capability, name = (
                capability_member[:2]
                if capability_member is not None
                else (
                    ("builtins", callable_role)
                    if callable_role is not None
                    else ("loader", "import_module")
                )
            )
            dynamic.append(
                _dynamic_row(
                    context,
                    imported_from=f"<runtime-{capability}>",
                    name=name,
                    kind="dynamic_transport",
                    line=getattr(node, "lineno", 0),
                    operation_context=(
                        type(parent).__name__ if parent is not None else "Root"
                    ),
                )
            )
            continue
        imported_from = context.resolve(node)
        if imported_from is None:
            capability = context.capability_module(node)
            if capability is None:
                continue
            imported_from = f"<runtime-{capability}>"
        if imported_from == context.module:
            continue
        if _is_allowed_module_use(context, node, parent) and (
            imported_from != "<runtime-ctypes.kernel32>"
            or isinstance(parent, ast.Attribute)
        ):
            continue
        dynamic.append(
            _dynamic_row(
                context,
                imported_from=imported_from,
                name="<module-object>",
                kind="dynamic_transport",
                line=getattr(node, "lineno", 0),
                operation_context=(
                    type(parent).__name__ if parent is not None else "Root"
                ),
            )
        )


def _scan_parsed_private_edges(
    parsed: Mapping[str, tuple[str, bool, ast.Module]],
    bounded_star_modules: Mapping[str, tuple[str, ...]],
    module_object_bindings: Mapping[str, Mapping[str, str]],
    *,
    include_runtime_class_recovery: bool,
) -> dict[str, list[dict[str, object]]]:
    modules = set(parsed)
    direct: list[dict[str, object]] = []
    qualified: list[dict[str, object]] = []
    dynamic: list[dict[str, object]] = []
    for module, (relative, is_package, tree) in sorted(parsed.items()):
        context = _ModuleContext(
            module=module,
            relative=relative,
            is_package=is_package,
            tree=tree,
            modules=modules,
            bounded_star_modules=bounded_star_modules,
            module_object_bindings=module_object_bindings,
        )
        _scan_imports(context, direct, dynamic)
        _scan_bindings(context)
        if include_runtime_class_recovery:
            context.runtime_class_expressions = _private_ast.runtime_class_expressions(
                tree,
                callable_names=context.callable_names,
                builtins_names=context.builtins_names,
            )
        _scan_qualified(context, qualified)
        _scan_dynamic_operations(context, dynamic)
        _scan_dynamic_transport(context, dynamic)
    return {
        "direct": sorted(direct, key=lambda row: str(row["id"])),
        "qualified": sorted(qualified, key=lambda row: str(row["id"])),
        "dynamic": sorted(dynamic, key=lambda row: str(row["id"])),
    }


def _scan_governed_private_edges(
    source_texts: Mapping[str, str],
    *,
    include_runtime_class_recovery: bool,
) -> dict[str, list[dict[str, object]]]:
    parsed = _private_flow.parse_governed_sources(source_texts, GOVERNED_ROOTS)
    bounded_star_modules = {
        module: exports
        for module, (_relative, _is_package, tree) in parsed.items()
        if (exports := _private_ast.literal_bounded_exports(tree)) is not None
    }
    return _scan_parsed_private_edges(
        parsed,
        bounded_star_modules,
        _private_flow.module_object_bindings(parsed),
        include_runtime_class_recovery=include_runtime_class_recovery,
    )


def scan_governed_private_edges(
    source_texts: Mapping[str, str],
) -> dict[str, list[dict[str, object]]]:
    """Return every current private edge across governed internal modules."""

    return _scan_governed_private_edges(
        source_texts,
        include_runtime_class_recovery=True,
    )


def _scan_v1_inventory_private_edges(
    source_texts: Mapping[str, str],
) -> dict[str, list[dict[str, object]]]:
    """Replay sealed v1 counts; live acceptance never uses this path."""

    parsed = _private_flow.parse_governed_sources(source_texts, GOVERNED_ROOTS)
    bounded_star_modules = {
        module: ()
        for module, (_relative, _is_package, tree) in parsed.items()
        if _private_ast.literal_all(tree) is not None
    }
    return _scan_parsed_private_edges(
        parsed,
        bounded_star_modules,
        {},
        include_runtime_class_recovery=False,
    )


def current_governed_sources(root: Path = ROOT) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for governed_root in GOVERNED_ROOTS
        for path in sorted((root / governed_root).rglob("*.py"))
    }


def scan_sealed_governed_private_edges(
    root: Path,
    *,
    commit: str,
    tree: str,
) -> dict[str, list[dict[str, object]]]:
    """Scan a fixed historical tree without consulting current source bytes."""

    observed_tree = subprocess.check_output(
        ["git", "rev-parse", f"{commit}^{{tree}}"], cwd=root, text=True
    ).strip()
    assert observed_tree == tree
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", commit, "--", *GOVERNED_ROOTS],
        cwd=root,
        text=True,
    ).splitlines()
    sources = {
        path: subprocess.check_output(
            ["git", "show", f"{commit}:{path}"], cwd=root, text=True
        )
        for path in paths
        if path.endswith(".py")
    }
    assert sources and {
        path.split("/", 1)[0] for path in sources
    } == set(GOVERNED_ROOTS)
    return _scan_v1_inventory_private_edges(sources)


def scan_analysis_governed_private_edges(
    root: Path,
    production_source_texts: Mapping[str, str] | None,
    exact_governed_source_texts: Mapping[str, str] | None,
) -> dict[str, list[dict[str, object]]]:
    """Scan exact governed inputs while supporting production-source mutations."""

    if exact_governed_source_texts is not None:
        sources = dict(exact_governed_source_texts)
        include_runtime_class_recovery = True
    elif production_source_texts is not None:
        sources = dict(production_source_texts)
        # ``source_texts`` also replays the immutable baseline architecture. Keep
        # that sealed projection independent of later live scanner extensions;
        # exact governed candidates opt into the current recovery analysis.
        include_runtime_class_recovery = False
    else:
        sources = current_governed_sources(root)
        include_runtime_class_recovery = True
    observed = _scan_governed_private_edges(
        sources,
        include_runtime_class_recovery=include_runtime_class_recovery,
    )
    stable_fields = ("id", "importer", "imported_from", "name", "line")
    return {
        "direct": [
            {field: row[field] for field in stable_fields}
            for row in observed["direct"]
        ],
        "qualified": [
            {
                **{field: row[field] for field in stable_fields},
                "kind": "qualified",
                "context": row["context"],
            }
            for row in observed["qualified"]
        ],
        "dynamic": [
            {field: row[field] for field in stable_fields} | {
                "kind": row["kind"],
                "context": row["context"],
            }
            for row in observed["dynamic"]
        ],
    }


def build_inventory(root: Path, *, commit: str, tree: str) -> dict[str, object]:
    sources = current_governed_sources(root)
    observed = _scan_v1_inventory_private_edges(sources)
    return _private_inventory.build_inventory_payload(
        sources,
        observed,
        GOVERNED_ROOTS,
        commit=commit,
        tree=tree,
    )


load_inventory = _private_inventory.load_inventory
verify_inventory_seal = _private_inventory.verify_inventory_seal


def build_inventory_from_immutable_git(root: Path = ROOT) -> dict[str, object]:
    relative = _private_inventory.INVENTORY_PATH.relative_to(ROOT).as_posix()
    baseline = subprocess.run(
        [
            "git",
            "show",
            f"105695aacd3d1c7e62705f64188da88d202db4cd:{relative}",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    assert baseline == _private_inventory.INVENTORY_PATH.read_bytes()
    payload = _private_inventory.load_inventory()
    _private_inventory.verify_inventory_seal(payload)
    return payload


def _main() -> None:
    payload = build_inventory_from_immutable_git()
    _private_inventory.write_inventory(_private_inventory.INVENTORY_PATH, payload)
    print(f"{_private_inventory.INVENTORY_PATH}: {payload['artifact_sha256']}")


if __name__ == "__main__":
    _main()
