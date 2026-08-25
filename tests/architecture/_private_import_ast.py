"""Pure AST proofs shared by Task 10 private-edge governance."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from pathlib import Path

MAPPING_METHODS = {"__getitem__", "get", "pop", "setdefault"}
UNKNOWN_MODULE = "<dynamic-module>"
DYNAMIC_BUILTIN_KINDS = {
    "compile": "source_exec",
    "delattr": "dynamic_lookup",
    "eval": "source_exec",
    "exec": "source_exec",
    "getattr": "dynamic_lookup",
    "globals": "unbounded_namespace",
    "locals": "unbounded_namespace",
    "setattr": "dynamic_lookup",
    "vars": "unbounded_namespace",
}


def dynamic_builtin_names() -> dict[str, str]:
    return {name: name for name in DYNAMIC_BUILTIN_KINDS}


CAPABILITY_DYNAMIC_MEMBERS: Mapping[str, frozenset[str]] = {
    "builtins": frozenset(
        {
            "__import__",
            "compile",
            "delattr",
            "eval",
            "exec",
            "getattr",
            "globals",
            "locals",
            "setattr",
            "vars",
        }
    ),
    "importlib": frozenset({"import_module"}),
    "logging": frozenset({"config"}),
    "multiprocessing": frozenset({"reduction"}),
    "numpy": frozenset({"load"}),
    "numpy.lib._format_impl": frozenset({"read_array"}),
    "numpy.lib._npyio_impl": frozenset({"NpzFile", "load"}),
    "numpy.lib.format": frozenset({"read_array"}),
    "numpy.lib.npyio": frozenset({"NpzFile", "load"}),
    "operator": frozenset({"attrgetter", "methodcaller"}),
    "pandas": frozenset({"read_pickle"}),
    "pandas.io.api": frozenset({"read_pickle"}),
    "pandas.io.pickle": frozenset({"read_pickle"}),
    "sys": frozenset({"modules"}),
    "unittest": frozenset({"mock"}),
    "yaml": frozenset(
        {
            "CFullLoader",
            "CLoader",
            "CUnsafeLoader",
            "FullLoader",
            "Loader",
            "UnsafeLoader",
            "full_load",
            "full_load_all",
            "load",
            "load_all",
            "unsafe_load",
            "unsafe_load_all",
        }
    ),
    "yaml.cyaml": frozenset(
        {"CFullLoader", "CLoader", "CUnsafeLoader"}
    ),
    "yaml.loader": frozenset({"FullLoader", "Loader", "UnsafeLoader"}),
}
REFLECTIVE_CAPABILITY_SAFE_MEMBERS: Mapping[str, frozenset[str]] = {
    "_ctypes": frozenset(),
    "_frozen_importlib": frozenset(),
    "_frozen_importlib_external": frozenset(),
    "_imp": frozenset(),
    "_pickle": frozenset(),
    "cloudpickle": frozenset(),
    "ctypes": frozenset(
        {
            "POINTER",
            "Structure",
            "byref",
            "c_size_t",
            "get_last_error",
            "wintypes",
        }
    ),
    "ctypes.kernel32": frozenset({"LockFileEx", "UnlockFileEx"}),
    "dill": frozenset(),
    "gc": frozenset(),
    "importlib.metadata": frozenset({"version"}),
    "importlib._bootstrap": frozenset(),
    "importlib._bootstrap_external": frozenset(),
    "inspect": frozenset({"getfile"}),
    "importlib.machinery": frozenset(),
    "importlib.util": frozenset(),
    "joblib": frozenset(),
    "logging.config": frozenset(),
    "marshal": frozenset(),
    "mock": frozenset(),
    "multiprocessing.reduction": frozenset(),
    "pandas.compat.pickle_compat": frozenset(),
    "pickle": frozenset(),
    "pkg_resources": frozenset(),
    "pkgutil": frozenset(),
    "pydoc": frozenset(),
    "runpy": frozenset(),
    "shelve": frozenset(),
    "traceback": frozenset({"format_exception"}),
    "unittest.mock": frozenset(),
    "zipimport": frozenset(),
}
CAPABILITY_MODULES = frozenset(
    {*CAPABILITY_DYNAMIC_MEMBERS, *REFLECTIVE_CAPABILITY_SAFE_MEMBERS}
)
SELECTIVE_CAPABILITY_MODULES = frozenset(
    {"logging", "multiprocessing", "numpy", "operator", "pandas", "unittest", "yaml"}
)
DYNAMIC_DUNDER_KINDS: Mapping[str, str] = {
    "__base__": "unbounded_namespace",
    "__bases__": "unbounded_namespace",
    "__builtins__": "unbounded_namespace",
    "__closure__": "unbounded_namespace",
    "__dict__": "unbounded_namespace",
    "__getattr__": "dynamic_lookup",
    "__getattribute__": "dynamic_lookup",
    "__globals__": "unbounded_namespace",
    "__import__": "source_exec",
    "__loader__": "source_exec",
    "__mro__": "unbounded_namespace",
    "__spec__": "dynamic_lookup",
    "__subclasses__": "unbounded_namespace",
    "mro": "unbounded_namespace",
}
UNSCOPED_DYNAMIC_DUNDERS = frozenset(
    {
        "__builtins__",
        "__base__",
        "__bases__",
        "__closure__",
        "__getattr__",
        "__getattribute__",
        "__globals__",
        "__import__",
        "__mro__",
        "__subclasses__",
        "mro",
    }
)
IMPLICIT_RUNTIME_NAMES = frozenset({"__builtins__", "__loader__", "__spec__"})
RUNTIME_NAMESPACE_ATTRIBUTES = frozenset(
    {"ag_frame", "cr_frame", "f_back", "f_builtins", "f_globals", "f_locals", "gi_frame", "tb_frame"}
)


def parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def implicit_runtime_edge(node: ast.AST) -> tuple[str, str, str] | None:
    if not (
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id in IMPLICIT_RUNTIME_NAMES
    ):
        return None
    kind = DYNAMIC_DUNDER_KINDS[node.id]
    return "<runtime-namespace>", "*" if kind == "unbounded_namespace" else node.id, kind


def attribute_parts(node: ast.expr) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def is_private_name(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _literal_payload_text(
    node: ast.AST,
    bindings: Mapping[str, str] | None = None,
) -> str | None:
    known = {} if bindings is None else bindings
    if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, str)):
        return (
            node.value.decode("utf-8", errors="ignore")
            if isinstance(node.value, bytes)
            else node.value
        )
    if isinstance(node, ast.Name):
        return known.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_payload_text(node.left, known)
        right = _literal_payload_text(node.right, known)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts = [
            _literal_payload_text(
                value.value if isinstance(value, ast.FormattedValue) else value,
                known,
            )
            for value in node.values
        ]
        if any(part is None for part in parts):
            return None
        return "".join(part for part in parts if part is not None)
    return None


def literal_string_bindings(tree: ast.Module) -> dict[str, str]:
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                assignments.setdefault(target.id, []).append(node.value)
    unique = {
        name: values[0] for name, values in assignments.items() if len(values) == 1
    }
    bindings: dict[str, str] = {}
    for _round in range(len(unique) + 1):
        changed = False
        for name, value in unique.items():
            literal = _literal_payload_text(value, bindings)
            if literal is not None and bindings.get(name) != literal:
                bindings[name] = literal
                changed = True
        if not changed:
            return bindings
    raise AssertionError("literal string binding analysis did not converge")


def literal_private_reference_edge(
    node: ast.AST,
    modules: set[str],
    bindings: Mapping[str, str] | None = None,
) -> tuple[str, str, str] | None:
    text = _literal_payload_text(node, bindings)
    if text is None:
        return None
    private_names = sorted(
        {
            token
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)
            if is_private_name(token)
        }
    )
    if not private_names:
        return None
    for module in sorted(modules, key=lambda value: (-len(value), value)):
        for name in private_names:
            pattern = (
                re.escape(module)
                + r"(?:[.:/\\]|\r?\n)+"
                + re.escape(name)
            )
            if re.search(pattern, text):
                return module, name, "serialized_private_reference"
    return None


def literal_runtime_recovery_edge(
    node: ast.AST,
    bindings: Mapping[str, str],
) -> tuple[str, str, str] | None:
    """Reject statically recoverable class-enumeration member names."""

    return (
        ("<runtime-class>", "*", "unbounded_namespace")
        if _literal_payload_text(node, bindings) == "__subclasses__"
        else None
    )


def _runtime_class_target_expressions(node: ast.expr) -> set[tuple[str, ...]]:
    parts = expression_parts(node)
    if parts is not None:
        return {parts}
    if isinstance(node, (ast.List, ast.Tuple)):
        return {
            expression
            for element in node.elts
            for expression in _runtime_class_target_expressions(element)
        }
    if isinstance(node, ast.Subscript):
        return _runtime_class_target_expressions(node.value)
    if isinstance(node, ast.Starred):
        return _runtime_class_target_expressions(node.value)
    return set()


def runtime_class_expressions(
    tree: ast.Module,
    *,
    callable_names: Mapping[str, str],
    builtins_names: set[str],
) -> set[tuple[str, ...]]:
    """Return finite expressions that may transport a runtime class object."""

    expressions = {("object",), ("type",)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            expressions.update(
                (alias.asname or alias.name,)
                for alias in node.names
                if alias.name in {"object", "type"}
            )
    expressions.update(
        (builtins_name, class_name)
        for builtins_name in {"builtins", *builtins_names}
        for class_name in ("object", "type")
    )
    bindings: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            bindings.extend((target, node.value) for target in node.targets)
        elif (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ) or isinstance(node, (ast.AugAssign, ast.NamedExpr)):
            bindings.append((node.target, node.value))
        elif isinstance(node, (ast.AsyncFor, ast.For, ast.comprehension)):
            bindings.append((node.target, node.iter))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    for _round in range(len(bindings) + len(functions) + 1):
        before = len(expressions)
        for target, value in bindings:
            if is_runtime_class_expression(
                value,
                expressions,
                callable_names=callable_names,
                builtins_names=builtins_names,
            ):
                expressions.update(
                    _runtime_class_target_expressions(target)
                )
        for function in functions:
            if any(
                isinstance(child, (ast.Return, ast.Yield, ast.YieldFrom))
                and child.value is not None
                and is_runtime_class_expression(
                    child.value,
                    expressions,
                    callable_names=callable_names,
                    builtins_names=builtins_names,
                )
                for child in ast.walk(function)
            ):
                expressions.add((function.name,))
        if len(expressions) == before:
            return expressions
    raise AssertionError("runtime class alias analysis did not converge")


def is_runtime_class_expression(
    node: ast.expr,
    expressions: set[tuple[str, ...]],
    *,
    callable_names: Mapping[str, str],
    builtins_names: set[str],
) -> bool:
    parts = expression_parts(node)
    if parts is not None and parts in expressions:
        return True
    if (
        isinstance(node, ast.Call)
        and len(node.args) >= 2
        and literal_string(node.args[1]) is None
        and dynamic_callable_role(
            node.func,
            callable_names=callable_names,
            builtins_names=builtins_names,
        )
        == "getattr"
    ):
        return True
    return any(
        is_runtime_class_expression(
            child,
            expressions,
            callable_names=callable_names,
            builtins_names=builtins_names,
        )
        for child in ast.iter_child_nodes(node)
        if isinstance(child, ast.expr)
    )


def runtime_class_lookup_edge(
    node: ast.Call,
    role: str | None,
    expressions: set[tuple[str, ...]],
    callable_names: Mapping[str, str],
    builtins_names: set[str],
) -> tuple[str, str, str] | None:
    """Reject dynamic lookup on a runtime class object."""

    def is_runtime(value: ast.expr) -> bool:
        return is_runtime_class_expression(
            value,
            expressions,
            callable_names=callable_names,
            builtins_names=builtins_names,
        )

    return (
        ("<runtime-class>", "*", "unbounded_namespace")
        if role in {"delattr", "getattr", "setattr", "vars"}
        and node.args
        and is_runtime(node.args[0])
        else None
    )


def runtime_class_transport_edge(
    node: ast.Call,
    builtins_names: set[str],
) -> tuple[str, str, str] | None:
    """Reject explicit class objects passed to an unknown consumer."""

    def is_explicit(value: ast.expr) -> bool:
        parts = expression_parts(value)
        if parts in {("object",), ("type",)} or (
            parts is not None
            and len(parts) == 2
            and parts[0] in {"builtins", *builtins_names}
            and parts[1] in {"object", "type"}
        ):
            return True
        if isinstance(value, ast.Call):
            return is_explicit(value.func)
        if isinstance(value, (ast.List, ast.Set, ast.Tuple)):
            return any(is_explicit(element) for element in value.elts)
        if isinstance(value, ast.Dict):
            return any(is_explicit(item) for item in value.values)
        if isinstance(value, ast.IfExp):
            return is_explicit(value.body) or is_explicit(value.orelse)
        if isinstance(value, ast.NamedExpr):
            return is_explicit(value.value)
        if isinstance(value, ast.Starred):
            return is_explicit(value.value)
        return False

    parts = expression_parts(node.func)
    if parts is not None and parts[-1] in {"cast", "format_exception", "isinstance", "issubclass"}:
        return None
    transported = (*node.args, *(keyword.value for keyword in node.keywords))
    return (
        ("<runtime-class>", "<transport>", "dynamic_transport")
        if any(is_explicit(value) for value in transported)
        else None
    )


def capability_module(
    node: ast.expr,
    *,
    builtins_names: set[str],
    importlib_names: set[str],
    sys_names: set[str],
) -> str | None:
    if not isinstance(node, ast.Name):
        return None
    if node.id in builtins_names:
        return "builtins"
    if node.id in importlib_names:
        return "importlib"
    if node.id in sys_names:
        return "sys"
    return None


def capability_member_is_dynamic(capability: str, name: str | None) -> bool:
    safe_members = REFLECTIVE_CAPABILITY_SAFE_MEMBERS.get(capability)
    if safe_members is not None:
        return name is None or is_private_name(name) or name not in safe_members
    return (
        name is None
        or is_private_name(name)
        or name in CAPABILITY_DYNAMIC_MEMBERS[capability]
        or f"{capability}.{name}" in CAPABILITY_MODULES
    )


def capability_member_kind(capability: str, name: str) -> str | None:
    safe_members = REFLECTIVE_CAPABILITY_SAFE_MEMBERS.get(capability)
    if safe_members is not None:
        return None if name in safe_members else "dynamic_lookup"
    if name not in CAPABILITY_DYNAMIC_MEMBERS[capability]:
        return None
    if capability == "builtins":
        return DYNAMIC_BUILTIN_KINDS.get(name) or DYNAMIC_DUNDER_KINDS[name]
    if capability == "sys":
        return "unbounded_namespace"
    return "dynamic_lookup"


def module_for_path(relative: str) -> tuple[str, bool]:
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    is_package = bool(parts) and parts[-1] == "__init__"
    if is_package:
        parts.pop()
    if not parts:
        raise AssertionError(f"governed Python path has no module: {relative}")
    return ".".join(parts), is_package


def resolve_from(
    module: str,
    *,
    is_package: bool,
    level: int,
    imported: str | None,
) -> str:
    if level == 0:
        return imported or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    ascend = level - 1
    if ascend > len(package):
        return ""
    base = package[: len(package) - ascend] if ascend else package
    if imported:
        base.extend(imported.split("."))
    return ".".join(base)


def dynamic_callable_role(
    node: ast.expr,
    *,
    callable_names: Mapping[str, str],
    builtins_names: set[str],
) -> str | None:
    if isinstance(node, ast.Name):
        return callable_names.get(node.id)
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in builtins_names
        and node.attr in DYNAMIC_BUILTIN_KINDS
    ):
        return node.attr
    return None


def assignment_targets(node: ast.Assign | ast.AnnAssign) -> tuple[ast.expr, ...]:
    return tuple(node.targets) if isinstance(node, ast.Assign) else (node.target,)


def is_simple_alias_value(node: ast.expr, parent: ast.AST | None) -> bool:
    if isinstance(parent, ast.Assign) and parent.value is node:
        return all(isinstance(target, ast.Name) for target in parent.targets)
    return (
        isinstance(parent, ast.AnnAssign)
        and parent.value is node
        and isinstance(parent.target, ast.Name)
    )


def is_assignment_target(node: ast.expr, parent: ast.AST | None) -> bool:
    if isinstance(parent, ast.Assign):
        return any(target is node for target in parent.targets)
    return isinstance(parent, ast.AnnAssign) and parent.target is node


def imported_capability_module(module: str, *, has_alias: bool) -> str:
    root = module.split(".", 1)[0]
    return module if has_alias and module in CAPABILITY_MODULES else root


def is_bounded_registry_access(node: ast.expr, parent: ast.AST | None) -> bool:
    return (
        isinstance(parent, ast.Subscript) and parent.value is node
    ) or (
        isinstance(parent, ast.Attribute)
        and parent.value is node
        and parent.attr in MAPPING_METHODS
    )


def _all_declaration(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == "__all__"
        for target in node.targets
    ):
        return node.value
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.value if node.target.id == "__all__" else None
    return None


def literal_all_value(tree: ast.Module) -> tuple[str, ...] | None:
    declarations = [
        value for node in tree.body if (value := _all_declaration(node)) is not None
    ]
    if len(declarations) != 1:
        return None
    try:
        value = ast.literal_eval(declarations[0])
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(value, tuple)
        or not value
        or not all(isinstance(name, str) for name in value)
        or len(value) != len(set(value))
    ):
        return None
    return tuple(value)


def literal_all(tree: ast.Module) -> tuple[str, ...] | None:
    references = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "__all__"
    ]
    if len(references) != 1 or not isinstance(references[0].ctx, ast.Store):
        return None
    return literal_all_value(tree)


def imported_local_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return names


def literal_bounded_exports(tree: ast.Module) -> tuple[str, ...] | None:
    exports = literal_all(tree)
    if exports is None:
        return None
    proofs: dict[str, list[bool]] = {}
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            proofs.setdefault(node.name, []).append(not node.decorator_list)
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                proofs.setdefault(
                    alias.asname or alias.name.split(".", 1)[0], []
                ).append(False)
            continue
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    proofs.setdefault(alias.asname or alias.name, []).append(False)
            continue
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        literal = False
        if value is not None:
            try:
                ast.literal_eval(value)
            except (TypeError, ValueError):
                pass
            else:
                literal = True
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                proofs.setdefault(target.id, []).append(literal)
    return (
        exports
        if all(proofs.get(name) == [True] for name in exports)
        else None
    )


def expression_parts(node: ast.expr) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return (node.id,)
    return attribute_parts(node)


def static_module_expression(
    parts: tuple[str, ...],
    module_expressions: Mapping[tuple[str, ...], str],
    modules: set[str],
) -> str | None:
    for length in range(len(parts), 0, -1):
        imported = module_expressions.get(parts[:length])
        if imported is None:
            continue
        candidate = ".".join((imported, *parts[length:]))
        if candidate in modules:
            return candidate
    return None


def literal_string(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def mapping_lookup(node: ast.expr) -> tuple[ast.expr, ast.expr] | None:
    if isinstance(node, ast.Subscript):
        return node.value, node.slice
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in MAPPING_METHODS
        and node.args
    ):
        return node.func.value, node.args[0]
    return None


def bounded_namespace_member(
    node: ast.Call, parents: Mapping[ast.AST, ast.AST]
) -> str | None:
    parent = parents.get(node)
    lookup: ast.expr | None = None
    if isinstance(parent, ast.Subscript) and parent.value is node:
        lookup = parent
    elif (
        isinstance(parent, ast.Attribute)
        and parent.value is node
        and parent.attr in MAPPING_METHODS
    ):
        call = parents.get(parent)
        if isinstance(call, ast.Call) and call.func is parent:
            lookup = call
    resolved = mapping_lookup(lookup) if lookup is not None else None
    if resolved is None or resolved[0] is not node:
        return None
    return literal_string(resolved[1])


def is_bounded_capability_namespace_member(
    capability: str,
    member: str | None,
    node: ast.Call,
    parents: Mapping[ast.AST, ast.AST],
) -> bool:
    if member is None:
        return False
    if not capability_member_is_dynamic(capability, member):
        return True
    if capability != "ctypes" or member != "WinDLL":
        return False
    parent = parents.get(node)
    lookup = (
        parent
        if isinstance(parent, ast.Subscript)
        else parents.get(parent)
        if isinstance(parent, ast.Attribute)
        else None
    )
    guard = parents.get(lookup) if lookup is not None else None
    return (
        isinstance(guard, ast.Call)
        and isinstance(guard.func, ast.Name)
        and guard.func.id == "callable"
        and guard.args == [lookup]
        and not guard.keywords
    )


def bounded_vars_member_owner(
    node: ast.expr,
    *,
    member: str,
    callable_names: Mapping[str, str],
    builtins_names: set[str],
) -> ast.expr | None:
    lookup = mapping_lookup(node)
    if not (
        lookup is not None
        and isinstance(lookup[0], ast.Call)
        and dynamic_callable_role(
            lookup[0].func,
            callable_names=callable_names,
            builtins_names=builtins_names,
        )
        == "vars"
        and len(lookup[0].args) == 1
        and not lookup[0].keywords
        and literal_string(lookup[1]) == member
    ):
        return None
    return lookup[0].args[0]


def is_bounded_ctypes_kernel_call(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and len(node.args) == 1
        and literal_string(node.args[0]) == "kernel32"
        and len(node.keywords) == 1
        and node.keywords[0].arg == "use_last_error"
        and isinstance(node.keywords[0].value, ast.Constant)
        and node.keywords[0].value.value is True
    )


def is_sys_module_registry(
    node: ast.expr,
    *,
    sys_names: set[str],
    sys_module_names: set[str],
) -> bool:
    return (
        isinstance(node, ast.Name) and node.id in sys_module_names
    ) or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in sys_names
        and node.attr == "modules"
    )


def literal_runtime_module(
    node: ast.expr,
    *,
    sys_names: set[str],
    sys_module_names: set[str],
    importlib_names: set[str],
    import_module_names: set[str],
    builtins_names: set[str],
    modules: set[str],
) -> str | None:
    lookup = mapping_lookup(node)
    if lookup is not None and is_sys_module_registry(
        lookup[0], sys_names=sys_names, sys_module_names=sys_module_names
    ):
        module_name = literal_string(lookup[1])
        if module_name is None:
            return UNKNOWN_MODULE
        return module_name if module_name in modules | set(CAPABILITY_MODULES) else None
    if not isinstance(node, ast.Call):
        return None
    runtime_import = (
        isinstance(node.func, ast.Name) and node.func.id in import_module_names
    ) or (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and (
            (node.func.value.id in builtins_names and node.func.attr == "__import__")
            or (
                node.func.value.id in importlib_names
                and node.func.attr == "import_module"
            )
        )
    )
    if not runtime_import or not node.args:
        return None
    module_name = literal_string(node.args[0])
    return module_name if module_name in modules | set(CAPABILITY_MODULES) else None
