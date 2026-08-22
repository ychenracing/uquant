"""Behavioral Task 3 capture helpers bound to the immutable Task 1 tree."""

from __future__ import annotations

import base64
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
ORDER_PROBE_CASE_COUNT = 7
UNKNOWN_KEYWORD_CASE_INDEX = 195
METHOD_IDS = (
    "uquant.config:SystemConfig.override",
    "uquant.config:SystemConfig.to_dict",
    "uquant.types:AccountState.empty",
    "uquant.types:AccountState.to_dict",
    "uquant.types:Decision.canonical_payload",
    "uquant.types:Decision.legacy_canonical_payload",
    "uquant.types:Position.sellable_shares",
)

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
        "order_probe_case_count": ORDER_PROBE_CASE_COUNT,
        "pair_case_count": pair_count,
        "unknown_keyword_case_index": UNKNOWN_KEYWORD_CASE_INDEX,
    }


def capture_validation_contract(
    fixture: Mapping[str, object],
) -> dict[str, object]:
    """Replay every stored stimulus against immutable baseline behavior."""

    cases = cast(Sequence[Mapping[str, object]], fixture["cases"])
    baseline_default = baseline_config_module().DEFAULT_CONFIG
    captured = [
        {
            "changes": dict(cast(Mapping[str, object], case["changes"])),
            **exception_observation(
                baseline_default,
                cast(Mapping[str, object], case["changes"]),
            ),
        }
        for case in cases
    ]
    return {
        "baseline": validation_fixture_metadata(cases),
        "cases": captured,
        "schema_version": 1,
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
    "baseline_config_module",
    "baseline_load_method_pickles",
    "baseline_method_contract",
    "capture_validation_contract",
    "current_load_method_pickles",
    "current_method_contract",
    "exception_observation",
)
