"""Immutable Task 10 governance inventory and relocation evidence.

The inventory is derived from an extracted Git archive of the immutable Task 10
start tree.  It therefore cannot be refreshed from a later candidate tree to
authorize deleted tests, weakened CLI seams, or newly hidden architecture debt.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from ._analysis import ROOT

ARCHITECTURE_REFERENCE_COMMIT = "a6a77deb7ae6c3bb0878895729e9a5a72cf75482"
ARCHITECTURE_REFERENCE_TREE = "cd3551ab769dece496b6599210a0dd14c9cd98ad"
ARCHITECTURE_INVENTORY_PATH = (
    ROOT / "artifacts" / "architecture_refactor" / "task10_governance_inventory.json"
)

GOVERNED_SCRIPTS = (
    "scripts/run_generalization_ablation.py",
    "scripts/run_risk_differential.py",
    "scripts/run_current_heads_competitor_matrix.py",
    "scripts/run_window_competitor_adapter.py",
    "scripts/analyze_risk_differential.py",
    "scripts/future_holdout.py",
    "scripts/production_observation.py",
    "scripts/run_five_window_outperformance.py",
    "scripts/run_risk_counterfactual.py",
    "scripts/backfill_tencent_history.py",
    "scripts/run_performance_diagnostic.py",
    "scripts/run_window_outperformance.py",
)

OVERSIZED_TEST_FILES = (
    "tests/test_lifecycle_and_risk.py",
    "tests/architecture/_analysis.py",
    "tests/test_attribution_identity.py",
    "tests/test_generalization_ablation.py",
    "tests/test_generalization_matrix.py",
    "tests/test_execution.py",
    "tests/test_future_holdout_runtime.py",
    "tests/test_generalization.py",
    "tests/test_recovery_contracts.py",
    "tests/architecture/_compatibility_baseline.py",
    "tests/test_engine_contracts.py",
    "tests/test_risk_transitions.py",
    "tests/test_engineering_gate_edges.py",
    "tests/architecture/test_risk_boundaries.py",
)

EXPECTED_DEBT_COUNTS = {
    "oversized_modules": 1,
    "long_functions": 50,
    "branchy_functions": 78,
    "cross_module_private_imports": 15,
    "mutable_module_globals": 77,
    "production_type_ignores": 6,
    "duplicate_private_helper_groups": 25,
    "internal_import_cycles": 0,
}

EXPECTED_PRODUCTION_OVER_800 = (
    "uquant/account/validation_orders.py",
    "uquant/attribution/validation.py",
    "uquant/portfolio/pipeline.py",
    "uquant/risk/assessment.py",
    "uquant/risk_sentinel/history.py",
    "uquant/validation/generalization_matrix.py",
    "uquant/validation/generalization_policy/evaluator.py",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _ast_sha256(node: ast.AST) -> str:
    return _sha256_bytes(ast.dump(node, include_attributes=False).encode("utf-8"))


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> tuple[str, ...]:
    targets: list[ast.expr]
    if isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        targets = list(node.targets)
    names: list[str] = []
    for target in targets:
        names.extend(child.id for child in ast.walk(target) if isinstance(child, ast.Name))
    return tuple(sorted(set(names)))


def _unit_name(node: ast.AST, ordinal: int) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        names = _assigned_names(node)
        return ",".join(names) if names else f"assignment-{ordinal}"
    return f"{type(node).__name__}-{ordinal}"


def semantic_units(source: str) -> list[dict[str, object]]:
    """Return exact non-import top-level semantic units and assertion evidence."""

    tree = ast.parse(source, type_comments=True)
    result: list[dict[str, object]] = []
    for ordinal, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            continue
        assertions = [
            _ast_sha256(child) for child in ast.walk(node) if isinstance(child, ast.Assert)
        ]
        decorators = (
            [_ast_sha256(item) for item in node.decorator_list]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            else []
        )
        result.append(
            {
                "ordinal": ordinal,
                "kind": type(node).__name__,
                "name": _unit_name(node, ordinal),
                "ast_sha256": _ast_sha256(node),
                "assertion_sha256": assertions,
                "decorator_sha256": decorators,
            }
        )
    return result


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ast.dump(node.args, include_attributes=False)


def public_seams(source: str) -> list[dict[str, object]]:
    """Freeze static public call signatures without importing production code."""

    tree = ast.parse(source, type_comments=True)
    seams: list[dict[str, object]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            seams.append(
                {
                    "qualname": node.name,
                    "signature_sha256": _sha256_bytes(
                        _function_signature(node).encode("utf-8")
                    ),
                }
            )
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            seams.append(
                {
                    "qualname": node.name,
                    "signature_sha256": canonical_sha256(
                        [ast.dump(base, include_attributes=False) for base in node.bases]
                    ),
                }
            )
            seams.extend(
                {
                    "qualname": f"{node.name}.{child.name}",
                    "signature_sha256": _sha256_bytes(
                        _function_signature(child).encode("utf-8")
                    ),
                }
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            )
    return sorted(seams, key=lambda row: str(row["qualname"]))


def _file_record(path: Path, root: Path) -> dict[str, object]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_bytes(raw),
        "bytes": len(raw),
        "lines": len(text.splitlines()),
        "semantic_units": semantic_units(text),
    }


def _normalise_cli_output(value: str, root: Path) -> str:
    return value.replace(str(root.resolve()), "<REPOSITORY_ROOT>").replace("\r\n", "\n")


def cli_help_seam(path: Path, root: Path) -> dict[str, object]:
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = "0"
    repository_path = str(root.resolve())
    inherited_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        os.pathsep.join((repository_path, inherited_path))
        if inherited_path
        else repository_path
    )
    completed = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    return {
        "returncode": completed.returncode,
        "stdout": _normalise_cli_output(completed.stdout, root),
        "stderr": _normalise_cli_output(completed.stderr, root),
    }


def _load_frozen_analysis(root: Path) -> tuple[dict[str, object], dict[str, list[dict[str, object]]]]:
    source_path = root / "tests" / "architecture" / "_analysis.py"
    namespace: dict[str, object] = {
        "__file__": str(source_path),
        "__name__": "task10_frozen_analysis",
    }
    exec(compile(source_path.read_bytes(), str(source_path), "exec"), namespace)
    snapshot_fn = namespace["architecture_snapshot"]
    measured_fn = namespace["measured_debt"]
    assert callable(snapshot_fn)
    assert callable(measured_fn)
    snapshot = snapshot_fn(root)
    debt = measured_fn(snapshot)
    assert isinstance(snapshot, dict)
    assert isinstance(debt, dict)
    return snapshot, debt


def build_inventory(root: Path, *, start_commit: str, start_tree: str) -> dict[str, object]:
    snapshot, debt = _load_frozen_analysis(root)
    counts = {category: len(rows) for category, rows in debt.items()}
    if counts != EXPECTED_DEBT_COUNTS:
        raise AssertionError(f"unexpected immutable Task 10 debt: {counts}")

    production_files = sorted((root / "uquant").rglob("*.py"))
    production_records = [_file_record(path, root) for path in production_files]
    for record in production_records:
        source = (root / str(record["path"])).read_text(encoding="utf-8")
        record["public_seams"] = public_seams(source)

    cli_records = []
    for relative in GOVERNED_SCRIPTS:
        record = _file_record(root / relative, root)
        record["help_seam"] = cli_help_seam(root / relative, root)
        cli_records.append(record)

    test_records = [_file_record(root / relative, root) for relative in OVERSIZED_TEST_FILES]
    payload: dict[str, object] = {
        "schema_version": 1,
        "inventory_id": "uquant-task10-governance-inventory-v1",
        "immutable_start": {"commit": start_commit, "tree": start_tree},
        "architecture_snapshot": snapshot,
        "architecture_debt": debt,
        "architecture_debt_counts": counts,
        "expected_production_over_800": list(EXPECTED_PRODUCTION_OVER_800),
        "governed_cli_scripts": cli_records,
        "oversized_test_files": test_records,
        "test_repository_unit_multiplicity": _serialised_unit_counts(
            (root / "tests").rglob("*.py")
        ),
        "cli_owned_layer_unit_multiplicity": _serialised_unit_counts(
            tuple((root / "scripts").rglob("*.py"))
            + tuple((root / "research").rglob("*.py"))
            + tuple((root / "uquant" / "validation").rglob("*.py"))
        ),
        "production_files": production_records,
        "requirements_sha256": _sha256_bytes((root / "requirements.txt").read_bytes()),
        "source_surface_registry_sha256": _sha256_bytes(
            (root / "benchmarks" / "source_surface_registry.json").read_bytes()
        ),
        "reproducibility": {
            "archive": f"git archive --format=tar {start_commit}",
            "generator": "python -m tests.architecture._governance_inventory",
            "final_zero_test": (
                "pytest -q tests/architecture/test_architecture_governance.py::"
                "test_task10_final_live_debt_matches_empty_acceptance_allowlist"
            ),
        },
    }
    payload["artifact_sha256"] = canonical_sha256(payload)
    return payload


def load_inventory(path: Path = ARCHITECTURE_INVENTORY_PATH) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("Task 10 inventory must be a JSON object")
    return value


def verify_inventory_seal(payload: Mapping[str, object]) -> None:
    unsigned = dict(payload)
    seal = unsigned.pop("artifact_sha256", None)
    if seal != canonical_sha256(unsigned):
        raise AssertionError("Task 10 inventory seal is stale")


def _safe_extract_archive(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        root = destination.resolve()
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise AssertionError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise AssertionError(f"Task 10 immutable archive contains link: {member.name}")
        bundle.extractall(destination, filter="data")


def build_inventory_from_immutable_git(root: Path = ROOT) -> dict[str, object]:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ARCHITECTURE_REFERENCE_COMMIT],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    with tempfile.TemporaryDirectory(prefix="uquant-task10-inventory-") as raw:
        extracted = Path(raw)
        _safe_extract_archive(archive, extracted)
        return build_inventory(
            extracted,
            start_commit=ARCHITECTURE_REFERENCE_COMMIT,
            start_tree=ARCHITECTURE_REFERENCE_TREE,
        )


def current_semantic_unit_counts(paths: Iterable[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        if path.is_file():
            for unit in semantic_units(path.read_text(encoding="utf-8")):
                counts[str(unit["ast_sha256"])] += 1
    return counts


def _serialised_unit_counts(paths: Iterable[Path]) -> dict[str, int]:
    return dict(sorted(current_semantic_unit_counts(paths).items()))


def _main() -> None:
    payload = build_inventory_from_immutable_git()
    ARCHITECTURE_INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHITECTURE_INVENTORY_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{ARCHITECTURE_INVENTORY_PATH}: {payload['artifact_sha256']}")


if __name__ == "__main__":
    _main()
