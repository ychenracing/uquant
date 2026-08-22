"""Behavioral Task 3 capture helpers bound to the immutable Task 1 tree."""

from __future__ import annotations

import ast
import base64
import copy
import hashlib
import io
import json
import os
import pickle
import subprocess
import sys
import tarfile
import tempfile
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
OVERLAP_WITNESS_START_INDEX = ORDER_PROBE_START_INDEX + ORDER_PROBE_CASE_COUNT
OVERLAP_WITNESS_CASE_COUNT = 4
UNKNOWN_KEYWORD_CASE_INDEX = OVERLAP_WITNESS_START_INDEX + OVERLAP_WITNESS_CASE_COUNT
TOTAL_VALIDATION_CASE_COUNT = UNKNOWN_KEYWORD_CASE_INDEX + 1
VALIDATION_CLAUSE_COUNT = 159
OVERLAP_WITNESSES = (
    (
        OVERLAP_WITNESS_START_INDEX,
        "leader-cycle-market-range-before-impulse-relation",
    ),
    (
        OVERLAP_WITNESS_START_INDEX + 1,
        "strategic-transition-max-range-before-inverted-range",
    ),
    (
        OVERLAP_WITNESS_START_INDEX + 2,
        "transition-range-before-repair-relation",
    ),
    (
        OVERLAP_WITNESS_START_INDEX + 3,
        "transition-repair-relation-before-chronic-window",
    ),
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
_COMPARISON_PROBE_KEY = "__task3_comparison_probe__"
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


class _TransitionFreezeProbe:
    """Deterministic non-transitive comparable for reachable order probes."""

    __slots__ = ("_range_is_valid",)

    def __init__(self, *, range_is_valid: bool) -> None:
        self._range_is_valid = range_is_valid

    def __ge__(self, other: object) -> bool:
        del other
        return True

    def __le__(self, other: object) -> bool:
        if other == 1:
            return self._range_is_valid
        return True


class _ConfigArgumentNormalizer(ast.NodeTransformer):
    """Normalize only a mechanically relocated validator argument to ``self``."""

    def __init__(self, argument_name: str) -> None:
        self._argument_name = argument_name

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id != self._argument_name:
            return node
        return ast.copy_location(ast.Name(id="self", ctx=node.ctx), node)


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
        tree = ast.parse(source_for(path), filename=path)
        path_functions: dict[str, ast.FunctionDef] = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in path_functions:
                raise AssertionError(f"duplicate validator helper in {path}: {node.name}")
            path_functions[node.name] = node
        functions[path] = path_functions

    model_tree = ast.parse(
        source_for(CANDIDATE_CONFIG_MODEL_PATH),
        filename=CANDIDATE_CONFIG_MODEL_PATH,
    )
    root = _system_config_post_init(model_tree)

    model_imports: dict[str, tuple[str, str]] = {}
    for statement in model_tree.body:
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
            local_name = alias.asname or alias.name
            if local_name in model_imports:
                raise AssertionError(f"duplicate validator import alias: {local_name}")
            model_imports[local_name] = (imported_path, alias.name)

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
    return clauses


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


def _materialize_change_value(value: object) -> object:
    if not isinstance(value, Mapping) or set(value) != {_COMPARISON_PROBE_KEY}:
        return value
    mode = value[_COMPARISON_PROBE_KEY]
    if mode == "range-and-relation":
        return _TransitionFreezeProbe(range_is_valid=False)
    if mode == "relation-only":
        return _TransitionFreezeProbe(range_is_valid=True)
    raise AssertionError(f"unknown Task 3 comparison probe: {mode!r}")


def materialize_validation_changes(
    changes: Mapping[str, object],
) -> dict[str, object]:
    """Resolve deterministic JSON fixture probes into public override values."""

    return {
        name: _materialize_change_value(value)
        for name, value in changes.items()
    }


def exception_observation(config: object, changes: Mapping[str, object]) -> dict[str, str]:
    """Capture the exact public exception outcome for one flat override."""

    override = cast(Any, config.override)
    try:
        override(**materialize_validation_changes(changes))
    except Exception as exc:
        return {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
    raise AssertionError(f"expected invalid override to fail: {dict(changes)!r}")


def validation_fixture_metadata(cases: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Return deterministic provenance and matrix dimensions for the oracle."""

    isolated = cases[:ISOLATED_VALIDATION_CASE_COUNT]
    pair_count = sum(
        1
        for index, left in enumerate(isolated)
        for right in isolated[index + 1 :]
        if set(cast(Mapping[str, object], left["changes"])).isdisjoint(
            cast(Mapping[str, object], right["changes"])
        )
    )
    return {
        "baseline_blob_sha256": hashlib.sha256(git_blob(BASELINE_CONFIG_PATH)).hexdigest(),
        "baseline_commit": BASELINE_COMMIT,
        "baseline_path": BASELINE_CONFIG_PATH,
        "isolated_case_count": ISOLATED_VALIDATION_CASE_COUNT,
        "order_probe_start_index": ORDER_PROBE_START_INDEX,
        "order_probe_case_count": ORDER_PROBE_CASE_COUNT,
        "order_probe_indexes": list(
            range(ORDER_PROBE_START_INDEX, OVERLAP_WITNESS_START_INDEX)
        ),
        "overlap_witness_start_index": OVERLAP_WITNESS_START_INDEX,
        "overlap_witness_case_count": OVERLAP_WITNESS_CASE_COUNT,
        "overlap_witness_indexes": list(
            range(OVERLAP_WITNESS_START_INDEX, UNKNOWN_KEYWORD_CASE_INDEX)
        ),
        "overlap_witnesses": [
            {"case_index": case_index, "witness_id": witness_id}
            for case_index, witness_id in OVERLAP_WITNESSES
        ],
        "pair_case_count": pair_count,
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
    if fixture["schema_version"] != 2:
        raise AssertionError("validation fixture schema_version must be 2")
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
    if fixture["baseline"] != validation_fixture_metadata(cases):
        raise AssertionError("validation fixture metadata is not the exact baseline partition")

    canonical_changes = [_canonical_changes(case) for case in cases]
    if len(set(canonical_changes)) != len(canonical_changes):
        raise AssertionError("validation fixture contains duplicate stimuli")

    baseline_fields = set(cast(Any, baseline_config_module().DEFAULT_CONFIG).to_dict())
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

    order_probes = cases[ORDER_PROBE_START_INDEX:OVERLAP_WITNESS_START_INDEX]
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

    witnesses = cases[OVERLAP_WITNESS_START_INDEX:UNKNOWN_KEYWORD_CASE_INDEX]
    if len(witnesses) != OVERLAP_WITNESS_CASE_COUNT:
        raise AssertionError("overlap-witness partition length changed")
    for (expected_index, expected_id), case in zip(
        OVERLAP_WITNESSES,
        witnesses,
        strict=True,
    ):
        if set(case) != {"changes", "exception_type", "message", "witness_id"}:
            raise AssertionError(f"overlap witness {expected_index} has invalid fields")
        if case["witness_id"] != expected_id:
            raise AssertionError(f"overlap witness {expected_index} has the wrong id")
        if case["exception_type"] != "ValueError":
            raise AssertionError("overlap witnesses must be validation failures")
        changes = cast(Mapping[str, object], case["changes"])
        if not set(changes).issubset(baseline_fields):
            raise AssertionError("overlap witnesses must target governed fields")

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
        "schema_version": 2,
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
    "OVERLAP_WITNESS_CASE_COUNT",
    "OVERLAP_WITNESS_START_INDEX",
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
    "materialize_validation_changes",
    "validate_validation_fixture_shape",
)
