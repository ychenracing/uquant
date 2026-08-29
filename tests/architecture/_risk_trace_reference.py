from __future__ import annotations

import ast
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from uquant.contracts.strict_json import canonical_json_sha256


def assert_trace_seals(
    payload: dict[str, object],
    *,
    baseline_commit: str,
    baseline_tree: str,
    account_fields: tuple[str, ...],
) -> None:
    assert payload["baseline_commit"] == baseline_commit
    assert payload["baseline_tree"] == baseline_tree
    assert payload["risk_account_fields"] == list(account_fields)
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    assert payload["payload_sha256"] == canonical_json_sha256(unsigned)
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    assert sum(int(scenario["record_count"]) for scenario in scenarios) == 60
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        assert scenario["record_count"] == len(scenario["records"])
        assert scenario["records_sha256"] == canonical_json_sha256(scenario["records"])


def _top_level_nodes(source: bytes) -> dict[str, ast.AST]:
    tree = ast.parse(source)
    nodes: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    nodes[target.id] = node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes[node.name] = node
    return nodes


def _assert_trace_logic_is_fixed(
    *,
    root: Path,
    runner: Path,
    runner_sha256: str,
    logic_blob: str,
) -> bytes:
    source = subprocess.run(
        [
            "git",
            "show",
            "105695aacd3d1c7e62705f64188da88d202db4cd:tests/architecture/_task7_risk_trace.py",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    assert hashlib.sha256(source).hexdigest() == runner_sha256
    immutable = subprocess.run(
        ["git", "cat-file", "blob", logic_blob],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    candidate_nodes = _top_level_nodes(runner.read_bytes())
    immutable_nodes = _top_level_nodes(immutable)
    fixed_nodes = (
        "_RISK_ACCOUNT_FIELDS",
        "_jsonable",
        "_account_projection",
        "_assessment_payload",
        "risk_trace_replay",
    )
    for name in fixed_nodes:
        assert ast.dump(candidate_nodes[name], include_attributes=False) == ast.dump(
            immutable_nodes[name], include_attributes=False
        )
    return source


def immutable_trace_from_archive(
    *,
    root: Path,
    destination: Path,
    baseline_commit: str,
    baseline_tree: str,
    risk_sha256: str,
    risk_size: int,
    runner: Path,
    runner_sha256: str,
    logic_blob: str,
) -> dict[str, object]:
    """Replay the oracle in a fresh process whose only uquant source is an archive."""

    runner_source = _assert_trace_logic_is_fixed(
        root=root,
        runner=runner,
        runner_sha256=runner_sha256,
        logic_blob=logic_blob,
    )
    archive = subprocess.run(
        ["git", "archive", "--format=tar", baseline_tree],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as snapshot:
        snapshot.extractall(destination, filter="data")
    assert not (destination / ".git").exists()
    immutable_risk = (destination / "uquant" / "risk.py").read_bytes()
    assert len(immutable_risk) == risk_size
    assert hashlib.sha256(immutable_risk).hexdigest() == risk_sha256
    assert (destination / "data" / "frozen" / "SHA256SUMS").is_file()

    injected_runner = destination / "_risk_trace_reference_runner.py"
    injected_runner.write_bytes(runner_source)
    # The archived risk owner used a BLAS dot kernel whose last bit depends on
    # the host CPU.  Replay that one-dimensional reduction with the same
    # explicitly fused arithmetic now used by production, without modifying
    # any source inside the immutable archive.
    launcher = "\n".join(
        (
            "import runpy, sys",
            "from fractions import Fraction",
            "import numpy",
            "native_dot = numpy.dot",
            "def deterministic_dot(left, right, *args, **kwargs):",
            "    if args or kwargs or numpy.ndim(left) != 1 or numpy.ndim(right) != 1:",
            "        return native_dot(left, right, *args, **kwargs)",
            "    if len(left) != len(right):",
            "        return native_dot(left, right, *args, **kwargs)",
            "    total = 0.0",
            "    for left_value, right_value in zip(left, right, strict=True):",
            (
                "        total = float(Fraction.from_float(total) "
                "+ Fraction.from_float(float(left_value)) "
                "* Fraction.from_float(float(right_value)))"
            ),
            "    return total",
            "numpy.dot = deterministic_dot",
            "snapshot, runner = sys.argv[1:]",
            "sys.path[:] = [snapshot] + [entry for entry in sys.path if '__editable__.uquant' not in entry]",
            "sys.meta_path[:] = [finder for finder in sys.meta_path "
            "if not finder.__class__.__module__.startswith('__editable___uquant_')]",
            "sys.argv[:] = [runner, snapshot]",
            "runpy.run_path(runner, run_name='__main__')",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", launcher, str(destination), str(injected_runner)],
        cwd=destination,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return payload


__all__ = ("assert_trace_seals", "immutable_trace_from_archive")
