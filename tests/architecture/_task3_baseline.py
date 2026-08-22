"""Behavioral Task 3 capture helpers bound to the immutable Task 1 tree."""

from __future__ import annotations

import ast
import base64
import builtins
import copy
import dis
import hashlib
import importlib
import inspect
import io
import json
import math
import os
import pickle
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import types
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from ._analysis import ROOT
from ._baseline import BASELINE_COMMIT

BASELINE_CONFIG_PATH = "uquant/config.py"
ISOLATED_VALIDATION_CASE_COUNT = 188
ORDER_PROBE_START_INDEX = ISOLATED_VALIDATION_CASE_COUNT
ORDER_PROBE_CASE_COUNT = 7
REACHABLE_WITNESS_START_INDEX = ORDER_PROBE_START_INDEX + ORDER_PROBE_CASE_COUNT
REACHABLE_WITNESS_CASE_COUNT = 2
UNKNOWN_KEYWORD_CASE_INDEX = REACHABLE_WITNESS_START_INDEX + REACHABLE_WITNESS_CASE_COUNT
TOTAL_VALIDATION_CASE_COUNT = UNKNOWN_KEYWORD_CASE_INDEX + 1
VALIDATION_CLAUSE_COUNT = 159
PAIR_CASE_COUNT = 17_571
VALIDATION_STIMULUS_MANIFEST_SHA256 = (
    "e88a379662b342cd702d66bb4fc55a897ac4019916a61a971b9375a064a46b78"
)
GOVERNED_EXTERNAL_GLOBALS: Mapping[str, object] = types.MappingProxyType(
    {"math": math}
)
REACHABLE_WITNESSES = (
    (
        REACHABLE_WITNESS_START_INDEX,
        "leader-cycle-market-range-before-impulse-relation",
    ),
    (
        REACHABLE_WITNESS_START_INDEX + 1,
        "strategic-transition-max-range-before-inverted-range",
    ),
)
STRUCTURAL_ONLY_ADJACENT_SWAPS = (
    ("transition-range-before-repair-relation", (140, 141)),
    ("transition-repair-relation-before-chronic-window", (141, 142)),
)
CANDIDATE_CONFIG_MODEL_PATH = "uquant/config/model.py"
CANDIDATE_VALIDATION_PATHS = (
    "uquant/config/validation/execution.py",
    "uquant/config/validation/market.py",
    "uquant/config/validation/portfolio.py",
    "uquant/config/validation/recovery.py",
    "uquant/config/validation/risk.py",
    "uquant/config/validation/sentinel.py",
    "uquant/config/validation/strategic.py",
)
_UNKNOWN_KEYWORD_CHANGES = {"not_a_governed_parameter": True}
METHOD_IDS = (
    "uquant.config:SystemConfig.override",
    "uquant.config:SystemConfig.to_dict",
    "uquant.types:AccountState.empty",
    "uquant.types:AccountState.to_dict",
    "uquant.types:Decision.canonical_payload",
    "uquant.types:Decision.legacy_canonical_payload",
    "uquant.types:Position.sellable_shares",
)


class _ConfigArgumentNormalizer(ast.NodeTransformer):
    """Normalize only a mechanically relocated validator argument to ``self``."""

    def __init__(self, argument_name: str) -> None:
        self._argument_name = argument_name

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id != self._argument_name:
            return node
        return ast.copy_location(ast.Name(id="self", ctx=node.ctx), node)


class _ModuleScopeBindingVisitor(ast.NodeVisitor):
    """Collect module-executed bindings without descending into callable bodies."""

    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.deleted: set[str] = set()
        self.loaded: set[str] = set()
        self.indirect: set[str] = set()
        self.mutated_roots: set[str] = set()
        self.dynamic = False

    @staticmethod
    def _root_name(node: ast.AST) -> str | None:
        while isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    def _record_nested_shadowing(self, node: ast.AST) -> None:
        for descendant in ast.walk(node):
            if descendant is node:
                continue
            if isinstance(descendant, ast.Name) and isinstance(
                descendant.ctx,
                (ast.Store, ast.Del),
            ):
                self.indirect.add(descendant.id)
            elif isinstance(descendant, ast.arg):
                self.indirect.add(descendant.arg)
            elif isinstance(descendant, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.indirect.add(descendant.name)
            elif isinstance(descendant, (ast.Global, ast.Nonlocal)):
                self.indirect.update(descendant.names)
            elif isinstance(descendant, ast.Attribute) and isinstance(
                descendant.ctx,
                (ast.Store, ast.Del),
            ):
                self.indirect.add(descendant.attr)
                root_name = self._root_name(descendant)
                if root_name is not None:
                    self.mutated_roots.add(root_name)
            elif (
                isinstance(descendant, ast.Subscript)
                and isinstance(descendant.ctx, (ast.Store, ast.Del))
                and isinstance(descendant.slice, ast.Constant)
                and isinstance(descendant.slice.value, str)
            ):
                self.indirect.add(descendant.slice.value)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.loaded.add(node.id)
        elif isinstance(node.ctx, ast.Del):
            self.deleted.add(node.id)
        else:
            self.bound.add(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.indirect.add(node.attr)
            root_name = self._root_name(node)
            if root_name is not None:
                self.mutated_roots.add(root_name)
        self.visit(node.value)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.ctx, (ast.Store, ast.Del))
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            self.indirect.add(node.slice.value)
        self.visit(node.value)
        if isinstance(node.ctx, ast.Load):
            self.visit(node.slice)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", maxsplit=1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                self.dynamic = True
            self.bound.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)
        self._record_nested_shadowing(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)
        self._record_nested_shadowing(node)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)
        self._record_nested_shadowing(node)
        for value in (*node.decorator_list, *node.bases):
            self.visit(value)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "exec":
            self.dynamic = True
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            self.indirect.add(node.args[1].value)
            root_name = self._root_name(node.args[0])
            if root_name is not None:
                self.mutated_roots.add(root_name)
        for keyword in node.keywords:
            if keyword.arg is not None:
                self.indirect.add(keyword.arg)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.indirect.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.indirect.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.bound.add(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.bound.add(node.rest)
        self.generic_visit(node)


_METHOD_CONTRACT_SCRIPT = r"""
import base64
import json
import pickle
import sys

import uquant.config as config
import uquant.types as domain_types

modules = {
    "uquant.config": config,
    "uquant.types": domain_types,
}


def authored_methods():
    records = {}
    for module_name, module in modules.items():
        for class_name, cls in vars(module).items():
            if not isinstance(cls, type) or cls.__module__ != module_name:
                continue
            for method_name, descriptor in vars(cls).items():
                if method_name.startswith("_"):
                    continue
                is_classmethod = isinstance(descriptor, classmethod)
                is_staticmethod = isinstance(descriptor, staticmethod)
                if is_classmethod or is_staticmethod:
                    function = descriptor.__func__
                elif callable(descriptor):
                    function = descriptor
                else:
                    continue
                if not getattr(function, "__qualname__", "").startswith(
                    f"{cls.__qualname__}."
                ):
                    continue
                pickle_target = getattr(cls, method_name) if is_classmethod else function
                method_id = f"{module_name}:{class_name}.{method_name}"
                records[method_id] = {
                    "classmethod": is_classmethod,
                    "module": function.__module__,
                    "pickle_b64": base64.b64encode(
                        pickle.dumps(pickle_target, protocol=4)
                    ).decode("ascii"),
                    "qualname": function.__qualname__,
                }
    return {key: records[key] for key in sorted(records)}


payload = json.loads(sys.stdin.read() or "{}")
records = authored_methods()
if payload.get("action", "capture") == "capture":
    result = records
else:
    result = {}
    for method_id, encoded in payload["pickles"].items():
        try:
            loaded = pickle.loads(base64.b64decode(encoded))
            module_name, member = method_id.split(":", 1)
            class_name, method_name = member.split(".", 1)
            cls = getattr(modules[module_name], class_name)
            descriptor = vars(cls)[method_name]
            if isinstance(descriptor, classmethod):
                ok = loaded.__func__ is descriptor.__func__ and loaded.__self__ is cls
            else:
                ok = loaded is descriptor
            result[method_id] = {"ok": ok, "error": ""}
        except Exception as exc:
            result[method_id] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
print(json.dumps(result, allow_nan=False, separators=(",", ":"), sort_keys=True))
"""


@lru_cache(maxsize=8)
def git_blob(path: str, commit: str = BASELINE_COMMIT) -> bytes:
    """Read one exact path from the reviewed immutable Git commit."""

    resolved = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{commit}}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if resolved != commit:
        raise AssertionError(f"baseline commit resolved to {resolved}, expected {commit}")
    return subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _body_without_docstring(body: Sequence[ast.stmt]) -> list[ast.stmt]:
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return list(body[1:])
    return list(body)


def _system_config_post_init(tree: ast.Module) -> ast.FunctionDef:
    config_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SystemConfig"
    )
    return next(
        node
        for node in config_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "__post_init__"
    )


def _normalized_clause_dump(statement: ast.stmt, argument_name: str) -> str:
    normalized = _ConfigArgumentNormalizer(argument_name).visit(copy.deepcopy(statement))
    ast.fix_missing_locations(normalized)
    return ast.dump(normalized, include_attributes=False)


@lru_cache(maxsize=1)
def baseline_validation_clause_dumps() -> tuple[str, ...]:
    """Return the immutable Task 1 validation clauses in exact semantic order."""

    source = git_blob(BASELINE_CONFIG_PATH).decode("utf-8")
    tree = ast.parse(source, filename=f"{BASELINE_COMMIT}:{BASELINE_CONFIG_PATH}")
    post_init = _system_config_post_init(tree)
    clauses = tuple(
        _normalized_clause_dump(statement, post_init.args.args[0].arg)
        for statement in _body_without_docstring(post_init.body)
    )
    if len(clauses) != VALIDATION_CLAUSE_COUNT:
        raise AssertionError(
            f"baseline validation clause count changed: {len(clauses)}"
        )
    return clauses


def _top_level_helper_call(statement: ast.stmt) -> ast.Call | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    if not isinstance(statement.value.func, ast.Name):
        return None
    return statement.value


def _assert_no_binding_bypass(
    *,
    path: str,
    tree: ast.Module,
    canonical_positions: Mapping[str, int],
) -> None:
    """Reject module-scope aliases, rebinding, deletion, or shadowing."""

    for name, position in canonical_positions.items():
        for statement in tree.body[position + 1 :]:
            visitor = _ModuleScopeBindingVisitor()
            visitor.visit(statement)
            if visitor.dynamic:
                raise AssertionError(
                    f"dynamic module binding follows governed callable in {path}: {name}"
                )
            if name in visitor.bound:
                raise AssertionError(f"governed callable is rebound in {path}: {name}")
            if name in visitor.deleted:
                raise AssertionError(f"governed callable is deleted in {path}: {name}")
            if name in visitor.indirect:
                raise AssertionError(
                    f"governed callable is indirectly rebound in {path}: {name}"
                )
            if name in visitor.loaded:
                raise AssertionError(f"governed callable is aliased in {path}: {name}")


def _validation_module_name(path: str) -> str:
    if not path.endswith(".py"):
        raise AssertionError(f"validator source path is not Python: {path}")
    return path.removesuffix(".py").replace("/", ".")


def _function_code_from_source(
    *,
    source: str,
    path: str,
    qualname: str,
    first_lineno: int,
    filename: str | None = None,
) -> types.CodeType:
    module_code = compile(
        source,
        filename or path,
        "exec",
        dont_inherit=True,
    )
    pending = [module_code]
    matches: list[types.CodeType] = []
    while pending:
        code = pending.pop()
        for constant in code.co_consts:
            if not isinstance(constant, types.CodeType):
                continue
            pending.append(constant)
            if constant.co_qualname == qualname and constant.co_firstlineno == first_lineno:
                matches.append(constant)
    if len(matches) != 1:
        raise AssertionError(
            f"expected one governed code object for {path}:{qualname}, got {len(matches)}"
        )
    return matches[0]


def _expected_code(
    *,
    path: str,
    qualname: str,
    first_lineno: int,
) -> types.CodeType:
    source_path = (ROOT / path).resolve()
    return _function_code_from_source(
        source=source_path.read_text(encoding="utf-8"),
        path=path,
        qualname=qualname,
        first_lineno=first_lineno,
        filename=str(source_path),
    )


def _global_load_names(code: types.CodeType) -> set[str]:
    names = {
        cast(str, instruction.argval)
        for instruction in dis.get_instructions(code)
        if instruction.opname == "LOAD_GLOBAL"
    }
    for constant in code.co_consts:
        if isinstance(constant, types.CodeType):
            names.update(_global_load_names(constant))
    return names


def _module_import_positions(tree: ast.Module) -> dict[str, int]:
    positions: dict[str, int] = {}
    for position, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                positions[name] = position
        elif isinstance(statement, ast.ImportFrom) and statement.module != "__future__":
            for alias in statement.names:
                if alias.name == "*":
                    raise AssertionError("governed modules may not use star imports")
                positions[alias.asname or alias.name] = position
    return positions


def _resolved_module_imports(tree: ast.Module, module_name: str) -> dict[str, object]:
    bindings: dict[str, object] = {}
    package = module_name.rpartition(".")[0]
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.asname is None:
                    imported_name = alias.name.split(".", maxsplit=1)[0]
                    bindings[imported_name] = importlib.import_module(imported_name)
                else:
                    bindings[alias.asname] = importlib.import_module(alias.name)
        elif isinstance(statement, ast.ImportFrom) and statement.module != "__future__":
            relative_name = f"{'.' * statement.level}{statement.module or ''}"
            imported_module = importlib.import_module(relative_name, package=package)
            for alias in statement.names:
                if alias.name == "*":
                    raise AssertionError("governed modules may not use star imports")
                bindings[alias.asname or alias.name] = getattr(imported_module, alias.name)
    return bindings


def _assert_governed_dependency_topology(
    *,
    path: str,
    source: str,
    tree: ast.Module,
    functions: Mapping[str, ast.FunctionDef],
) -> None:
    """Reject source bindings that alter a governed function's global lookup."""

    loaded_globals: set[str] = set()
    for name, definition in functions.items():
        code = _function_code_from_source(
            source=source,
            path=path,
            qualname=name,
            first_lineno=definition.lineno,
        )
        loaded_globals.update(_global_load_names(code))

    import_positions = _module_import_positions(tree)
    builtin_dependencies = loaded_globals.intersection(vars(builtins))
    external_dependencies = loaded_globals.intersection(GOVERNED_EXTERNAL_GLOBALS)
    unknown = loaded_globals.difference(
        builtin_dependencies,
        external_dependencies,
        functions,
    )
    if unknown:
        raise AssertionError(
            f"governed callables have unresolved global dependencies in {path}: "
            f"{sorted(unknown)!r}"
        )

    for name in builtin_dependencies:
        for statement in tree.body:
            visitor = _ModuleScopeBindingVisitor()
            visitor.visit(statement)
            if visitor.dynamic:
                raise AssertionError(
                    f"dynamic module binding can shadow builtin in {path}: {name}"
                )
            if name in visitor.bound or name in visitor.deleted or name in visitor.indirect:
                raise AssertionError(
                    f"governed builtin dependency is shadowed in {path}: {name}"
                )

    resolved_imports = _resolved_module_imports(
        tree,
        _validation_module_name(path),
    )
    for name in external_dependencies:
        if (
            name not in import_positions
            or resolved_imports.get(name) is not GOVERNED_EXTERNAL_GLOBALS[name]
        ):
            raise AssertionError(
                f"governed external dependency changed in {path}: {name}"
            )

    governed_import_positions = {
        name: import_positions[name]
        for name in external_dependencies
    }
    _assert_no_binding_bypass(
        path=path,
        tree=tree,
        canonical_positions=governed_import_positions,
    )


def _assert_system_config_source_dispatch(tree: ast.Module) -> None:
    config_classes = [
        (position, node)
        for position, node in enumerate(tree.body)
        if isinstance(node, ast.ClassDef) and node.name == "SystemConfig"
    ]
    if len(config_classes) != 1:
        raise AssertionError("candidate must define exactly one SystemConfig class")
    position, config_class = config_classes[0]
    forbidden_methods = {
        node.name
        for node in config_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"__getattr__", "__getattribute__"}
    }
    if forbidden_methods:
        raise AssertionError(
            f"SystemConfig attribute dispatch changed: {sorted(forbidden_methods)!r}"
        )
    for statement in tree.body[position + 1 :]:
        visitor = _ModuleScopeBindingVisitor()
        visitor.visit(statement)
        if visitor.dynamic:
            raise AssertionError("dynamic binding follows the SystemConfig definition")
        if "SystemConfig" in visitor.bound or "SystemConfig" in visitor.deleted:
            raise AssertionError("SystemConfig class binding changed")
        if "SystemConfig" in visitor.mutated_roots:
            raise AssertionError("SystemConfig class dispatch was mutated")


def _callable_source_dump(function: types.FunctionType) -> str:
    try:
        source = textwrap.dedent(inspect.getsource(function))
    except (OSError, TypeError) as exc:
        raise AssertionError(f"governed callable has no inspectable source: {function}") from exc
    tree = ast.parse(source)
    definition = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        ),
        None,
    )
    if definition is None:
        raise AssertionError(f"governed callable source is not a function: {function}")
    return ast.dump(definition, include_attributes=False)


def _assert_exact_runtime_function(
    *,
    function: object,
    module: types.ModuleType,
    expected_code: types.CodeType,
    expected_qualname: str,
    definition: ast.FunctionDef,
) -> types.FunctionType:
    if not isinstance(function, types.FunctionType):
        raise AssertionError(
            f"governed binding is not a function: {module.__name__}.{definition.name}"
        )
    if function.__globals__ is not module.__dict__:
        raise AssertionError(
            f"governed function has foreign globals: {module.__name__}.{definition.name}"
        )
    if function.__module__ != module.__name__:
        raise AssertionError(
            f"governed function module changed: {module.__name__}.{definition.name}"
        )
    if function.__qualname__ != expected_qualname:
        raise AssertionError(
            f"governed function qualname changed: {module.__name__}.{definition.name}"
        )
    if function.__code__ != expected_code:
        raise AssertionError(
            f"governed function code changed: {module.__name__}.{definition.name}"
        )
    if function.__defaults__ is not None or function.__kwdefaults__ is not None:
        raise AssertionError(
            f"governed function defaults changed: {module.__name__}.{definition.name}"
        )
    expected_source = ast.dump(definition, include_attributes=False)
    if _callable_source_dump(function) != expected_source:
        raise AssertionError(
            f"governed function source changed: {module.__name__}.{definition.name}"
        )
    return function


def _assert_exact_runtime_globals(
    *,
    function: types.FunctionType,
    expected_code: types.CodeType,
    expected_bindings: Mapping[str, object],
) -> None:
    """Verify every actual LOAD_GLOBAL resolves to its governed object."""

    function_builtins = cast(dict[str, object], cast(Any, function).__builtins__)
    if function_builtins is not vars(builtins):
        raise AssertionError(
            f"governed function builtins changed: {function.__module__}.{function.__qualname__}"
        )
    for name in _global_load_names(expected_code):
        if name in vars(builtins):
            if name in function.__globals__:
                raise AssertionError(
                    f"governed builtin dependency is shadowed at runtime: "
                    f"{function.__module__}.{function.__qualname__}->{name}"
                )
            if function_builtins.get(name) is not vars(builtins)[name]:
                raise AssertionError(
                    f"governed builtin dependency changed: "
                    f"{function.__module__}.{function.__qualname__}->{name}"
                )
            continue
        if name not in expected_bindings:
            raise AssertionError(
                f"governed runtime dependency is not declared: "
                f"{function.__module__}.{function.__qualname__}->{name}"
            )
        if function.__globals__.get(name) is not expected_bindings[name]:
            raise AssertionError(
                f"governed runtime dependency changed: "
                f"{function.__module__}.{function.__qualname__}->{name}"
            )


def candidate_validation_clause_dumps(
    source_overrides: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Flatten split validators into their effective ordered semantic clauses."""

    overrides = dict(source_overrides or {})
    allowed_paths = {CANDIDATE_CONFIG_MODEL_PATH, *CANDIDATE_VALIDATION_PATHS}
    unknown_paths = set(overrides).difference(allowed_paths)
    if unknown_paths:
        raise AssertionError(f"unknown candidate source overrides: {sorted(unknown_paths)!r}")

    def source_for(path: str) -> str:
        if path in overrides:
            return overrides[path]
        return (ROOT / path).read_text(encoding="utf-8")

    functions: dict[str, dict[str, ast.FunctionDef]] = {}
    for path in CANDIDATE_VALIDATION_PATHS:
        path_source = source_for(path)
        tree = ast.parse(path_source, filename=path)
        path_functions: dict[str, ast.FunctionDef] = {}
        canonical_positions: dict[str, int] = {}
        for position, node in enumerate(tree.body):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in path_functions:
                raise AssertionError(f"duplicate validator helper in {path}: {node.name}")
            if node.decorator_list:
                raise AssertionError(f"governed callable may not be decorated: {path}:{node.name}")
            path_functions[node.name] = node
            canonical_positions[node.name] = position
        _assert_no_binding_bypass(
            path=path,
            tree=tree,
            canonical_positions=canonical_positions,
        )
        _assert_governed_dependency_topology(
            path=path,
            source=path_source,
            tree=tree,
            functions=path_functions,
        )
        functions[path] = path_functions

    model_source = source_for(CANDIDATE_CONFIG_MODEL_PATH)
    model_tree = ast.parse(
        model_source,
        filename=CANDIDATE_CONFIG_MODEL_PATH,
    )
    _assert_system_config_source_dispatch(model_tree)
    root = _system_config_post_init(model_tree)
    if root.decorator_list:
        raise AssertionError("SystemConfig.__post_init__ may not be decorated")

    model_imports: dict[str, tuple[str, str]] = {}
    model_import_positions: dict[str, int] = {}
    for position, statement in enumerate(model_tree.body):
        if (
            not isinstance(statement, ast.ImportFrom)
            or statement.level != 1
            or statement.module is None
            or not statement.module.startswith("validation.")
        ):
            continue
        imported_path = f"uquant/config/{statement.module.replace('.', '/')}.py"
        if imported_path not in functions:
            continue
        for alias in statement.names:
            if alias.asname is not None:
                raise AssertionError(
                    f"governed validator import may not be aliased: {alias.name}"
                )
            local_name = alias.asname or alias.name
            if local_name in model_imports:
                raise AssertionError(f"duplicate validator import alias: {local_name}")
            model_imports[local_name] = (imported_path, alias.name)
            model_import_positions[local_name] = position
    _assert_no_binding_bypass(
        path=CANDIDATE_CONFIG_MODEL_PATH,
        tree=model_tree,
        canonical_positions=model_import_positions,
    )

    def flatten(
        path: str,
        function: ast.FunctionDef,
        stack: tuple[str, ...],
    ) -> list[str]:
        function_id = f"{path}:{function.name}"
        if function_id in stack:
            raise AssertionError(
                f"recursive validator extraction: {(*stack, function_id)!r}"
            )
        if len(function.args.args) != 1:
            raise AssertionError(f"validator must have one positional argument: {function.name}")
        argument_name = function.args.args[0].arg
        clauses: list[str] = []
        for statement in _body_without_docstring(function.body):
            helper_call = _top_level_helper_call(statement)
            if helper_call is not None and isinstance(helper_call.func, ast.Name):
                helper_target: tuple[str, str] | None
                if path == CANDIDATE_CONFIG_MODEL_PATH:
                    helper_target = model_imports.get(helper_call.func.id)
                elif helper_call.func.id in functions[path]:
                    helper_target = (path, helper_call.func.id)
                else:
                    helper_target = None
                if helper_target is not None:
                    if (
                        len(helper_call.args) != 1
                        or helper_call.keywords
                        or not isinstance(helper_call.args[0], ast.Name)
                        or helper_call.args[0].id != argument_name
                    ):
                        raise AssertionError(
                            f"non-mechanical validator call in {function.name}: "
                            f"{ast.dump(helper_call, include_attributes=False)}"
                        )
                    helper_path, helper_name = helper_target
                    helper = functions[helper_path].get(helper_name)
                    if helper is None:
                        raise AssertionError(
                            f"validator import has no implementation: "
                            f"{helper_path}:{helper_name}"
                        )
                    clauses.extend(flatten(helper_path, helper, (*stack, function_id)))
                    continue
            clauses.append(_normalized_clause_dump(statement, argument_name))
        return clauses

    clauses = tuple(flatten(CANDIDATE_CONFIG_MODEL_PATH, root, ()))
    if len(clauses) != VALIDATION_CLAUSE_COUNT:
        raise AssertionError(
            f"candidate validation clause count changed: {len(clauses)}"
        )
    if not overrides:
        _assert_live_candidate_bindings(
            root=root,
            functions=functions,
            model_imports=model_imports,
        )
    return clauses


def _assert_live_candidate_bindings(
    *,
    root: ast.FunctionDef,
    functions: Mapping[str, Mapping[str, ast.FunctionDef]],
    model_imports: Mapping[str, tuple[str, str]],
) -> None:
    """Match live globals and code to the governed on-disk callable topology."""

    live_functions: dict[tuple[str, str], types.FunctionType] = {}
    live_codes: dict[tuple[str, str], types.CodeType] = {}
    live_modules: dict[str, types.ModuleType] = {}
    for path, path_functions in functions.items():
        module_name = _validation_module_name(path)
        module = importlib.import_module(module_name)
        live_modules[path] = module
        module_file = getattr(module, "__file__", None)
        if module_file is None or Path(module_file).resolve() != (ROOT / path).resolve():
            raise AssertionError(f"governed validator module path changed: {module_name}")
        for name, definition in path_functions.items():
            expected_code = _expected_code(
                path=path,
                qualname=name,
                first_lineno=definition.lineno,
            )
            live_codes[(path, name)] = expected_code
            live_functions[(path, name)] = _assert_exact_runtime_function(
                function=vars(module).get(name),
                module=module,
                expected_code=expected_code,
                expected_qualname=name,
                definition=definition,
            )

    for path, path_functions in functions.items():
        expected_bindings = dict(GOVERNED_EXTERNAL_GLOBALS)
        expected_bindings.update(
            {
                name: live_functions[(path, name)]
                for name in path_functions
            }
        )
        for name in path_functions:
            _assert_exact_runtime_globals(
                function=live_functions[(path, name)],
                expected_code=live_codes[(path, name)],
                expected_bindings=expected_bindings,
            )

    model_module = importlib.import_module(_validation_module_name(CANDIDATE_CONFIG_MODEL_PATH))
    module_file = getattr(model_module, "__file__", None)
    if module_file is None or Path(module_file).resolve() != (
        ROOT / CANDIDATE_CONFIG_MODEL_PATH
    ).resolve():
        raise AssertionError("governed config model module path changed")
    system_config = vars(model_module).get("SystemConfig")
    if not isinstance(system_config, type):
        raise AssertionError("SystemConfig live binding is not a class")
    if type(system_config) is not type or system_config.__mro__ != (system_config, object):
        raise AssertionError("SystemConfig class hierarchy or metaclass changed")
    if system_config.__qualname__ != "SystemConfig":
        raise AssertionError("SystemConfig class qualname changed")
    if vars(system_config).get("__getattribute__") is not None:
        raise AssertionError("SystemConfig defines a custom __getattribute__")
    if "__getattr__" in vars(system_config):
        raise AssertionError("SystemConfig defines a custom __getattr__")
    if cast(object, system_config.__getattribute__) is not cast(
        object,
        object.__getattribute__,
    ):
        raise AssertionError("SystemConfig attribute dispatch changed")
    default_config = vars(model_module).get("DEFAULT_CONFIG")
    if type(default_config) is not system_config:
        raise AssertionError("DEFAULT_CONFIG is not an exact SystemConfig instance")
    expected_root_code = _expected_code(
        path=CANDIDATE_CONFIG_MODEL_PATH,
        qualname="SystemConfig.__post_init__",
        first_lineno=root.lineno,
    )
    live_root = _assert_exact_runtime_function(
        function=vars(system_config).get("__post_init__"),
        module=model_module,
        expected_code=expected_root_code,
        expected_qualname="SystemConfig.__post_init__",
        definition=root,
    )

    model_bindings = {
        local_name: live_functions[target]
        for local_name, target in model_imports.items()
        if target in live_functions
    }
    _assert_exact_runtime_globals(
        function=live_root,
        expected_code=expected_root_code,
        expected_bindings=model_bindings,
    )

    for local_name, target in model_imports.items():
        expected_binding = live_functions.get(target)
        if expected_binding is None:
            raise AssertionError(f"model imports an ungoverned validator: {local_name}")
        if live_root.__globals__.get(local_name) is not expected_binding:
            raise AssertionError(
                f"SystemConfig.__post_init__ validator binding changed: {local_name}"
            )

    for path, path_functions in functions.items():
        for name, definition in path_functions.items():
            live_function = live_functions[(path, name)]
            for statement in _body_without_docstring(definition.body):
                helper_call = _top_level_helper_call(statement)
                if helper_call is None or not isinstance(helper_call.func, ast.Name):
                    continue
                helper_name = helper_call.func.id
                expected_helper = live_functions.get((path, helper_name))
                if expected_helper is None:
                    continue
                if live_function.__globals__.get(helper_name) is not expected_helper:
                    raise AssertionError(
                        f"validator helper binding changed: "
                        f"{live_modules[path].__name__}.{name}->{helper_name}"
                    )


@lru_cache(maxsize=1)
def baseline_config_module() -> types.ModuleType:
    """Execute the immutable baseline config bytes as behavior, not text evidence."""

    source = git_blob(BASELINE_CONFIG_PATH)
    module_name = f"_uquant_task3_baseline_config_{BASELINE_COMMIT[:12]}"
    module = types.ModuleType(module_name)
    module.__file__ = f"{BASELINE_COMMIT}:{BASELINE_CONFIG_PATH}"
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def exception_observation(config: object, changes: Mapping[str, object]) -> dict[str, str]:
    """Capture the exact public exception outcome for one flat override."""

    override = cast(Any, config.override)
    try:
        override(**dict(changes))
    except Exception as exc:
        return {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    raise AssertionError(f"expected invalid override to fail: {dict(changes)!r}")


def _validation_pair_count(cases: Sequence[Mapping[str, object]]) -> int:
    isolated = cases[:ISOLATED_VALIDATION_CASE_COUNT]
    return sum(
        1
        for index, left in enumerate(isolated)
        for right in isolated[index + 1 :]
        if set(cast(Mapping[str, object], left["changes"])).isdisjoint(
            cast(Mapping[str, object], right["changes"])
        )
    )


def _stimulus_manifest_sha256(cases: Sequence[Mapping[str, object]]) -> str:
    manifest: list[dict[str, object]] = []
    for case in cases:
        entry: dict[str, object] = {"changes": case["changes"]}
        if "witness_id" in case:
            entry["witness_id"] = case["witness_id"]
        manifest.append(entry)
    encoded = json.dumps(
        manifest,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def validation_fixture_metadata(cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return deterministic provenance and immutable matrix dimensions."""

    return {
        "baseline_blob_sha256": hashlib.sha256(git_blob(BASELINE_CONFIG_PATH)).hexdigest(),
        "baseline_commit": BASELINE_COMMIT,
        "baseline_path": BASELINE_CONFIG_PATH,
        "isolated_case_count": ISOLATED_VALIDATION_CASE_COUNT,
        "order_probe_start_index": ORDER_PROBE_START_INDEX,
        "order_probe_case_count": ORDER_PROBE_CASE_COUNT,
        "order_probe_indexes": list(
            range(ORDER_PROBE_START_INDEX, REACHABLE_WITNESS_START_INDEX)
        ),
        "reachable_witness_start_index": REACHABLE_WITNESS_START_INDEX,
        "reachable_witness_case_count": REACHABLE_WITNESS_CASE_COUNT,
        "reachable_witness_indexes": list(
            range(REACHABLE_WITNESS_START_INDEX, UNKNOWN_KEYWORD_CASE_INDEX)
        ),
        "reachable_witnesses": [
            {"case_index": case_index, "witness_id": witness_id}
            for case_index, witness_id in REACHABLE_WITNESSES
        ],
        "structural_only_adjacent_swaps": [
            {
                "baseline_clause_indexes": list(clause_indexes),
                "swap_id": swap_id,
            }
            for swap_id, clause_indexes in STRUCTURAL_ONLY_ADJACENT_SWAPS
        ],
        "pair_case_count": PAIR_CASE_COUNT,
        "stimulus_manifest_sha256": VALIDATION_STIMULUS_MANIFEST_SHA256,
        "total_case_count": TOTAL_VALIDATION_CASE_COUNT,
        "unknown_keyword_case_index": UNKNOWN_KEYWORD_CASE_INDEX,
        "validation_clause_count": VALIDATION_CLAUSE_COUNT,
    }


def _canonical_changes(case: Mapping[str, object]) -> str:
    changes = case.get("changes")
    if not isinstance(changes, Mapping) or not changes:
        raise AssertionError("every validation case must have non-empty mapping changes")
    return json.dumps(
        changes,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def validate_validation_fixture_shape(fixture: Mapping[str, object]) -> None:
    """Reject truncated, repartitioned, duplicated, or misclassified fixtures."""

    if set(fixture) != {"baseline", "cases", "schema_version"}:
        raise AssertionError("validation fixture top-level shape changed")
    if fixture["schema_version"] != 3:
        raise AssertionError("validation fixture schema_version must be 3")
    cases_value = fixture["cases"]
    if not isinstance(cases_value, list):
        raise AssertionError("validation fixture cases must be a list")
    if len(cases_value) != TOTAL_VALIDATION_CASE_COUNT:
        raise AssertionError(
            f"validation fixture must have exactly {TOTAL_VALIDATION_CASE_COUNT} cases"
        )
    if not all(isinstance(case, Mapping) for case in cases_value):
        raise AssertionError("every validation fixture case must be an object")
    cases = cast(list[Mapping[str, object]], cases_value)
    for case in cases:
        if not isinstance(case.get("exception_type"), str) or not isinstance(
            case.get("message"),
            str,
        ):
            raise AssertionError("every validation case must record string behavior")
    observed_manifest = _stimulus_manifest_sha256(cases)
    if observed_manifest != VALIDATION_STIMULUS_MANIFEST_SHA256:
        raise AssertionError(
            f"validation stimulus manifest changed: {observed_manifest}"
        )
    observed_pair_count = _validation_pair_count(cases)
    if observed_pair_count != PAIR_CASE_COUNT:
        raise AssertionError(
            f"validation pair count changed: {observed_pair_count}"
        )
    if fixture["baseline"] != validation_fixture_metadata(cases):
        raise AssertionError("validation fixture metadata is not the exact baseline partition")

    canonical_changes = [_canonical_changes(case) for case in cases]
    if len(set(canonical_changes)) != len(canonical_changes):
        raise AssertionError("validation fixture contains duplicate stimuli")

    baseline_fields = set(cast(Any, baseline_config_module().DEFAULT_CONFIG).to_dict())
    for case in cases:
        changes = cast(Mapping[str, object], case["changes"])
        if any(type(value) not in {bool, int, float, str} for value in changes.values()):
            raise AssertionError("validation fixtures accept only serialized scalar values")
    isolated = cases[:ISOLATED_VALIDATION_CASE_COUNT]
    for case in isolated:
        if set(case) != {"changes", "exception_type", "message"}:
            raise AssertionError("isolated cases may contain only behavior fields")
        if case["exception_type"] != "ValueError":
            raise AssertionError("isolated cases must be validation failures")
        changes = cast(Mapping[str, object], case["changes"])
        if not set(changes).issubset(baseline_fields):
            raise AssertionError("isolated cases must target governed fields")

    isolated_changes = [
        cast(Mapping[str, object], case["changes"])
        for case in isolated
    ]
    isolated_canonical = set(canonical_changes[:ISOLATED_VALIDATION_CASE_COUNT])
    pair_probe_changes: set[str] = set()
    for index, left in enumerate(isolated_changes):
        for right in isolated_changes[index + 1 :]:
            if not set(left).isdisjoint(right):
                continue
            merged = {**left, **right}
            encoded = json.dumps(
                merged,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            pair_probe_changes.add(encoded)
    if isolated_canonical.intersection(pair_probe_changes):
        raise AssertionError("isolated cases must not be composite order probes")

    order_probes = cases[ORDER_PROBE_START_INDEX:REACHABLE_WITNESS_START_INDEX]
    if len(order_probes) != ORDER_PROBE_CASE_COUNT:
        raise AssertionError("order-probe partition length changed")
    for index, case in enumerate(order_probes, start=ORDER_PROBE_START_INDEX):
        if set(case) != {"changes", "exception_type", "message"}:
            raise AssertionError(f"order probe {index} has invalid case fields")
        if case["exception_type"] != "ValueError":
            raise AssertionError(f"order probe {index} must be a validation failure")
        changes = cast(Mapping[str, object], case["changes"])
        if not set(changes).issubset(baseline_fields):
            raise AssertionError(f"order probe {index} must target governed fields")
        if canonical_changes[index] not in pair_probe_changes:
            raise AssertionError(
                f"order probe {index} must merge two disjoint isolated stimuli"
            )

    witnesses = cases[REACHABLE_WITNESS_START_INDEX:UNKNOWN_KEYWORD_CASE_INDEX]
    if len(witnesses) != REACHABLE_WITNESS_CASE_COUNT:
        raise AssertionError("reachable-witness partition length changed")
    for (expected_index, expected_id), case in zip(
        REACHABLE_WITNESSES,
        witnesses,
        strict=True,
    ):
        if set(case) != {"changes", "exception_type", "message", "witness_id"}:
            raise AssertionError(f"reachable witness {expected_index} has invalid fields")
        if case["witness_id"] != expected_id:
            raise AssertionError(f"reachable witness {expected_index} has the wrong id")
        if case["exception_type"] != "ValueError":
            raise AssertionError("reachable witnesses must be validation failures")
        changes = cast(Mapping[str, object], case["changes"])
        if not set(changes).issubset(baseline_fields):
            raise AssertionError("reachable witnesses must target governed fields")

    unknown = cases[UNKNOWN_KEYWORD_CASE_INDEX]
    if set(unknown) != {"changes", "exception_type", "message"}:
        raise AssertionError("unknown-keyword case has invalid fields")
    if unknown["changes"] != _UNKNOWN_KEYWORD_CHANGES:
        raise AssertionError("the final case must be the exact unknown-keyword stimulus")
    if unknown["exception_type"] != "TypeError":
        raise AssertionError("the final unknown-keyword case must preserve TypeError")


def capture_validation_contract(
    fixture: Mapping[str, object],
) -> dict[str, object]:
    """Replay every stored stimulus against immutable baseline behavior."""

    validate_validation_fixture_shape(fixture)
    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    captured: list[dict[str, object]] = []
    for case in cases:
        captured_case: dict[str, object] = {
            "changes": dict(cast(Mapping[str, object], case["changes"])),
            **exception_observation(
                baseline_default,
                cast(Mapping[str, object], case["changes"]),
            ),
        }
        if "witness_id" in case:
            captured_case["witness_id"] = case["witness_id"]
        captured.append(captured_case)
    return {
        "baseline": validation_fixture_metadata(cases),
        "cases": captured,
        "schema_version": 3,
    }


@lru_cache(maxsize=1)
def _baseline_archive() -> bytes:
    return subprocess.run(
        ["git", "archive", "--format=tar", BASELINE_COMMIT, "uquant"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _run_baseline_package(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Run pickle behavior in an isolated package materialized from baseline Git bytes."""

    with tempfile.TemporaryDirectory(prefix="uquant-task3-baseline-") as directory:
        baseline_root = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(_baseline_archive()), mode="r:") as archive:
            archive.extractall(baseline_root, filter="data")
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = "0"
        environment["PYTHONPATH"] = str(baseline_root)
        completed = subprocess.run(
            [sys.executable, "-c", _METHOD_CONTRACT_SCRIPT],
            cwd=baseline_root,
            env=environment,
            input=json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True),
            check=True,
            capture_output=True,
            text=True,
        )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise AssertionError("baseline method contract must be an object")
    return cast(dict[str, dict[str, object]], value)


@lru_cache(maxsize=1)
def baseline_method_contract() -> dict[str, dict[str, object]]:
    """Capture authored public method attribution and pickle bytes from baseline."""

    return _run_baseline_package({"action": "capture"})


def baseline_load_method_pickles(
    pickles: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    """Load candidate-created method pickles in the isolated baseline package."""

    return _run_baseline_package({"action": "load", "pickles": dict(pickles)})


def current_method_contract() -> dict[str, dict[str, object]]:
    """Capture the same authored public method behavior from the candidate tree."""

    import uquant.config as config
    import uquant.types as domain_types

    modules = {
        "uquant.config": config,
        "uquant.types": domain_types,
    }
    records: dict[str, dict[str, object]] = {}
    for module_name, module in modules.items():
        for class_name, cls in vars(module).items():
            if not isinstance(cls, type) or cls.__module__ != module_name:
                continue
            for method_name, descriptor in vars(cls).items():
                if method_name.startswith("_"):
                    continue
                is_classmethod = isinstance(descriptor, classmethod)
                is_staticmethod = isinstance(descriptor, staticmethod)
                if is_classmethod or is_staticmethod:
                    function = descriptor.__func__
                elif callable(descriptor):
                    function = descriptor
                else:
                    continue
                if not getattr(function, "__qualname__", "").startswith(
                    f"{cls.__qualname__}."
                ):
                    continue
                pickle_target = getattr(cls, method_name) if is_classmethod else function
                method_id = f"{module_name}:{class_name}.{method_name}"
                records[method_id] = {
                    "classmethod": is_classmethod,
                    "module": function.__module__,
                    "pickle_b64": base64.b64encode(
                        pickle.dumps(pickle_target, protocol=4)
                    ).decode("ascii"),
                    "qualname": function.__qualname__,
                }
    return {key: records[key] for key in sorted(records)}


def current_load_method_pickles(
    pickles: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    """Load baseline-created method pickles through the candidate facades."""

    import uquant.config as config
    import uquant.types as domain_types

    modules = {
        "uquant.config": config,
        "uquant.types": domain_types,
    }
    results: dict[str, dict[str, object]] = {}
    for method_id, encoded in pickles.items():
        try:
            loaded = pickle.loads(base64.b64decode(encoded))
            module_name, member = method_id.split(":", 1)
            class_name, method_name = member.split(".", 1)
            cls = getattr(modules[module_name], class_name)
            descriptor = vars(cls)[method_name]
            if isinstance(descriptor, classmethod):
                ok = loaded.__func__ is descriptor.__func__ and loaded.__self__ is cls
            else:
                ok = loaded is descriptor
            results[method_id] = {"ok": ok, "error": ""}
        except Exception as exc:
            results[method_id] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    return results


__all__ = (
    "BASELINE_COMMIT",
    "ISOLATED_VALIDATION_CASE_COUNT",
    "METHOD_IDS",
    "REACHABLE_WITNESS_CASE_COUNT",
    "REACHABLE_WITNESS_START_INDEX",
    "TOTAL_VALIDATION_CASE_COUNT",
    "UNKNOWN_KEYWORD_CASE_INDEX",
    "baseline_config_module",
    "baseline_load_method_pickles",
    "baseline_method_contract",
    "baseline_validation_clause_dumps",
    "candidate_validation_clause_dumps",
    "capture_validation_contract",
    "current_load_method_pickles",
    "current_method_contract",
    "exception_observation",
    "validate_validation_fixture_shape",
)
