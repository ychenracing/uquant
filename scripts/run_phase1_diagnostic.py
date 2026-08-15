"""Replay an AI-era causal trace from an exact clean or reviewed-patch checkout."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import json
import platform
import shutil

# Security: Git and uv are invoked without a shell and with fixed executable names.
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research.first_divergence import (
    first_economic_divergence,
    trace_backtest,
)
from uquant.config import DEFAULT_CONFIG
from uquant.config_governance import DEFAULT_GOVERNANCE_PATH, GOVERNANCE_PATH
from uquant.engine import ProductionEngine
from uquant.reference_registry import DEFAULT_REGISTRY_PATH
from uquant.validation.manifest import verify_data_manifest

_PRODUCTION_FILES = (
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "benchmarks/reference_registry.json",
    GOVERNANCE_PATH.as_posix(),
)
_METRIC_FIELDS = (
    "final_wealth",
    "max_drawdown",
    "account_orders",
    "annual_turnover",
    "gross_turnover",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _require_ai_era_interval(start: str, end: str) -> tuple[str, str]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if start_date < date(2023, 1, 1):
        raise RuntimeError("diagnostic economics cannot start before 2023-01-01")
    if start_date > end_date:
        raise RuntimeError("diagnostic interval starts after it ends")
    return start_date.isoformat(), end_date.isoformat()


def _config_payload(config: Any) -> dict[str, Any]:
    payload = config.to_dict() if hasattr(config, "to_dict") else dataclasses.asdict(config)
    if not isinstance(payload, dict):
        raise RuntimeError("diagnostic config is not serializable")
    return payload


def _config_sha256(config: Any) -> str:
    return _sha256(_canonical_bytes(_config_payload(config)))


def _git(root: Path, arguments: Sequence[str]) -> bytes:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("cannot resolve git for diagnostic provenance")
    try:
        return subprocess.run(  # nosec B603
            [git, "-C", str(root), *arguments],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect diagnostic source checkout") from exc


def _source_paths(root: Path) -> tuple[Path, ...]:
    fixed = tuple(root / relative for relative in _PRODUCTION_FILES)
    paths = (*fixed, *((root / "uquant").rglob("*.py")))
    if any(not path.is_file() for path in paths):
        raise RuntimeError("diagnostic source checkout omits a production input")
    return tuple(sorted(paths, key=lambda path: path.relative_to(root).as_posix()))


def _source_sha256(root: Path) -> str:
    return _source_digest(root, _source_paths(root))


def _source_digest(root: Path, paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _assert_imported_source(root: Path) -> None:
    imported = (
        Path(inspect.getfile(ProductionEngine)).resolve(),
        Path(inspect.getfile(DEFAULT_CONFIG.__class__)).resolve(),
        Path(inspect.getfile(trace_backtest)).resolve(),
    )
    if any(not path.is_relative_to(root) for path in imported):
        raise RuntimeError("diagnostic imports do not belong to --source-root")
    expected_registry = (root / "benchmarks" / "reference_registry.json").resolve()
    if Path(DEFAULT_REGISTRY_PATH).resolve() != expected_registry:
        raise RuntimeError("diagnostic reference registry does not belong to --source-root")
    expected_governance = (root / GOVERNANCE_PATH).resolve()
    if Path(DEFAULT_GOVERNANCE_PATH).resolve() != expected_governance:
        raise RuntimeError("diagnostic config governance does not belong to --source-root")


def _trace_adapter_sha256(root: Path) -> str:
    adapter = root / "research" / "first_divergence.py"
    if not adapter.is_file():
        raise RuntimeError("diagnostic source omits its trace adapter")
    return _sha256(adapter.read_bytes())


def _source_provenance(
    root: Path,
    *,
    expected_patch_sha256: str | None,
    expected_commit: str,
    expected_source_sha256: str,
    expected_trace_adapter_sha256: str,
) -> dict[str, Any]:
    commit = _git(root, ("rev-parse", "HEAD")).decode().strip()
    status = _git(
        root,
        (
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "uquant",
            *_PRODUCTION_FILES,
        ),
    ).decode()
    patch = _git(root, ("diff", "HEAD", "--binary", "--", "uquant", *_PRODUCTION_FILES))
    patch_sha256 = _sha256(patch) if patch else None
    if any(line.startswith("??") for line in status.splitlines()):
        raise RuntimeError("diagnostic source contains untracked production inputs")
    if bool(status.strip()) != bool(expected_patch_sha256):
        raise RuntimeError("diagnostic source must be clean or carry one declared patch")
    if patch_sha256 != expected_patch_sha256:
        raise RuntimeError("diagnostic source patch differs from the declared mechanism")
    provenance = {
        "commit": commit,
        "source_sha256": _source_sha256(root),
        "trace_adapter_sha256": _trace_adapter_sha256(root),
        "uv_lock_sha256": _sha256((root / "uv.lock").read_bytes()),
        "patch_sha256": patch_sha256,
    }
    if commit != expected_commit:
        raise RuntimeError("diagnostic source commit differs from the declared commit")
    if provenance["source_sha256"] != expected_source_sha256:
        raise RuntimeError("diagnostic source differs from the declared source hash")
    if provenance["trace_adapter_sha256"] != expected_trace_adapter_sha256:
        raise RuntimeError("diagnostic trace adapter differs from the declared hash")
    return provenance


def _runner_provenance() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    runner_paths = (
        root / "scripts" / "run_phase1_diagnostic.py",
        root / "research" / "first_divergence.py",
        root / "uv.lock",
    )
    status = _git(
        root,
        (
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            "scripts/run_phase1_diagnostic.py",
            "research/first_divergence.py",
            "uv.lock",
        ),
    ).decode()
    if status.strip():
        raise RuntimeError("diagnostic runner and runtime lock must be committed")
    return {
        "commit": _git(root, ("rev-parse", "HEAD")).decode().strip(),
        "source_sha256": _source_digest(root, runner_paths),
        "uv_lock_sha256": _sha256((root / "uv.lock").read_bytes()),
    }


def _uv_version() -> str:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("cannot resolve uv for diagnostic provenance")
    try:
        output = subprocess.run(  # nosec B603
            [uv, "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot inspect diagnostic runtime") from exc
    parts = output.split()
    if len(parts) < 2 or parts[0] != "uv":
        raise RuntimeError("cannot inspect diagnostic runtime")
    return parts[1]


def _parse_changes(raw_changes: Sequence[str]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    fields = _config_payload(DEFAULT_CONFIG)
    for raw in raw_changes:
        name, separator, value = raw.partition("=")
        if not separator or name not in fields or name in changes:
            raise RuntimeError(f"invalid diagnostic config change: {raw}")
        try:
            changes[name] = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid diagnostic config value: {raw}") from exc
    return changes


def _run_trace(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.source_root).resolve()
    data_dir = Path(args.data_dir).resolve()
    _assert_imported_source(root)
    start, end = _require_ai_era_interval(args.start, args.end)
    changes = _parse_changes(args.set)
    config = DEFAULT_CONFIG.override(**changes)
    source = _source_provenance(
        root,
        expected_patch_sha256=args.expected_patch_sha256,
        expected_commit=args.expected_commit,
        expected_source_sha256=args.expected_source_sha256,
        expected_trace_adapter_sha256=args.expected_trace_adapter_sha256,
    )
    runner = _runner_provenance()
    manifest = verify_data_manifest(data_dir)
    result, trace = trace_backtest(
        ProductionEngine(data_dir, config),
        symbols=tuple(args.symbols.split(",")),
        start=start,
        end=end,
    )
    _assert_imported_source(root)
    if source != _source_provenance(
        root,
        expected_patch_sha256=args.expected_patch_sha256,
        expected_commit=args.expected_commit,
        expected_source_sha256=args.expected_source_sha256,
        expected_trace_adapter_sha256=args.expected_trace_adapter_sha256,
    ):
        raise RuntimeError("diagnostic source changed during replay")
    if runner != _runner_provenance():
        raise RuntimeError("diagnostic runner changed during replay")
    if manifest != verify_data_manifest(data_dir):
        raise RuntimeError("diagnostic data changed during replay")
    return {
        "schema_version": 1,
        "replay_exit_code": 0,
        "runner": runner,
        "source": source,
        "environment": {
            "python_full_version": platform.python_version(),
            "numpy_version": np.__version__,
            "pandas_version": pd.__version__,
            "uv_version": _uv_version(),
            "uv_lock_sha256": runner["uv_lock_sha256"],
        },
        "data": manifest,
        "interval": {"start": start, "end": end},
        "symbols": args.symbols.split(","),
        "config_changes": changes,
        "effective_config_sha256": _config_sha256(config),
        "metrics": {name: result[name] for name in _METRIC_FIELDS},
        "trace_sha256": _sha256(_canonical_bytes(trace)),
        "trace": trace,
    }


def _load_trace(path: str) -> Mapping[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("trace"), list):
        raise RuntimeError(f"invalid diagnostic trace: {path}")
    if payload.get("trace_sha256") != _sha256(_canonical_bytes(payload["trace"])):
        raise RuntimeError(f"diagnostic trace hash mismatch: {path}")
    return payload


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    from research.first_divergence import first_executable_divergence

    runner_root = Path(__file__).resolve().parents[1]
    comparator_path = Path(inspect.getfile(first_executable_divergence)).resolve()
    if not comparator_path.is_relative_to(runner_root):
        raise RuntimeError("diagnostic comparator does not belong to the runner checkout")
    _runner_provenance()
    left = _load_trace(args.left)
    right = _load_trace(args.right)
    for field in ("data", "environment", "interval", "symbols"):
        if left.get(field) != right.get(field):
            raise RuntimeError(f"diagnostic traces differ in {field}")
    if args.require_same_config and left.get("effective_config_sha256") != right.get(
        "effective_config_sha256"
    ):
        raise RuntimeError("diagnostic code ablation changed effective config")
    return {
        "schema_version": 1,
        "left_trace_sha256": left["trace_sha256"],
        "right_trace_sha256": right["trace_sha256"],
        "first_divergence": first_economic_divergence(left["trace"], right["trace"]),
        "first_executable_divergence": first_executable_divergence(
            left["trace"], right["trace"]
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    trace = subparsers.add_parser("trace")
    trace.add_argument("--source-root", default=".")
    trace.add_argument("--data-dir", required=True)
    trace.add_argument("--symbols", required=True)
    trace.add_argument("--start", required=True)
    trace.add_argument("--end", required=True)
    trace.add_argument("--set", action="append", default=[])
    trace.add_argument("--expected-patch-sha256")
    trace.add_argument("--expected-commit", required=True)
    trace.add_argument("--expected-source-sha256", required=True)
    trace.add_argument("--expected-trace-adapter-sha256", required=True)
    trace.add_argument("--output", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--left", required=True)
    compare.add_argument("--right", required=True)
    compare.add_argument("--require-same-config", action="store_true")
    compare.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = _run_trace(args) if args.command == "trace" else _compare(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical_bytes(payload) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
