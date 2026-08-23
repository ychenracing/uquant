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
HOLDOUT_OWNERS = (
    "uquant/validation/holdout/__init__.py",
    "uquant/validation/holdout/contract.py",
    "uquant/validation/holdout/source_identity.py",
    "uquant/validation/holdout/manifest.py",
    "uquant/validation/holdout/lanes.py",
    "uquant/validation/holdout/snapshots.py",
    "uquant/validation/holdout/replay.py",
    "uquant/validation/holdout/checkpoints.py",
    "uquant/validation/holdout/artifact_transaction.py",
    "uquant/validation/holdout/service.py",
)
HOLDOUT_RUNTIME_FACADE = "uquant/validation/holdout_runtime.py"
HOLDOUT_LANES_FACADE = "uquant/validation/holdout_lanes.py"
RELOCATIONS = {
    "uquant/validation/generalization.py": GENERALIZATION_OWNERS,
    "uquant/validation/generalization_reference.py": POLICY_OWNERS,
    "uquant/validation/holdout.py": (
        HOLDOUT_OWNERS[0],
        HOLDOUT_OWNERS[1],
        HOLDOUT_OWNERS[2],
        HOLDOUT_OWNERS[3],
        HOLDOUT_OWNERS[9],
    ),
    HOLDOUT_RUNTIME_FACADE: (
        HOLDOUT_RUNTIME_FACADE,
        HOLDOUT_OWNERS[5],
        HOLDOUT_OWNERS[6],
        HOLDOUT_OWNERS[7],
        HOLDOUT_OWNERS[8],
        HOLDOUT_OWNERS[9],
    ),
    HOLDOUT_LANES_FACADE: (HOLDOUT_LANES_FACADE, HOLDOUT_OWNERS[4]),
}
_SOURCE_SLICES = {
    GENERALIZATION_OWNERS[1]: (("uquant/validation/generalization.py", ((34, 269),)),),
    GENERALIZATION_OWNERS[2]: (("uquant/validation/generalization.py", ((272, 626),)),),
    GENERALIZATION_OWNERS[3]: (("uquant/validation/generalization.py", ((629, 938),)),),
    GENERALIZATION_OWNERS[4]: (("uquant/validation/generalization.py", ((941, 1213),)),),
    GENERALIZATION_OWNERS[5]: (("uquant/validation/generalization.py", ((1216, 1444),)),),
    GENERALIZATION_OWNERS[6]: (("uquant/validation/generalization.py", ((1447, 1800),)),),
    GENERALIZATION_OWNERS[7]: (("uquant/validation/generalization.py", ((1803, 1937),)),),
    POLICY_OWNERS[2]: (("uquant/validation/generalization_reference.py", ((39, 474),)),),
    POLICY_OWNERS[3]: (("uquant/validation/generalization_reference.py", ((477, 796),)),),
    POLICY_OWNERS[4]: (("uquant/validation/generalization_reference.py", ((928, 1097),)),),
    POLICY_OWNERS[5]: (("uquant/validation/generalization_reference.py", ((799, 925), (1100, 1778))),),
    HOLDOUT_OWNERS[1]: (
        (HOLDOUT_RUNTIME_FACADE, ((105, 105),)),
        ("uquant/validation/holdout.py", ((27, 210), (254, 530), (584, 597), (1155, 1179))),
    ),
    HOLDOUT_OWNERS[2]: (("uquant/validation/holdout.py", ((213, 251), (533, 581), (763, 1152))),),
    HOLDOUT_OWNERS[3]: (("uquant/validation/holdout.py", ((600, 760),)),),
    HOLDOUT_OWNERS[4]: ((HOLDOUT_LANES_FACADE, ((14, 354),)),),
    HOLDOUT_OWNERS[5]: ((HOLDOUT_RUNTIME_FACADE, ((122, 195), (360, 556))),),
    HOLDOUT_OWNERS[6]: ((HOLDOUT_RUNTIME_FACADE, ((54, 87), (559, 862), (1040, 1060), (1302, 1316))),),
    HOLDOUT_OWNERS[7]: ((HOLDOUT_RUNTIME_FACADE, ((88, 104), (865, 1037))),),
    HOLDOUT_OWNERS[8]: ((HOLDOUT_RUNTIME_FACADE, ((106, 119), (129, 132), (198, 357), (1063, 1299))),),
    HOLDOUT_OWNERS[9]: (
        ("uquant/validation/holdout.py", ((1182, 1352),)),
        (HOLDOUT_RUNTIME_FACADE, ((1319, 1519),)),
    ),
}
_TRANSPORT_HELPERS = {
    "compatibility_value",
    "runtime_compatibility_value",
    "_compatibility_head_and_source",
}
_PATH_TRANSPORT_HELPERS = {HOLDOUT_OWNERS[9]: {"append_holdout_snapshot"}}
_PATH_TRANSPORT_ASSIGNS: dict[str, set[str]] = {}
_RETAINED_FACADES = {
    "uquant/validation/generalization_reference.py",
    HOLDOUT_RUNTIME_FACADE,
    HOLDOUT_LANES_FACADE,
}
_FACADE_SOURCE_SLICES = {
    HOLDOUT_RUNTIME_FACADE: ((HOLDOUT_RUNTIME_FACADE, ((1, 1519),)),),
    HOLDOUT_LANES_FACADE: ((HOLDOUT_LANES_FACADE, ((1, 377),)),),
}


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

    approved: dict[str, tuple[str, ...]] = {}
    for legacy, declared in RELOCATIONS.items():
        entry = _entry(inventory, legacy)
        paths = set(entry["symbol_owner_mapping"].values())
        if legacy == "uquant/validation/generalization_reference.py":
            paths.add("uquant/validation/generalization_policy/__init__.py")
        approved[legacy] = tuple(sorted(paths))
        assert approved[legacy] == tuple(sorted(declared))
    return approved


def _expected_registry(root: Path) -> dict[str, Any]:
    registry = json.loads(_git_source(root, "benchmarks/source_surface_registry.json"))
    for surface in registry["surfaces"]:
        paths = set(surface["source_paths"])
        for legacy, owners in RELOCATIONS.items():
            if legacy not in paths:
                continue
            if legacy not in _RETAINED_FACADES:
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
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node = cast(ast.FunctionDef, self.generic_visit(node))
        if node.name == "append_holdout_snapshot":
            node.args.kwonlyargs = [
                argument
                for argument in node.args.kwonlyargs
                if argument.arg not in {"_read_checkpoint", "_verify_checkpoint"}
            ]
            node.args.kw_defaults = node.args.kw_defaults[:3]
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        replacements = {
            "_read_checkpoint": "_read_checkpoint_carrier",
            "_verify_checkpoint": "_verify_checkpoint_artifacts",
        }
        node.id = replacements.get(node.id, node.id)
        return node

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
            and node.func.id in {"compatibility_value", "runtime_compatibility_value"}
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

    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST:
        node = cast(ast.ImportFrom, self.generic_visit(node))
        if node.module == "account" and node.level == 3:
            node.level = 2
        return node


class _ImmutableTransportNormalizer(ast.NodeTransformer):
    def visit_ImportFrom(self, node: ast.ImportFrom) -> ast.AST | None:
        if (
            node.module == "holdout_runtime"
            and node.level == 1
            and {alias.name for alias in node.names}
            == {"read_future_holdout_replay", "replay_future_holdout"}
        ):
            return None
        return self.generic_visit(node)


def _candidate_statements(source: str, *, owner: str) -> list[ast.stmt]:
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
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in _PATH_TRANSPORT_HELPERS.get(owner, set())
        ):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
                continue
            if any(
                isinstance(target, ast.Name)
                and target.id in _PATH_TRANSPORT_ASSIGNS.get(owner, set())
                for target in targets
            ):
                continue
        statements.append(node)
    return statements


def _immutable_statements(source: str, ranges: tuple[tuple[int, int], ...]) -> list[ast.stmt]:
    return [
        node
        for node in ast.parse(source).body
        if any(start <= node.lineno <= end for start, end in ranges)
    ]


def _defined_top_level_symbols(source: str) -> set[str]:
    """Return names owned by the immutable legacy module itself."""

    symbols: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            symbols.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return symbols


def has_immutable_local_relocation_lineage(
    root: Path,
    *,
    importer_owner: str,
    imported_from_owner: str,
    name: str,
    legacy_sources: Mapping[str, str] | None = None,
) -> bool:
    """Prove a new owner edge was a local immutable legacy reference.

    A split may turn two slices of one legacy module into separate owners.  Such an
    edge is admissible only when the imported symbol was defined in its immutable
    legacy module and the importer's declared slice from that same module loaded it.
    """

    def legacy_source(path: str) -> str:
        if legacy_sources is not None and path in legacy_sources:
            return legacy_sources[path]
        legacy_path = (
            path
            if path.endswith(".py")
            else path.replace(".", "/") + ".py"
        )
        return _git_source(root, legacy_path)

    importer_path = importer_owner.replace(".", "/") + ".py"
    imported_from_path = imported_from_owner.replace(".", "/") + ".py"
    importer_slices = _SOURCE_SLICES.get(
        importer_path, _FACADE_SOURCE_SLICES.get(importer_path, ())
    )
    definition_sources = {
        legacy
        for legacy, _ranges in _SOURCE_SLICES.get(imported_from_path, ())
        if name in _defined_top_level_symbols(legacy_source(legacy))
    }
    return any(
        legacy in definition_sources
        and any(
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == name
            for statement in _immutable_statements(legacy_source(legacy), ranges)
            for node in ast.walk(statement)
        )
        for legacy, ranges in importer_slices
    )


def _normalized_dump(node: ast.stmt, *, candidate: bool) -> str:
    value = copy.deepcopy(node)
    if candidate:
        value = cast(ast.stmt, _TransportNormalizer().visit(value))
    else:
        value = cast(ast.stmt, _ImmutableTransportNormalizer().visit(value))
    return ast.dump(ast.fix_missing_locations(value), include_attributes=False)


def owner_ast_rows(
    root: Path, *, candidate_sources: Mapping[str, str] | None = None
) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    expected: dict[str, tuple[str, ...]] = {}
    observed: dict[str, tuple[str, ...]] = {}
    for owner, slices in _SOURCE_SLICES.items():
        expected[owner] = tuple(
            _normalized_dump(node, candidate=False)
            for legacy, ranges in slices
            for node in _immutable_statements(_git_source(root, legacy), ranges)
        )
        source = (
            (root / owner).read_text(encoding="utf-8")
            if candidate_sources is None or owner not in candidate_sources
            else candidate_sources[owner]
        )
        observed[owner] = tuple(
            _normalized_dump(node, candidate=True)
            for node in _candidate_statements(source, owner=owner)
        )
    return expected, observed


def assert_owner_ast_exact(
    root: Path, *, candidate_sources: Mapping[str, str] | None = None
) -> None:
    expected, observed = owner_ast_rows(root, candidate_sources=candidate_sources)
    assert observed == expected


__all__ = (
    "GENERALIZATION_OWNERS",
    "HOLDOUT_LANES_FACADE",
    "HOLDOUT_OWNERS",
    "HOLDOUT_RUNTIME_FACADE",
    "POLICY_OWNERS",
    "RELOCATIONS",
    "approved_relocations",
    "assert_owner_ast_exact",
    "build_relocation_contract",
    "has_immutable_local_relocation_lineage",
)
