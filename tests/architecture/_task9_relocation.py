"""Immutable source-relocation checks for Task 9 validation owners."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from uquant.contracts.strict_json import canonical_json_sha256

TASK9_START = "719288f6067686b3199d305899ddc09adf098a0d"
TASK9_START_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"
GENERALIZATION_OWNERS = (
    "uquant/validation/generalization/__init__.py",
    "uquant/validation/generalization/models.py",
    "uquant/validation/generalization/scenarios.py",
    "uquant/validation/generalization/provenance.py",
    "uquant/validation/generalization/baseline.py",
    "uquant/validation/generalization/metrics.py",
    "uquant/validation/generalization/gates.py",
    "uquant/validation/generalization/runner.py",
)
POLICY_OWNERS = (
    "uquant/validation/generalization_reference.py",
    "uquant/validation/generalization_policy/__init__.py",
    "uquant/validation/generalization_policy/schema.py",
    "uquant/validation/generalization_policy/cells.py",
    "uquant/validation/generalization_policy/projection.py",
    "uquant/validation/generalization_policy/evaluator.py",
)
RELOCATIONS = {
    "uquant/validation/generalization.py": GENERALIZATION_OWNERS,
    "uquant/validation/generalization_reference.py": POLICY_OWNERS,
}
_SOURCE_RANGES = {
    GENERALIZATION_OWNERS[1]: ((34, 269),),
    GENERALIZATION_OWNERS[2]: ((272, 626),),
    GENERALIZATION_OWNERS[3]: ((629, 938),),
    GENERALIZATION_OWNERS[4]: ((941, 1213),),
    GENERALIZATION_OWNERS[5]: ((1216, 1444),),
    GENERALIZATION_OWNERS[6]: ((1447, 1800),),
    GENERALIZATION_OWNERS[7]: ((1803, 1937),),
    POLICY_OWNERS[2]: ((39, 474),),
    POLICY_OWNERS[3]: ((477, 796),),
    POLICY_OWNERS[4]: ((928, 1097),),
    POLICY_OWNERS[5]: ((799, 925), (1100, 1778)),
}
_OWNER_LEGACY = {
    path: "uquant/validation/generalization.py"
    for path in GENERALIZATION_OWNERS[1:]
} | {
    path: "uquant/validation/generalization_reference.py"
    for path in POLICY_OWNERS[2:]
}
_TRANSPORT_HELPERS = {"compatibility_value", "_compatibility_head_and_source"}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True
    ).stdout


def _git_source(root: Path, path: str) -> str:
    return _git(root, "show", f"{TASK9_START}:{path}").decode()


def _entry(inventory: Mapping[str, Any], path: str) -> Mapping[str, Any]:
    return next(
        cast(Mapping[str, Any], item)
        for item in cast(list[object], inventory["entries"])
        if cast(Mapping[str, Any], item)["path"] == path
    )


def approved_relocations(inventory: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Bind owner paths to the immutable inventory, not candidate declarations."""

    generalization = _entry(inventory, "uquant/validation/generalization.py")
    policy = _entry(inventory, "uquant/validation/generalization_reference.py")
    generalization_paths = tuple(
        sorted(set(generalization["symbol_owner_mapping"].values()))
    )
    policy_paths = tuple(
        sorted(
            {
                *policy["symbol_owner_mapping"].values(),
                "uquant/validation/generalization_policy/__init__.py",
            }
        )
    )
    assert generalization_paths == tuple(sorted(GENERALIZATION_OWNERS))
    assert policy_paths == tuple(sorted(POLICY_OWNERS))
    return {
        "uquant/validation/generalization.py": generalization_paths,
        "uquant/validation/generalization_reference.py": policy_paths,
    }


def _expected_registry(root: Path) -> dict[str, Any]:
    registry = json.loads(_git_source(root, "benchmarks/source_surface_registry.json"))
    for surface in registry["surfaces"]:
        paths = set(surface["source_paths"])
        for legacy, owners in RELOCATIONS.items():
            if legacy not in paths:
                continue
            if legacy != "uquant/validation/generalization_reference.py":
                paths.remove(legacy)
            paths.update(owners)
        surface["source_paths"] = sorted(paths)
    del registry["canonical_sha256"]
    registry["canonical_sha256"] = canonical_json_sha256(registry)
    return cast(dict[str, Any], registry)


def build_relocation_contract(
    root: Path, inventory: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the closed relocation evidence from immutable and current sources."""

    root = root.resolve()
    approved = approved_relocations(inventory)
    entries: list[dict[str, Any]] = []
    for legacy, owners in approved.items():
        immutable = _entry(inventory, legacy)
        restored = _git(root, "cat-file", "blob", str(immutable["git_blob_sha1"]))
        assert len(restored) == immutable["size_bytes"]
        assert hashlib.sha256(restored).hexdigest() == immutable["content_sha256"]
        owner_rows = []
        for path in owners:
            content = (root / path).read_bytes()
            owner_rows.append(
                {
                    "path": path,
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        entries.append(
            {
                "legacy_path": legacy,
                "legacy_blob_sha1": immutable["git_blob_sha1"],
                "legacy_content_sha256": immutable["content_sha256"],
                "legacy_size_bytes": immutable["size_bytes"],
                "owners": owner_rows,
            }
        )
    requirements = (root / "requirements.txt").read_bytes()
    baseline_requirements = _git(root, "show", f"{TASK9_START}:requirements.txt")
    assert requirements == baseline_requirements
    candidate_registry = json.loads(
        (root / "benchmarks/source_surface_registry.json").read_text(encoding="utf-8")
    )
    assert candidate_registry == _expected_registry(root)
    payload: dict[str, Any] = {
        "contract": "uquant-task9-approved-source-relocation-v1",
        "baseline_commit": TASK9_START,
        "baseline_tree": TASK9_START_TREE,
        "entries": entries,
        "requirements": {
            "size_bytes": len(requirements),
            "sha256": hashlib.sha256(requirements).hexdigest(),
        },
        "source_surface_registry_sha256": canonical_json_sha256(candidate_registry),
    }
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    return payload


class _TransportNormalizer(ast.NodeTransformer):
    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        node = cast(ast.Subscript, self.generic_visit(node))
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == 3
        ):
            node.slice.value = 2
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = cast(ast.Call, self.generic_visit(node))
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "compatibility_value"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return copy.deepcopy(node.args[1])
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "_compatibility_head_and_source"
        ):
            node.func.id = "_head_and_source"
        return node


def _candidate_statements(source: str) -> list[ast.stmt]:
    statements: list[ast.stmt] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _TRANSPORT_HELPERS:
            continue
        statements.append(node)
    return statements


def _immutable_statements(source: str, ranges: tuple[tuple[int, int], ...]) -> list[ast.stmt]:
    return [
        node
        for node in ast.parse(source).body
        if any(start <= node.lineno <= end for start, end in ranges)
    ]


def _normalized_dump(node: ast.stmt, *, candidate: bool) -> str:
    value = copy.deepcopy(node)
    if candidate:
        value = cast(ast.stmt, _TransportNormalizer().visit(value))
    return ast.dump(ast.fix_missing_locations(value), include_attributes=False)


def owner_ast_rows(
    root: Path, *, candidate_sources: Mapping[str, str] | None = None
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    expected: dict[str, tuple[str, ...]] = {}
    observed: dict[str, tuple[str, ...]] = {}
    for owner, ranges in _SOURCE_RANGES.items():
        legacy = _OWNER_LEGACY[owner]
        expected[owner] = tuple(
            _normalized_dump(node, candidate=False)
            for node in _immutable_statements(_git_source(root, legacy), ranges)
        )
        source = (
            (root / owner).read_text(encoding="utf-8")
            if candidate_sources is None or owner not in candidate_sources
            else candidate_sources[owner]
        )
        observed[owner] = tuple(
            _normalized_dump(node, candidate=True)
            for node in _candidate_statements(source)
        )
    return expected, observed


def assert_owner_ast_exact(
    root: Path, *, candidate_sources: Mapping[str, str] | None = None
) -> None:
    expected, observed = owner_ast_rows(root, candidate_sources=candidate_sources)
    assert observed == expected


__all__ = (
    "GENERALIZATION_OWNERS",
    "POLICY_OWNERS",
    "RELOCATIONS",
    "approved_relocations",
    "assert_owner_ast_exact",
    "build_relocation_contract",
)
