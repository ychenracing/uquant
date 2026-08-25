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

PORTFOLIO_REFERENCE_COMMIT = "4b6bedb03fb7c58914d9d5032a2514c67f41f6ba"
PORTFOLIO_REFERENCE_TREE = "d3824f7c5d89521b8284b5de08cc1e82e3ab7ebd"

LEGACY_IMPLEMENTATIONS = (
    ("uquant/portfolio.py", "uquant.portfolio", "PortfolioAllocator"),
    ("uquant/portfolio_leaders.py", "uquant.portfolio_leaders", "LeaderPortfolioPolicy"),
    (
        "uquant/portfolio_strategic.py",
        "uquant.portfolio_strategic",
        "StrategicPortfolioPolicy",
    ),
    ("uquant/portfolio_recovery.py", "uquant.portfolio_recovery", "RecoveryPortfolioPolicy"),
)

_OWNER_MAPS: dict[str, dict[str, str]] = {
    "uquant/portfolio.py": {
        "PortfolioAllocator public/reflection/pickle facade and allocate": "uquant/portfolio/{__init__.py,allocator.py}",
        "_risk_attribution_mechanism/_risk_retention_score/_risk_retention_vector/_risk_lifecycle_rank/_subset_retention_vector/_sparse_risk_reduce/_risk_reduction_metadata/_turnover_aware_sector_cap": "uquant/portfolio/risk_reduction.py",
        "_commit_frozen_exit_state/_frozen_existing_targets": "uquant/portfolio/freeze.py",
        "_allocate_strategy fixed-order orchestration and owner handoff": "uquant/portfolio/pipeline.py",
        "_allocate_strategy recovery-admission/target continuous slices and _confirmed_recovery_gross": "uquant/portfolio/recovery/{admission.py,targets.py}",
        "same-call immutable inputs/carriers only": "uquant/portfolio/context.py",
    },
    "uquant/portfolio_leaders.py": {
        "LeaderPortfolioPolicy compatibility class": "uquant/portfolio_leaders.py",
        "_conviction_shares/_conviction_evidence_qualified/_correlations/_admission_utility/_dynamic_k": "uquant/portfolio/leaders/admission.py",
        "_session_clock/_session_distance/_rotation_allowed/_update_leader_cycle_arm/_retention_score/_leader_lifecycle_exit_confirmed/_industry_handoff": "uquant/portfolio/leaders/lifecycle.py",
        "_cap_opportunity_gross and _leader_targets construction slices": "uquant/portfolio/leaders/targets.py",
    },
    "uquant/portfolio_strategic.py": {
        "StrategicPortfolioPolicy compatibility class": "uquant/portfolio_strategic.py",
        "_initialize_strategic_cohort discovery/qualification continuous slices": "uquant/portfolio/strategic/discovery.py",
        "_bounded_strategic_restore_risk_open/_retire_strategic_member and _strategic_cohort_targets epoch/retirement/restore/exit slices": "uquant/portfolio/strategic/lifecycle.py",
        "_strategic_cohort_targets deterministic target-construction slices": "uquant/portfolio/strategic/targets.py",
    },
    "uquant/portfolio_recovery.py": {
        "RecoveryPortfolioPolicy compatibility class": "uquant/portfolio_recovery.py",
        "_recovery_anchor_substitution complete continuous method": "uquant/portfolio/recovery/substitution.py",
    },
}

_AUTHORITY_REASONS = {
    "uquant/portfolio.py": (
        "Sole production Target allocator and outer RiskAssessment.target_gross_cap, Sentinel "
        "FREEZE_ONLY, sparse-reduction, owner-handoff and final-target authority."
    ),
    "uquant/portfolio_leaders.py": (
        "Sole ordinary leader admission, dynamic-K, rotation, lifecycle, pyramid, satellite and "
        "leader-target policy implementation at the Task 8 boundary."
    ),
    "uquant/portfolio_strategic.py": (
        "Sole strategic discovery, qualification, epoch, restore, trailing, retirement and target "
        "policy implementation at the Task 8 boundary."
    ),
    "uquant/portfolio_recovery.py": (
        "Sole recovery-anchor substitution policy implementation at the Task 8 boundary."
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
    if text:
        assert isinstance(output, str)
    else:
        assert isinstance(output, bytes)
    return cast(bytes | str, output)


def _git_source(root: Path, path: str) -> bytes:
    value = _git(root, "show", f"{PORTFOLIO_REFERENCE_COMMIT}:{path}")
    assert isinstance(value, bytes)
    return value


def immutable_python_sources(root: Path) -> dict[str, bytes]:
    listing = _git(root, "ls-tree", "-r", "--name-only", PORTFOLIO_REFERENCE_COMMIT, text=True)
    assert isinstance(listing, str)
    paths = [path for path in listing.splitlines() if path.endswith(".py")]
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input="".join(f"{PORTFOLIO_REFERENCE_COMMIT}:{path}\n" for path in paths).encode(),
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
            (item for item in ast.walk(ast.parse(source)) if isinstance(item, (ast.Import, ast.ImportFrom))),
            key=lambda item: (item.lineno, item.col_offset),
        ):
            if isinstance(node, ast.Import):
                values = [f"{leaf} module" for alias in node.names if alias.name == target]
            else:
                imported_from = _resolved_import_from(path, node)
                values = [
                    alias.name if imported_from == target else f"{leaf} module"
                    for alias in node.names
                    if imported_from == target or (imported_from == parent and alias.name == leaf)
                ]
            for value in values:
                if value not in symbols:
                    symbols.append(value)
        if symbols:
            consumers.append({"path": path, "symbols": symbols})
    return consumers


def _module_attribute_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, Any]]:
    parent, _, leaf = target.rpartition(".")
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        tree = ast.parse(source)
        aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update(
                    alias.asname or alias.name.split(".")[-1] for alias in node.names if alias.name == target
                )
            elif isinstance(node, ast.ImportFrom) and _resolved_import_from(path, node) == parent:
                aliases.update(alias.asname or alias.name for alias in node.names if alias.name == leaf)
        attributes = sorted(
            {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in aliases
            }
        )
        attributes.extend(
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in aliases
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        )
        attributes = sorted(set(attributes))
        if attributes:
            consumers.append({"path": path, "attributes": attributes})
    return consumers


def _dotted_identity_consumers(sources: dict[str, bytes], target: str) -> list[dict[str, Any]]:
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


def _class_methods(source: bytes, class_name: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return tuple(
        node.name for node in class_node.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _method_consumers(sources: dict[str, bytes], method_names: set[str]) -> list[dict[str, Any]]:
    consumers: list[dict[str, Any]] = []
    for path, source in sources.items():
        methods = sorted(
            {
                node.attr
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Attribute) and node.attr in method_names
            }
        )
        if methods:
            consumers.append({"path": path, "methods": methods})
    return consumers


def _runtime_seams(sources: dict[str, bytes], method_names: set[str]) -> list[dict[str, Any]]:
    seams: list[dict[str, Any]] = []
    for path, source in sources.items():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in method_names
            ):
                seams.append(  # noqa: PERF401 - line identity is part of each seam row
                    {
                        "path": path,
                        "line": node.lineno,
                        "target": ast.unparse(node.args[0]),
                        "attribute": node.args[1].value,
                    }
                )
    return sorted(seams, key=lambda row: (str(row["path"]), int(row["line"])))


def _fixed_references(root: Path, path: str) -> list[str]:
    output = _git(
        root,
        "grep",
        "-l",
        "--fixed-strings",
        path,
        PORTFOLIO_REFERENCE_COMMIT,
        "--",
        ".",
        text=True,
    )
    assert isinstance(output, str)
    return sorted(line.split(":", 1)[1] for line in output.splitlines())


def _reference_classification(paths: list[str]) -> dict[str, list[str]]:
    current = sorted(
        path
        for path in paths
        if path
        in {
            "benchmarks/source_surface_registry.json",
            "research/ablation_registry.py",
            "scripts/run_risk_differential.py",
        }
    )
    historical = sorted(
        path
        for path in paths
        if path.startswith("artifacts/")
        or path
        in {"benchmarks/architecture_refactor_public_api.json", "benchmarks/risk_capability_registry.json"}
    )
    documentation = sorted(
        path for path in paths if path.startswith("docs/") or path.startswith(".superpowers/")
    )
    classified = set(current) | set(historical) | set(documentation)
    return {
        "current_executable_consumers": current,
        "historical_machine_evidence_to_preserve": historical,
        "documentation_references": documentation,
        "other_current_or_contract_consumers": sorted(set(paths) - classified),
    }


_REFLECTION_SCRIPT = r"""
import builtins, hashlib, inspect, json, pickle, sys
snapshot = sys.argv[1]
if sys.argv[2] == "block-fcntl":
    real_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("blocked fcntl")
        return real_import(name, *args, **kwargs)
    builtins.__import__ = guarded_import
sys.path[:] = [snapshot] + [entry for entry in sys.path if "__editable__.uquant" not in entry]
sys.meta_path[:] = [finder for finder in sys.meta_path if not finder.__class__.__module__.startswith("__editable___uquant_")]
from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator, current_weights, effective_n
from uquant.portfolio_core import PortfolioCore
from uquant.portfolio_leaders import LeaderPortfolioPolicy
from uquant.portfolio_recovery import RecoveryPortfolioPolicy
from uquant.portfolio_strategic import StrategicPortfolioPolicy
classes = (PortfolioAllocator, LeaderPortfolioPolicy, StrategicPortfolioPolicy, RecoveryPortfolioPolicy)
method_names = sorted({name for cls in classes for name, value in cls.__dict__.items() if inspect.isfunction(value) or isinstance(value, (staticmethod, classmethod))})
payload = {"portfolio_all": list(__import__("uquant.portfolio", fromlist=["__all__"]).__all__), "classes": {}, "functions": {}}
for cls in classes:
    instance = cls(DEFAULT_CONFIG)
    instance_pickle = pickle.dumps(instance, protocol=5)
    class_pickle = pickle.dumps(cls, protocol=5)
    roundtrip = pickle.loads(instance_pickle)
    methods = {}
    for name, descriptor in cls.__dict__.items():
        if isinstance(descriptor, staticmethod):
            kind, function = "staticmethod", descriptor.__func__
        elif isinstance(descriptor, classmethod):
            kind, function = "classmethod", descriptor.__func__
        elif inspect.isfunction(descriptor):
            kind, function = "instance", descriptor
        else:
            continue
        methods[name] = {
            "descriptor": kind,
            "signature": str(inspect.signature(function)),
            "raw_docstring": function.__doc__,
            "module": function.__module__,
            "qualname": function.__qualname__,
        }
    lookup = {}
    for name in method_names:
        owner = next((base for base in cls.__mro__ if name in base.__dict__), None)
        if owner is not None:
            lookup[name] = f"{owner.__module__}.{owner.__qualname__}"
    payload["classes"][cls.__name__] = {
        "signature": str(inspect.signature(cls)),
        "module": cls.__module__,
        "qualname": cls.__qualname__,
        "raw_docstring": cls.__doc__,
        "mro": [f"{base.__module__}.{base.__qualname__}" for base in cls.__mro__],
        "direct_base": f"{cls.__base__.__module__}.{cls.__base__.__qualname__}",
        "methods": methods,
        "inherited_method_lookup": lookup,
        "class_pickle_sha256": hashlib.sha256(class_pickle).hexdigest(),
        "class_pickle_size": len(class_pickle),
        "instance_pickle_sha256": hashlib.sha256(instance_pickle).hexdigest(),
        "instance_pickle_size": len(instance_pickle),
        "roundtrip_class": f"{type(roundtrip).__module__}.{type(roundtrip).__qualname__}",
        "roundtrip_isinstance": isinstance(roundtrip, cls),
        "roundtrip_config_equal": roundtrip.cfg == DEFAULT_CONFIG,
    }
for function in (current_weights, effective_n):
    payload["functions"][function.__name__] = {
        "signature": str(inspect.signature(function)),
        "module": function.__module__,
        "qualname": function.__qualname__,
        "raw_docstring": function.__doc__,
    }
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
    normal = observed["normal"]
    assert isinstance(normal, dict)
    return {
        "normal": normal,
        "mode_sha256": {name: canonical_json_sha256(value) for name, value in observed.items()},
    }


def current_reflection_contract(root: Path) -> dict[str, Any]:
    """Observe candidate public/reflection/pickle/import behavior in fresh processes."""

    return _reflection_from_snapshot(root.resolve())


def _reflection_contract(root: Path) -> dict[str, Any]:
    archive = _git(root, "archive", "--format=tar", PORTFOLIO_REFERENCE_COMMIT)
    assert isinstance(archive, bytes)
    with tempfile.TemporaryDirectory(prefix="uquant-task8-inventory-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        snapshot.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            stream.extractall(snapshot, filter="data")
        return _reflection_from_snapshot(snapshot)


def build_portfolio_inventory(root: Path) -> dict[str, Any]:
    sources = immutable_python_sources(root)
    registry = json.loads(_git_source(root, "benchmarks/source_surface_registry.json"))
    public_api = json.loads(_git_source(root, "benchmarks/architecture_refactor_public_api.json"))
    reflection = _reflection_contract(root)
    all_method_names = {
        name
        for path, _, class_name in LEGACY_IMPLEMENTATIONS
        for name in _class_methods(_git_source(root, path), class_name)
        if name.startswith("_")
    }
    method_consumers = _method_consumers(sources, all_method_names)
    runtime_seams = _runtime_seams(sources, all_method_names)
    entries: list[dict[str, Any]] = []
    for path, module, class_name in LEGACY_IMPLEMENTATIONS:
        source = _git_source(root, path)
        blob = _git(root, "rev-parse", f"{PORTFOLIO_REFERENCE_COMMIT}:{path}", text=True)
        assert isinstance(blob, str)
        references = _fixed_references(root, path)
        memberships = [surface["id"] for surface in registry["surfaces"] if path in surface["source_paths"]]
        module_public = public_api["contract"]["modules"][module]
        classification = _reference_classification(references)
        entries.append(
            {
                "path": path,
                "module": module,
                "class_name": class_name,
                "classification": (
                    "CONSOLIDATE_THEN_DELETE"
                    if path == "uquant/portfolio.py"
                    else "CONSOLIDATE_THEN_RETAIN_FACADE"
                ),
                "disposition": (
                    "REPLACE_WITH_SAME_NAME_PACKAGE_AFTER_EXACT_PARITY"
                    if path == "uquant/portfolio.py"
                    else "RETAIN_THIN_COMPATIBILITY_FACADE_AFTER_EXACT_PARITY"
                ),
                "authority_reason": _AUTHORITY_REASONS[path],
                "git_blob_sha1": blob.strip(),
                "content_sha256": hashlib.sha256(source).hexdigest(),
                "size_bytes": len(source),
                "restore_commands": [
                    f"git cat-file blob {blob.strip()}",
                    f"git restore --source={PORTFOLIO_REFERENCE_COMMIT} --staged --worktree -- {path}",
                ],
                "symbol_owner_mapping": _OWNER_MAPS[path],
                "source_surface_memberships": memberships,
                "public_api_contract": module_public,
                "reflection_pickle_mro_contract": reflection["normal"]["classes"][class_name],
                "defined_methods": list(_class_methods(source, class_name)),
                "live_references": {
                    "immutable_fixed_path_consumers": references,
                    "ast_import_consumers": _import_consumers(sources, module),
                    "runtime_module_attribute_consumers": _module_attribute_consumers(sources, module),
                    "dotted_runtime_identity_consumers": _dotted_identity_consumers(sources, module),
                    "consumed_private_method_attributes": [
                        {
                            "path": row["path"],
                            "methods": sorted(set(row["methods"]) & set(_class_methods(source, class_name))),
                        }
                        for row in method_consumers
                        if set(row["methods"]) & set(_class_methods(source, class_name))
                    ],
                    "runtime_monkeypatch_seams": [
                        row for row in runtime_seams if row["attribute"] in _class_methods(source, class_name)
                    ],
                    **classification,
                },
            }
        )
    payload: dict[str, Any] = {
        "baseline_commit": PORTFOLIO_REFERENCE_COMMIT,
        "baseline_tree": PORTFOLIO_REFERENCE_TREE,
        "contract": "uquant-task8-pre-replacement-portfolio-inventory-v1",
        "derivation": {
            "authority": (
                "Only Git objects reachable from immutable Task 7 commit 4b6bedb03fb7c58914d9d5032a2514c67f41f6ba "
                "and fresh imports from its git archive are authority inputs."
            ),
            "checks": [
                "immutable git blob SHA-1/content SHA-256/byte size and exact restore commands",
                "git grep fixed-path references with executable/historical/documentation classification",
                "AST absolute/relative/package import, module attribute, dotted identity, private method and monkeypatch consumers",
                "immutable generation-2 source-surface memberships",
                "Task 1 public API plus fresh -I reflection/MRO/descriptor/pickle round trips",
            ],
            "preservation_rulings": {
                "historical_artifacts_and_patches": "KEEP_BYTE_IDENTICAL_AND_VERIFY_AT_RECORDED_SOURCE; never rewrite old path vocabulary as current package authority",
                "research/ablation_registry.py and scripts/run_risk_differential.py": "KEEP_EXECUTABLE_HISTORICAL_VOCABULARY; their sealed patch/capability carriers resolve recorded commits rather than candidate paths",
                "benchmarks/architecture_refactor_public_api.json": "KEEP_AUTHORITATIVE; prove exact same-name facade/class relocation without rewriting the frozen baseline",
                "benchmarks/source_surface_registry.json": "MIGRATE_CURRENT_EXECUTABLE_IDENTITY only for reviewed economic_decision_v1/full_package_v1 owners and retained facades",
                "requirements.txt": "KEEP_BYTE_IDENTICAL",
            },
        },
        "captured_while_all_implementations_intact": [path for path, _, _ in LEGACY_IMPLEMENTATIONS],
        "portfolio_public_contract": {
            "module": public_api["contract"]["modules"]["uquant.portfolio"],
            "runtime": {
                "all": reflection["normal"]["portfolio_all"],
                "functions": reflection["normal"]["functions"],
                "import_mode_sha256": reflection["mode_sha256"],
            },
        },
        "entries": entries,
    }
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    return payload


__all__ = (
    "LEGACY_IMPLEMENTATIONS",
    "TASK8_START",
    "TASK8_START_TREE",
    "build_portfolio_inventory",
    "current_reflection_contract",
)
