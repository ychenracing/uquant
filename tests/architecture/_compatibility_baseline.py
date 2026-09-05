# ruff: noqa: E402, I001
# Late re-exports preserve the immutable pytest collection identity and order.
"""Behavioral compatibility capture helpers bound to the immutable baseline tree."""

from __future__ import annotations

import ast
import builtins
import copy
import dis
import importlib
import inspect
import math
import subprocess
import textwrap
import types
from collections.abc import Mapping, Sequence
from functools import lru_cache
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

# Candidate-only retirement; the frozen baseline and stimulus manifest stay intact.
RETIRED_VALIDATION_CLAUSES: Mapping[int, str] = types.MappingProxyType(
    {
        57: "leader_cycle_confirm_days",
        58: "leader_cycle_min_mature",
        59: "leader_cycle_min_score",
        60: "leader_cycle_impulse_breadth",
        61: "leader_cycle_min_market_ret120",
        62: "leader_cycle_impulse_min_market_ret120",
        79: "strategic_epoch_cooldown_sessions",
        80: "strategic_epoch_min_symbol_change",
    }
)

RETIRED_CONFIG_FIELDS = frozenset((
    *RETIRED_VALIDATION_CLAUSES.values(),
    "leader_cycle_impulse_return",
    "leader_cycle_impulse_index_return",
))

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
    """Return immutable baseline validation clauses in exact semantic order."""

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

def projected_baseline_validation_clause_dumps() -> tuple[str, ...]:
    """Project only the eight reviewed clauses for retired epoch and leader-cycle controls."""

    return tuple(
        clause
        for index, clause in enumerate(baseline_validation_clause_dumps())
        if index not in RETIRED_VALIDATION_CLAUSES
    )

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
    for node in model_tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SystemConfig":
            for statement in node.body:
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id in RETIRED_CONFIG_FIELDS
                ):
                    raise AssertionError(
                        f"retired config field reintroduced: {statement.target.id}"
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
    if len(clauses) != VALIDATION_CLAUSE_COUNT - len(RETIRED_VALIDATION_CLAUSES):
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


from ._compatibility_validation_runtime import (
    _assert_live_candidate_bindings as _assert_live_candidate_bindings,
    baseline_config_module as baseline_config_module,
    exception_observation as exception_observation,
    _validation_pair_count as _validation_pair_count,
    _stimulus_manifest_sha256 as _stimulus_manifest_sha256,
    validation_fixture_metadata as validation_fixture_metadata,
    _canonical_changes as _canonical_changes,
    validate_validation_fixture_shape as validate_validation_fixture_shape,
    capture_validation_contract as capture_validation_contract,
    _baseline_archive as _baseline_archive,
    _run_baseline_package as _run_baseline_package,
    baseline_method_contract as baseline_method_contract,
    baseline_load_method_pickles as baseline_load_method_pickles,
    current_method_contract as current_method_contract,
    current_load_method_pickles as current_load_method_pickles,
    __all__ as __all__,
)
