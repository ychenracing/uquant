"""Runtime fixtures and contract captures for Task 3 compatibility."""

from __future__ import annotations

import ast
import base64
import hashlib
import importlib
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
from ._compatibility_baseline import (
    _METHOD_CONTRACT_SCRIPT,
    _UNKNOWN_KEYWORD_CHANGES,
    BASELINE_CONFIG_PATH,
    CANDIDATE_CONFIG_MODEL_PATH,
    GOVERNED_EXTERNAL_GLOBALS,
    ISOLATED_VALIDATION_CASE_COUNT,
    METHOD_IDS,
    ORDER_PROBE_CASE_COUNT,
    ORDER_PROBE_START_INDEX,
    PAIR_CASE_COUNT,
    REACHABLE_WITNESS_CASE_COUNT,
    REACHABLE_WITNESS_START_INDEX,
    REACHABLE_WITNESSES,
    STRUCTURAL_ONLY_ADJACENT_SWAPS,
    TOTAL_VALIDATION_CASE_COUNT,
    UNKNOWN_KEYWORD_CASE_INDEX,
    VALIDATION_CLAUSE_COUNT,
    VALIDATION_STIMULUS_MANIFEST_SHA256,
    _assert_exact_runtime_function,
    _assert_exact_runtime_globals,
    _body_without_docstring,
    _expected_code,
    _top_level_helper_call,
    _validation_module_name,
    baseline_validation_clause_dumps,
    candidate_validation_clause_dumps,
    git_blob,
)


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
    module_name = f"_uquant_compatibility_baseline_config_{BASELINE_COMMIT[:12]}"
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
