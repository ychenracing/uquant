"""Inventory sealing helpers for private-edge governance."""

from __future__ import annotations

import ast
import hashlib
import io
import json
import tarfile
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from . import _private_import_ast as _private_ast
from ._analysis_authorities import ROOT

INVENTORY_PATH = (
    ROOT
    / "artifacts"
    / "architecture_refactor"
    / "task10_private_import_remediation_inventory.json"
)


class DynamicRowContext(Protocol):
    module: str
    parents: Mapping[ast.AST, ast.AST]
    relative: str


def dynamic_row(
    context: DynamicRowContext,
    *,
    imported_from: str,
    name: str,
    kind: str,
    line: int,
    operation_context: str = "Load",
) -> dict[str, object]:
    return {
        "id": f"{context.module}:{imported_from}:{name}:{kind}:{line}",
        "root": context.module.split(".", 1)[0],
        "path": context.relative,
        "importer": context.module,
        "imported_from": imported_from,
        "name": name,
        "line": line,
        "kind": kind,
        "context": operation_context,
    }


def record_nested_module_transport(
    context: DynamicRowContext,
    node: ast.Import | ast.ImportFrom,
    target: str,
    dynamic: list[dict[str, object]],
) -> None:
    parent = context.parents.get(node)
    if (
        isinstance(parent, ast.Module)
        or target in _private_ast.SELECTIVE_CAPABILITY_MODULES
    ):
        return
    dynamic.append(
        dynamic_row(
            context,
            imported_from=target,
            name="<module-object>",
            kind="dynamic_transport",
            line=node.lineno,
            operation_context=(
                type(parent).__name__ if parent is not None else "Root"
            ),
        )
    )


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_inventory_payload(
    sources: Mapping[str, str],
    observed: Mapping[str, list[dict[str, object]]],
    governed_roots: tuple[str, ...],
    *,
    commit: str,
    tree: str,
) -> dict[str, object]:
    direct = observed["direct"]
    qualified = observed["qualified"]
    direct_by_root = Counter(str(row["root"]) for row in direct)
    qualified_by_root = Counter(str(row["root"]) for row in qualified)
    payload: dict[str, object] = {
        "schema_version": 1,
        "inventory_id": "uquant-task10-private-import-remediation-v1",
        "immutable_review": {"commit": commit, "tree": tree},
        "governed_roots": list(governed_roots),
        "governed_sources": [
            {
                "path": relative,
                "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "bytes": len(source.encode("utf-8")),
                "lines": len(source.splitlines()),
            }
            for relative, source in sorted(sources.items())
        ],
        "direct_private_imports": direct,
        "qualified_private_accesses": qualified,
        "counts": {
            "direct": len(direct),
            "qualified": len(qualified),
            "total": len(direct) + len(qualified),
        },
        "direct_by_root": {
            governed_root: direct_by_root[governed_root]
            for governed_root in governed_roots
        },
        "qualified_by_root": {
            governed_root: qualified_by_root[governed_root]
            for governed_root in governed_roots
        },
        "policy": (
            "Every listed edge is current live debt. Historical Task 5-9 relocation "
            "identities are evidence only and never an acceptance allowlist."
        ),
    }
    payload["artifact_sha256"] = canonical_sha256(payload)
    return payload


def verify_inventory_seal(payload: Mapping[str, object]) -> None:
    unsigned = dict(payload)
    expected = unsigned.pop("artifact_sha256", None)
    if expected != canonical_sha256(unsigned):
        raise AssertionError("private-import inventory seal is stale")


def load_inventory(path: Path = INVENTORY_PATH) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("private-import inventory must be an object")
    return payload


def write_inventory(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(rendered + "\n", encoding="utf-8")


def safe_extract_archive(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != root and root not in target.parents:
                raise AssertionError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise AssertionError(f"immutable archive contains link: {member.name}")
        bundle.extractall(destination, filter="data")
