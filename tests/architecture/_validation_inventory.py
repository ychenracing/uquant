from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, cast

from uquant.contracts.strict_json import canonical_json_sha256

VALIDATION_REFERENCE_COMMIT = "719288f6067686b3199d305899ddc09adf098a0d"
VALIDATION_REFERENCE_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"

LEGACY_IMPLEMENTATIONS = (
    (
        "uquant/validation/generalization.py",
        "uquant.validation.generalization",
        "REPLACE_WITH_SAME_NAME_PACKAGE_AFTER_EXACT_PARITY",
    ),
    (
        "uquant/validation/generalization_reference.py",
        "uquant.validation.generalization_reference",
        "RETAIN_THIN_COMPATIBILITY_FACADE_AFTER_EXACT_PARITY",
    ),
    (
        "uquant/validation/holdout.py",
        "uquant.validation.holdout",
        "REPLACE_WITH_SAME_NAME_PACKAGE_AFTER_EXACT_PARITY",
    ),
    (
        "uquant/validation/holdout_runtime.py",
        "uquant.validation.holdout_runtime",
        "RETAIN_THIN_COMPATIBILITY_FACADE_AFTER_EXACT_PARITY",
    ),
    (
        "uquant/validation/holdout_lanes.py",
        "uquant.validation.holdout_lanes",
        "RETAIN_THIN_COMPATIBILITY_FACADE_AFTER_EXACT_PARITY",
    ),
    (
        "uquant/risk_sentinel/cli.py",
        "uquant.risk_sentinel.cli",
        "RETAIN_CLI_WITH_PROVENANCE_DELEGATION",
    ),
    (
        "uquant/risk_sentinel/validation.py",
        "uquant.risk_sentinel.validation",
        "RETAIN_VALIDATION_WITH_PROVENANCE_IMPORT",
    ),
)

_OWNER_MAPS: dict[str, dict[str, str]] = {
    "uquant/validation/generalization.py": {
        "public compatibility surface": "uquant/validation/generalization/__init__.py",
        "frozen dataclasses and literal contracts": "uquant/validation/generalization/models.py",
        "pre-window evidence and deterministic scenario matrix": "uquant/validation/generalization/scenarios.py",
        "source/data/config provenance and immutable-input guard": "uquant/validation/generalization/provenance.py",
        "baseline/policy strict parsing and reference payload": "uquant/validation/generalization/baseline.py",
        "PnL, concentration, observation and aggregate metrics": "uquant/validation/generalization/metrics.py",
        "dominance/Pareto/scenario/dependency gates": "uquant/validation/generalization/gates.py",
        "fixed-order production/custom replay orchestration": "uquant/validation/generalization/runner.py",
    },
    "uquant/validation/generalization_reference.py": {
        "thin frozen public compatibility surface": "uquant/validation/generalization_reference.py",
        "strict JSON, constants, schemas and frozen models": "uquant/validation/generalization_policy/schema.py",
        "baseline/policy cells and reviewed contract loading": "uquant/validation/generalization_policy/cells.py",
        "cross-version economic/attribution projection": "uquant/validation/generalization_policy/projection.py",
        "cell/tail/policy artifact fixed-order evaluation": "uquant/validation/generalization_policy/evaluator.py",
    },
    "uquant/validation/holdout.py": {
        "same-name public compatibility surface": "uquant/validation/holdout/__init__.py",
        "sealed dates/constants/contract and layout": "uquant/validation/holdout/contract.py",
        "source/CLI/account/runtime identity": "uquant/validation/holdout/source_identity.py",
        "manifest schema/build/readback": "uquant/validation/holdout/manifest.py",
        "manifest top-level generation service": "uquant/validation/holdout/service.py",
    },
    "uquant/validation/holdout_runtime.py": {
        "thin runtime compatibility surface": "uquant/validation/holdout_runtime.py",
        "daily data capture/append/overlay": "uquant/validation/holdout/snapshots.py",
        "deterministic replay/readback/daily decision": "uquant/validation/holdout/replay.py",
        "checkpoint carrier and continuity": "uquant/validation/holdout/checkpoints.py",
        "path safety/locks/atomic bundle/rollback": "uquant/validation/holdout/artifact_transaction.py",
        "top-level replay generation": "uquant/validation/holdout/service.py",
    },
    "uquant/validation/holdout_lanes.py": {
        "thin lanes compatibility surface": "uquant/validation/holdout_lanes.py",
        "append-only lane models/registry/transition/report": "uquant/validation/holdout/lanes.py",
    },
    "uquant/risk_sentinel/cli.py": {
        "Sentinel source fingerprint only": "uquant/risk_sentinel/provenance.py",
        "all CLI parsing/shadow/report behavior": "uquant/risk_sentinel/cli.py",
    },
    "uquant/risk_sentinel/validation.py": {
        "offline validation behavior": "uquant/risk_sentinel/validation.py",
        "fingerprint import": "uquant/risk_sentinel/provenance.py",
    },
}

_AUTHORITY_REASONS = {
    "uquant/validation/generalization.py": (
        "Current deterministic scenario/provenance/baseline/metric/gate/runner authority; "
        "it cannot write a reviewed baseline."
    ),
    "uquant/validation/generalization_reference.py": (
        "Current compile-anchored champion baseline, frozen policy, projection and artifact "
        "evaluation authority."
    ),
    "uquant/validation/holdout.py": (
        "Current future-holdout contract, source/account identity, layout and manifest authority."
    ),
    "uquant/validation/holdout_runtime.py": (
        "Current append-only snapshot, deterministic replay, checkpoint and atomic artifact "
        "transaction authority."
    ),
    "uquant/validation/holdout_lanes.py": (
        "Current source-bound append-only lane identity and milestone reporting authority."
    ),
    "uquant/risk_sentinel/cli.py": (
        "Current read-only Sentinel CLI and source-fingerprint owner; economic authority is none."
    ),
    "uquant/risk_sentinel/validation.py": (
        "Current offline Sentinel contract/import-isolation verifier."
    ),
}


def _git(root: Path, *arguments: str, text: bool = False) -> bytes | str:
    output = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=text,
    ).stdout
    return cast(bytes | str, output)


def _git_source(root: Path, path: str) -> bytes:
    value = _git(root, "show", f"{VALIDATION_REFERENCE_TREE}:{path}")
    assert isinstance(value, bytes)
    return value


def immutable_python_sources(root: Path) -> dict[str, bytes]:
    listing = _git(root, "ls-tree", "-r", "--name-only", VALIDATION_REFERENCE_TREE, text=True)
    assert isinstance(listing, str)
    paths = [path for path in listing.splitlines() if path.endswith(".py")]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input="".join(f"{VALIDATION_REFERENCE_TREE}:{path}\n" for path in paths).encode(),
        check=True,
        capture_output=True,
    ).stdout
    stream = io.BytesIO(batch)
    sources: dict[str, bytes] = {}
    for path in paths:
        header = stream.readline().decode("ascii").split()
        assert len(header) == 3 and header[1] == "blob"
        size = int(header[2])
        sources[path] = stream.read(size)
        assert stream.read(1) == b"\n"
    assert not stream.read()
    return sources


def _module(path: str) -> str:
    parts = path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolved_import_from(path: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    module = _module(path)
    package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - (node.level - 1))]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)


def _import_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, Any]]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        symbols: list[str] = []
        for node in sorted(
            (
                item
                for item in ast.walk(ast.parse(source))
                if isinstance(item, (ast.Import, ast.ImportFrom))
            ),
            key=lambda item: (item.lineno, item.col_offset),
        ):
            if isinstance(node, ast.Import):
                values = [f"{leaf} module" for alias in node.names if alias.name == target]
            else:
                imported_from = _resolved_import_from(path, node)
                values = [
                    alias.name if imported_from == target else f"{leaf} module"
                    for alias in node.names
                    if imported_from == target
                    or (imported_from == parent and alias.name == leaf)
                ]
            for value in values:
                if value not in symbols:
                    symbols.append(value)
        if symbols:
            consumers.append({"path": path, "symbols": symbols})
    return consumers


def _module_aliases(path: str, tree: ast.Module, target: str) -> set[str]:
    parent, _, leaf = target.rpartition(".")
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname or alias.name.split(".")[-1]
                for alias in node.names
                if alias.name == target
            )
        elif isinstance(node, ast.ImportFrom):
            imported_from = _resolved_import_from(path, node)
            if imported_from == parent:
                aliases.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == leaf
                )
    return aliases


def _module_attribute_consumers(
    sources: dict[str, bytes], target: str
) -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        tree = ast.parse(source)
        aliases = _module_aliases(path, tree, target)
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "delattr"}
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in aliases
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                attributes.add(node.args[1].value)
        if attributes:
            consumers.append({"path": path, "attributes": sorted(attributes)})
    return consumers


def _dotted_identity_consumers(
    sources: dict[str, bytes], target: str
) -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        values = sorted(
            {
                node.value
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (node.value == target or node.value.startswith(f"{target}."))
            }
        )
        if values:
            consumers.append({"path": path, "values": values})
    return consumers


def _top_level_symbols(source: bytes) -> tuple[str, ...]:
    symbols: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            symbols.extend(
                item.id
                for target in targets
                for item in ast.walk(target)
                if isinstance(item, ast.Name)
            )
    return tuple(dict.fromkeys(symbols))


def _private_import_consumers(
    sources: dict[str, bytes], target: str, private_names: set[str]
) -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and _resolved_import_from(path, node) == target:
                imported.update(alias.name for alias in node.names if alias.name in private_names)
        if imported:
            consumers.append({"path": path, "symbols": sorted(imported)})
    return consumers


def _runtime_seams(sources: dict[str, bytes], target: str) -> list[dict[str, Any]]:
    seams: list[dict[str, Any]] = []
    for path, source in sources.items():
        tree = ast.parse(source)
        aliases = _module_aliases(path, tree, target)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "setattr" or len(node.args) < 2:
                continue
            attribute: str | None = None
            if isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                attribute = node.args[1].value
            first = node.args[0]
            direct_module = isinstance(first, ast.Name) and first.id in aliases
            dotted = (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith(target)
            )
            if attribute is not None and (direct_module or dotted):
                seams.append(
                    {
                        "path": path,
                        "line": node.lineno,
                        "target": ast.unparse(first),
                        "attribute": attribute,
                    }
                )
        seams.extend(
            {
                "path": path,
                "line": node.lineno,
                "target": node.args[0].value,
                "attribute": ast.unparse(node.args[1]),
            }
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith(target)
        )
    unique = {
        (row["path"], row["line"], row["target"], row["attribute"]): row
        for row in seams
    }
    return sorted(unique.values(), key=lambda row: (str(row["path"]), int(row["line"])))


def _fixed_references(root: Path, path: str) -> list[str]:
    output = _git(
        root,
        "grep",
        "-l",
        "--fixed-strings",
        path,
        VALIDATION_REFERENCE_TREE,
        "--",
        ".",
        text=True,
    )
    assert isinstance(output, str)
    return sorted(line.split(":", 1)[1] for line in output.splitlines())


def _reference_classification(paths: list[str]) -> dict[str, list[str]]:
    historical = sorted(
        path
        for path in paths
        if path.startswith("artifacts/")
        or path
        in {
            "benchmarks/architecture_refactor_public_api.json",
            "benchmarks/future_holdout_observation_overlay.json",
            "benchmarks/risk_sentinel_shadow_overlay.json",
        }
    )
    documentation = sorted(
        path
        for path in paths
        if path.startswith("docs/") or path.startswith(".superpowers/")
    )
    current = sorted(
        path
        for path in paths
        if path.endswith(".py")
        or path.startswith(".github/")
        or path
        in {
            "benchmarks/source_surface_registry.json",
            "pyproject.toml",
        }
    )
    classified = set(historical) | set(documentation) | set(current)
    return {
        "current_executable_consumers": current,
        "historical_machine_evidence_to_preserve": historical,
        "documentation_references": documentation,
        "other_current_or_contract_consumers": sorted(set(paths) - classified),
    }


def _literal_resources(root: Path, source: bytes) -> list[dict[str, Any]]:
    listing = _git(root, "ls-tree", "-r", "--name-only", VALIDATION_REFERENCE_TREE, text=True)
    assert isinstance(listing, str)
    tracked = set(listing.splitlines())
    values = sorted(
        {
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                node.value.endswith(".json")
                or node.value in {"requirements.txt", "pyproject.toml", "uv.lock"}
            )
        }
    )
    rows: list[dict[str, Any]] = []
    for value in values:
        matches = sorted(path for path in tracked if path == value or path.endswith(f"/{value}"))
        rows.append({"literal": value, "tracked_matches": matches})
    return rows


def _dependency_edges(path: str, source: bytes) -> list[str]:
    edges: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            edges.update(alias.name for alias in node.names if alias.name.startswith("uquant"))
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolved_import_from(path, node)
            if resolved.startswith("uquant"):
                edges.add(resolved)
    return sorted(edges)


_REFLECTION_SCRIPT = r"""
import builtins, dataclasses, hashlib, importlib, inspect, json, pickle, sys
snapshot = sys.argv[1]
def stable_default(field):
    if field.default is not dataclasses.MISSING:
        return {
            "kind": "value",
            "type": f"{type(field.default).__module__}.{type(field.default).__qualname__}",
            "repr": repr(field.default),
        }
    if field.default_factory is not dataclasses.MISSING:
        factory = field.default_factory
        return {
            "kind": "factory",
            "module": getattr(factory, "__module__", None),
            "qualname": getattr(factory, "__qualname__", None),
        }
    return {"kind": "required"}
public_contract = json.loads(
    open(
        snapshot + "/benchmarks/architecture_refactor_public_api.json",
        encoding="utf-8",
    ).read()
)["contract"]["modules"]
if sys.argv[2] == "block-fcntl":
    real_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("blocked fcntl")
        return real_import(name, *args, **kwargs)
    builtins.__import__ = guarded_import
sys.path[:] = [snapshot] + [entry for entry in sys.path if "__editable__.uquant" not in entry]
sys.meta_path[:] = [finder for finder in sys.meta_path if not finder.__class__.__module__.startswith("__editable___uquant_")]
names = (
    "uquant.validation.generalization",
    "uquant.validation.generalization_reference",
    "uquant.validation.holdout",
    "uquant.validation.holdout_runtime",
    "uquant.validation.holdout_lanes",
    "uquant.risk_sentinel.cli",
    "uquant.risk_sentinel.validation",
)
payload = {}
for module_name in names:
    module = importlib.import_module(module_name)
    public = public_contract[module_name]["public_names"]
    missing = sorted(name for name in public if not hasattr(module, name))
    if missing:
        raise RuntimeError(f"missing frozen public names for {module_name}: {missing}")
    objects = {}
    for name in public:
        value = getattr(module, name)
        if inspect.isfunction(value) or inspect.isclass(value):
            row = {
                "kind": "class" if inspect.isclass(value) else "function",
                "signature": str(inspect.signature(value)),
                "module": value.__module__,
                "qualname": value.__qualname__,
                "raw_docstring_sha256": (
                    None
                    if value.__doc__ is None
                    else hashlib.sha256(value.__doc__.encode()).hexdigest()
                ),
            }
            if inspect.isclass(value):
                encoded = pickle.dumps(value, protocol=5)
                row["class_pickle_sha256"] = hashlib.sha256(encoded).hexdigest()
                row["class_pickle_size"] = len(encoded)
                if dataclasses.is_dataclass(value):
                    row["dataclass_fields"] = [
                        {
                            "name": field.name,
                            "type": str(field.type),
                            "default": stable_default(field),
                            "init": field.init,
                            "repr": field.repr,
                            "compare": field.compare,
                            "kw_only": field.kw_only,
                        }
                        for field in dataclasses.fields(value)
                    ]
            objects[name] = row
    payload[module_name] = {"public_names": public, "objects": objects}
for name, module in list(sys.modules.items()):
    if name == "uquant" or name.startswith("uquant."):
        source = getattr(module, "__file__", None)
        if source is not None and not __import__("pathlib").Path(source).resolve().is_relative_to(__import__("pathlib").Path(snapshot).resolve()):
            raise RuntimeError(f"reflection imported candidate module: {name}")
print(json.dumps(payload, allow_nan=False, sort_keys=True))
"""


def _reflection_from_snapshot(snapshot: Path) -> dict[str, Any]:
    modes = {
        "normal": ((), "allow-fcntl"),
        "optimized": (("-O",), "allow-fcntl"),
        "double_optimized": (("-OO",), "allow-fcntl"),
        "windows_no_fcntl": (("-OO",), "block-fcntl"),
    }
    observed: dict[str, Any] = {}
    for name, (flags, fcntl_mode) in modes.items():
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                *flags,
                "-c",
                _REFLECTION_SCRIPT,
                str(snapshot),
                fcntl_mode,
            ],
            cwd=snapshot,
            check=True,
            capture_output=True,
            text=True,
        )
        value = json.loads(completed.stdout)
        assert isinstance(value, dict)
        observed[name] = value
    return {
        "normal": observed["normal"],
        "mode_sha256": {
            name: canonical_json_sha256(value) for name, value in observed.items()
        },
    }


def current_reflection_contract(root: Path) -> dict[str, Any]:
    return _reflection_from_snapshot(root.resolve())


def _reflection_contract(root: Path) -> dict[str, Any]:
    archive = _git(root, "archive", "--format=tar", VALIDATION_REFERENCE_TREE)
    assert isinstance(archive, bytes)
    with tempfile.TemporaryDirectory(prefix="uquant-task9-inventory-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        snapshot.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            stream.extractall(snapshot, filter="data")
        return _reflection_from_snapshot(snapshot)


def build_validation_inventory(root: Path) -> dict[str, Any]:
    sources = immutable_python_sources(root)
    registry = json.loads(_git_source(root, "benchmarks/source_surface_registry.json"))
    public_api = json.loads(
        _git_source(root, "benchmarks/architecture_refactor_public_api.json")
    )
    reflection = _reflection_contract(root)
    entries: list[dict[str, Any]] = []
    for path, module, disposition in LEGACY_IMPLEMENTATIONS:
        source = _git_source(root, path)
        blob = _git(root, "rev-parse", f"{VALIDATION_REFERENCE_TREE}:{path}", text=True)
        assert isinstance(blob, str)
        references = _fixed_references(root, path)
        memberships = [
            surface["id"]
            for surface in registry["surfaces"]
            if path in surface["source_paths"]
        ]
        symbols = _top_level_symbols(source)
        private = {name for name in symbols if name.startswith("_")}
        entries.append(
            {
                "path": path,
                "module": module,
                "classification": (
                    "CONSOLIDATE_THEN_DELETE"
                    if disposition.startswith("REPLACE_WITH")
                    else "CONSOLIDATE_THEN_RETAIN_FACADE"
                ),
                "disposition": disposition,
                "authority_reason": _AUTHORITY_REASONS[path],
                "git_blob_sha1": blob.strip(),
                "content_sha256": hashlib.sha256(source).hexdigest(),
                "size_bytes": len(source),
                "restore_commands": [
                    f"git cat-file blob {blob.strip()}",
                    f"git restore --source={VALIDATION_REFERENCE_COMMIT} --staged --worktree -- {path}",
                ],
                "symbol_owner_mapping": _OWNER_MAPS[path],
                "defined_top_level_symbols": list(symbols),
                "source_surface_memberships": memberships,
                "public_api_contract": {
                    "path": public_api["contract"]["modules"][module]["path"],
                    "public_names": public_api["contract"]["modules"][module][
                        "public_names"
                    ],
                    "canonical_sha256": canonical_json_sha256(
                        public_api["contract"]["modules"][module]
                    ),
                },
                "runtime_public_contract": reflection["normal"][module],
                "package_data_and_resource_literals": _literal_resources(root, source),
                "dependency_edges": _dependency_edges(path, source),
                "live_references": {
                    "immutable_fixed_path_consumers": references,
                    "ast_import_consumers": _import_consumers(sources, module),
                    "runtime_module_attribute_consumers": _module_attribute_consumers(
                        sources, module
                    ),
                    "dotted_runtime_identity_consumers": _dotted_identity_consumers(
                        sources, module
                    ),
                    "cross_module_private_import_consumers": _private_import_consumers(
                        sources, module, private
                    ),
                    "runtime_monkeypatch_seams": _runtime_seams(sources, module),
                    **_reference_classification(references),
                },
            }
        )
    payload: dict[str, Any] = {
        "baseline_commit": VALIDATION_REFERENCE_COMMIT,
        "baseline_tree": VALIDATION_REFERENCE_TREE,
        "contract": "uquant-task9-pre-replacement-validation-inventory-v1",
        "derivation": {
            "authority": (
                "Only Git objects reachable from immutable Task 8 commit "
                f"{VALIDATION_REFERENCE_COMMIT} and fresh imports from its git archive are authority inputs."
            ),
            "checks": [
                "immutable blob SHA-1/content SHA-256/bytes and restore commands",
                "bidirectional git-grep fixed paths with executable/historical/docs classification",
                "AST imports/module attributes/dotted identities/private imports/monkeypatch seams",
                "Task 1 public API plus fresh normal/-O/-OO/no-fcntl reflection and pickle",
                "generation-2 source memberships, resource literals and dependency edges",
            ],
            "preservation_rulings": {
                "historical_artifacts_contracts_registries_closure": (
                    "KEEP_BYTE_IDENTICAL_AND_VERIFY_AT_RECORDED_SOURCE"
                ),
                "current_source_surface_registry": (
                    "MIGRATE_ONLY_REVIEWED_CURRENT_VALIDATION_SENTINEL_MEMBERS"
                ),
                "future_holdout_lanes": (
                    "KEEP_IDENTITIES_AND_OBSERVATIONS_APPEND_ONLY_SOURCE_BOUND_NO_BACKFILL"
                ),
                "journal_v1": "KEEP_READ_ONLY_COMPATIBLE",
                "requirements.txt": "KEEP_PRESENT_AND_BYTE_IDENTICAL",
            },
        },
        "captured_while_all_implementations_intact": [
            path for path, _, _ in LEGACY_IMPLEMENTATIONS
        ],
        "public_runtime_contract": {
            "modules": reflection["normal"],
            "import_mode_sha256": reflection["mode_sha256"],
        },
        "immutable_multinode_sccs": [
            [
                "uquant.validation.holdout",
                "uquant.validation.holdout_lanes",
                "uquant.validation.holdout_runtime",
            ],
            [
                "uquant.risk_sentinel.cli",
                "uquant.risk_sentinel.validation",
            ],
        ],
        "entries": entries,
    }
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    return payload


__all__ = (
    "LEGACY_IMPLEMENTATIONS",
    "VALIDATION_REFERENCE_COMMIT",
    "VALIDATION_REFERENCE_TREE",
    "build_validation_inventory",
    "current_reflection_contract",
)
