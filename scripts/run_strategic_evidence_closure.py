"""Resumable, orchestrator for strategic evidence closure research."""

from __future__ import annotations

import argparse
import json

# Only fixed internal module argv is executed.
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from research.strategic_evidence.report import (
    assemble_evidence_artifacts,
    validate_evidence_artifacts,
)

_ARTIFACT_DIR = Path("artifacts/strategic_evidence_closure")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_commands(
    *,
    root: Path,
    runtime_dir: Path,
    resume: bool,
) -> dict[str, tuple[str, ...]]:
    """Return exact full-matrix commands; no sentinel or reduced path is emitted."""

    repository = root.resolve()
    runtime = runtime_dir.resolve()
    python = sys.executable
    forced_owner = (
        python,
        "-m",
        "research.strategic_evidence.forced_owner_runner",
        "run",
        "--root",
        str(repository),
        "--summary",
        str(repository / _ARTIFACT_DIR / "forced_owner_full.json"),
        "--manifest",
        str(repository / _ARTIFACT_DIR / "forced_owner_manifest.json"),
        "--trace-shard",
        str(runtime / "forced_owner_full_routes.jsonl.gz"),
        "--resume-dir",
        str(runtime / "forced_owner-resume"),
    )
    witness_ablation = (
        python,
        "-m",
        "research.strategic_evidence.witness_ablation_runner",
        "run",
        "--root",
        str(repository),
        "--summary",
        str(repository / _ARTIFACT_DIR / "witness_ablation_full.json"),
        "--manifest",
        str(repository / _ARTIFACT_DIR / "witness_ablation_manifest.json"),
        "--trace-shard",
        str(runtime / "witness_ablation_full_routes.jsonl.gz"),
        "--resume-dir",
        str(runtime / "witness_ablation-resume"),
    )
    reachability = (
        python,
        "-m",
        "research.strategic_evidence.reachability_runner",
        "--repository-root",
        str(repository),
        "--output",
        str(runtime / "state_reachability_84.jsonl.gz"),
        "--summary",
        str(repository / _ARTIFACT_DIR / "state_reachability_summary.json"),
        "--session-count",
        "80",
    )
    suffix = ("--resume",) if resume else ()
    return {
        "forced-owner": (*forced_owner, *suffix),
        "witness-ablation": (*witness_ablation, *suffix),
        "reachability": (*reachability, *suffix),
    }


def _source_paths(root: Path) -> dict[str, Path]:
    artifact_dir = root.resolve() / _ARTIFACT_DIR
    return {
        "task3": artifact_dir / "forced_owner_full.json",
        "task4": artifact_dir / "witness_ablation_full.json",
        "task5": artifact_dir / "state_reachability_summary.json",
    }


def _external_paths(runtime: Path) -> dict[str, Path]:
    return {
        "task3": runtime / "forced_owner_full_routes.jsonl.gz",
        "task4": runtime / "witness_ablation_full_routes.jsonl.gz",
        "task5": runtime / "state_reachability_84.jsonl.gz",
    }


def _print(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True), flush=True)


def _assemble(
    *,
    root: Path,
    runtime: Path,
    dry_run: bool,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    external = _external_paths(runtime)
    return assemble_evidence_artifacts(
        root=root,
        output_dir=root / _ARTIFACT_DIR if output_dir is None else output_dir,
        source_paths=_source_paths(root),
        forced_owner_shard=external["task3"] if external["task3"].is_file() else None,
        witness_ablation_shard=external["task4"] if external["task4"].is_file() else None,
        reachability_shard=external["task5"] if external["task5"].is_file() else None,
        dry_run=dry_run,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repository_root())
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "uquant-strategic-evidence",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="execute one resumable full-matrix step or all steps")
    run.add_argument(
        "--step",
        choices=("forced-owner", "witness-ablation", "reachability", "assemble", "all"),
        required=True,
    )
    run.add_argument("--resume", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("assemble", help="assemble policy, report, manifest, and checksums")
    subparsers.add_parser("validate", help="validate compact artifacts and available external bytes")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    if args.command == "assemble":
        _print(_assemble(root=root, runtime=runtime, dry_run=False))
        return 0
    if args.command == "validate":
        supplied = {task: path for task, path in _external_paths(runtime).items() if path.is_file()}
        _print(
            validate_evidence_artifacts(
                root / _ARTIFACT_DIR,
                root=root,
                external_paths=supplied,
            )
        )
        return 0

    step = str(args.step)
    commands = build_commands(root=root, runtime_dir=runtime, resume=bool(args.resume))
    selected = (
        ("forced-owner", "witness-ablation", "reachability")
        if step == "all"
        else (() if step == "assemble" else (step,))
    )
    if args.dry_run:
        if step == "assemble":
            with tempfile.TemporaryDirectory(prefix="assembly-dry-run-", dir=runtime) as temporary:
                _print(
                    _assemble(
                        root=root,
                        runtime=runtime,
                        dry_run=True,
                        output_dir=Path(temporary),
                    )
                )
            return 0
        _print(
            {
                "dry_run": True,
                "steps": list(selected),
                "commands": {name: list(commands[name]) for name in selected},
                "assemble": step in {"assemble", "all"},
            }
        )
        return 0
    for name in selected:
        # argv is built internally from fixed module entrypoints and parsed paths.
        subprocess.run(commands[name], cwd=root, check=True)  # nosec B603
    result = (
        _assemble(root=root, runtime=runtime, dry_run=False)
        if step in {"assemble", "all"}
        else {
            "runner_success": True,
            "completed_steps": list(selected),
            "capability_evaluated": False,
        }
    )
    _print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_commands", "main", "repository_root")
