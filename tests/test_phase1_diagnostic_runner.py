from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _load_diagnostic() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "run_phase1_diagnostic.py"
    spec = importlib.util.spec_from_file_location("run_phase1_diagnostic", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load diagnostic runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostic = _load_diagnostic()


def test_source_fingerprint_includes_config_parameter_governance(tmp_path: Path) -> None:
    for relative in diagnostic._PRODUCTION_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    (tmp_path / "uquant").mkdir()
    (tmp_path / "uquant" / "engine.py").write_text("engine", encoding="utf-8")
    governance = tmp_path / "benchmarks" / "config_parameter_governance.json"
    first = diagnostic._source_sha256(tmp_path)

    governance.write_text("changed-governance", encoding="utf-8")

    assert diagnostic._source_sha256(tmp_path) != first


def test_runner_provenance_binds_script_comparator_and_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(diagnostic.__file__).resolve().parents[1]
    observed_status: list[str] = []

    def git(_root: Path, arguments: tuple[str, ...]) -> bytes:
        assert _root == root
        if arguments[0] == "status":
            observed_status.extend(arguments)
            return b""
        assert arguments == ("rev-parse", "HEAD")
        return b"e" * 40 + b"\n"

    monkeypatch.setattr(diagnostic, "_git", git)

    provenance = diagnostic._runner_provenance()

    paths = (
        root / "scripts" / "run_phase1_diagnostic.py",
        root / "research" / "first_divergence.py",
        root / "uv.lock",
    )
    assert provenance == {
        "commit": "e" * 40,
        "source_sha256": diagnostic._source_digest(root, paths),
        "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
    }
    assert "scripts/run_phase1_diagnostic.py" in observed_status
    assert "research/first_divergence.py" in observed_status
    assert "uv.lock" in observed_status


def test_runner_provenance_rejects_dirty_comparator(monkeypatch: pytest.MonkeyPatch) -> None:
    def git(_root: Path, arguments: tuple[str, ...]) -> bytes:
        del _root
        if arguments[0] == "status":
            return b" M research/first_divergence.py\n"
        return b"e" * 40 + b"\n"

    monkeypatch.setattr(diagnostic, "_git", git)

    with pytest.raises(RuntimeError, match="runner and runtime lock must be committed"):
        diagnostic._runner_provenance()


def test_source_provenance_rejects_undeclared_trace_adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "uquant").mkdir()
    (tmp_path / "research").mkdir()
    (tmp_path / "benchmarks").mkdir()
    for relative in diagnostic._PRODUCTION_FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    (tmp_path / "uquant" / "engine.py").write_text("engine", encoding="utf-8")
    adapter = tmp_path / "research" / "first_divergence.py"
    adapter.write_text("trace-v1", encoding="utf-8")

    def git(_root: Path, arguments: tuple[str, ...]) -> bytes:
        del _root
        if arguments[0] == "rev-parse":
            return b"e" * 40 + b"\n"
        return b""

    monkeypatch.setattr(diagnostic, "_git", git)

    with pytest.raises(RuntimeError, match="trace adapter differs"):
        diagnostic._source_provenance(
            tmp_path,
            expected_patch_sha256=None,
            expected_commit="e" * 40,
            expected_source_sha256=diagnostic._source_sha256(tmp_path),
            expected_trace_adapter_sha256="0" * 64,
        )


def test_compare_rejects_tampered_trace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(diagnostic, "_runner_provenance", lambda: {})
    trace = tmp_path / "trace.json"
    trace.write_bytes(
        diagnostic._canonical_bytes(
            {
                "trace": [{"date": "2023-01-03"}],
                "trace_sha256": "0" * 64,
            }
        )
        + b"\n"
    )
    args: Any = type("Args", (), {"left": str(trace), "right": str(trace)})()

    with pytest.raises(RuntimeError, match="trace hash mismatch"):
        diagnostic._compare(args)


def test_compare_rejects_noncanonical_duplicate_trace_keys(tmp_path: Path) -> None:
    trace = [{"date": "2023-01-03"}]
    payload = {
        "trace": trace,
        "trace_sha256": diagnostic._sha256(diagnostic._canonical_bytes(trace)),
    }
    encoded = diagnostic._canonical_bytes(payload) + b"\n"
    path = tmp_path / "trace.json"
    path.write_bytes(encoded.replace(b'{"trace":', b'{"trace":[],"trace":', 1))

    with pytest.raises(RuntimeError, match="canonical"):
        diagnostic._load_trace(str(path))


def test_compare_does_not_treat_empty_trace_aliases_as_executable_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(diagnostic, "_runner_provenance", lambda: {})
    common = {
        "data": {"snapshot_id": "x"},
        "environment": {"python_full_version": "3.12.13"},
        "interval": {"start": "2023-01-03", "end": "2023-01-03"},
        "symbols": ["a"],
        "effective_config_sha256": "0" * 64,
    }
    left_trace = [{"date": "2023-01-03", "pending_orders": [], "new_fills": []}]
    right_trace = [{"date": "2023-01-03", "orders": [], "fills": []}]

    def write(name: str, trace: list[dict[str, Any]]) -> Path:
        path = tmp_path / name
        payload = {
            **common,
            "trace": trace,
            "trace_sha256": diagnostic._sha256(diagnostic._canonical_bytes(trace)),
        }
        path.write_bytes(diagnostic._canonical_bytes(payload) + b"\n")
        return path

    args: Any = type(
        "Args",
        (),
        {
            "left": str(write("left.json", left_trace)),
            "right": str(write("right.json", right_trace)),
            "require_same_config": True,
        },
    )()

    result = diagnostic._compare(args)

    assert result["first_executable_divergence"] is None
