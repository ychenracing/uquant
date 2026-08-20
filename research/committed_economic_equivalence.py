"""Checkpointed exact economic comparison for two committed production trees."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Final

from uquant.validation.equivalence import (
    Phase1Case,
    _baseline_data_provenance,
    _git_commit,
    _immutable_equivalence_data,
    _isolated_equivalence_tree,
    _require_clean_equivalence_tree,
    phase1_cases,
    trace_phase1_case,
)

_SCHEMA: Final = "uquant.committed-economic-equivalence.v1"
_TRACE_FIELDS: Final = {
    "decision_payload_sha256",
    "economic_account_sha256",
}
_SIDES: Final = ("baseline", "candidate")


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_trace(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _TRACE_FIELDS
        and all(
            isinstance(item, str)
            and len(item) == 64
            and all(character in "0123456789abcdef" for character in item)
            for item in value.values()
        )
    )


def _checkpoint_identity(
    *,
    baseline_commit: str,
    candidate_commit: str,
    data: dict[str, Any],
    matrix_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "baseline_commit": baseline_commit,
        "candidate_commit": candidate_commit,
        "data": data,
        "matrix_sha256": matrix_sha256,
    }


def _load_checkpoint(
    path: Path,
    *,
    identity: dict[str, Any],
) -> dict[str, dict[str, dict[str, str]]]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("economic equivalence checkpoint is unreadable") from exc
    if not isinstance(payload, dict) or any(
        payload.get(name) != value for name, value in identity.items()
    ):
        raise RuntimeError("economic equivalence checkpoint identity differs")
    raw_cases = payload.get("case_traces")
    if not isinstance(raw_cases, dict):
        raise RuntimeError("economic equivalence checkpoint is malformed")
    cases: dict[str, dict[str, dict[str, str]]] = {}
    for name, raw_sides in raw_cases.items():
        if not isinstance(name, str) or not isinstance(raw_sides, dict):
            raise RuntimeError("economic equivalence checkpoint is malformed")
        sides: dict[str, dict[str, str]] = {}
        for side, trace in raw_sides.items():
            if side not in _SIDES or not _valid_trace(trace):
                raise RuntimeError("economic equivalence checkpoint is malformed")
            assert isinstance(trace, dict)
            sides[side] = {str(field): str(value) for field, value in trace.items()}
        cases[name] = sides
    return cases


def _write_checkpoint(
    path: Path,
    *,
    identity: dict[str, Any],
    case_traces: dict[str, dict[str, dict[str, str]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    payload = {
        **identity,
        "case_traces": {
            name: case_traces[name]
            for name in sorted(case_traces)
        },
    }
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_equivalent_case_traces(
    case_traces: dict[str, dict[str, dict[str, str]]],
) -> None:
    for name in sorted(case_traces):
        sides = case_traces[name]
        if set(sides) != set(_SIDES):
            raise RuntimeError(f"economic equivalence case is incomplete: {name}")
        baseline = sides["baseline"]
        candidate = sides["candidate"]
        if baseline["decision_payload_sha256"] != candidate["decision_payload_sha256"]:
            raise RuntimeError(f"decision payload diverged: {name}")
        if baseline["economic_account_sha256"] != candidate["economic_account_sha256"]:
            raise RuntimeError(f"economic account diverged: {name}")


def _report(
    *,
    identity: dict[str, Any],
    case_traces: dict[str, dict[str, dict[str, str]]],
) -> dict[str, Any]:
    _assert_equivalent_case_traces(case_traces)
    baseline = {
        name: case_traces[name]["baseline"]
        for name in sorted(case_traces)
    }
    candidate = {
        name: case_traces[name]["candidate"]
        for name in sorted(case_traces)
    }
    return {
        **identity,
        "cases": len(case_traces),
        "baseline_trace_sha256": _sha256_json(baseline),
        "candidate_trace_sha256": _sha256_json(candidate),
        "exact_dimensions": {
            "decision_digest": True,
            "risk_assessment_control": True,
            "target_portfolio": True,
            "orders": True,
            "fills": True,
            "account_state_economic_fields": True,
            "final_wealth": True,
            "max_drawdown": True,
            "trade_count": True,
        },
        "proof_contract": (
            "Each case hashes every canonical daily Decision payload and the final economic "
            "AccountState after removing code_hash only. Equal decisions, fills, order ledger, "
            "cash and positions on identical authenticated prices deterministically preserve "
            "wealth, drawdown and trade count."
        ),
        "passed": True,
    }


def _matrix_sha256(cases: tuple[Phase1Case, ...]) -> str:
    return _sha256_json([asdict(case) for case in cases])


def compare_committed_economics(
    *,
    baseline_root: str | Path,
    candidate_root: str | Path,
    data_dir: str | Path,
    checkpoint: str | Path,
    jobs: int = 2,
) -> dict[str, Any]:
    """Replay the full Phase 1 matrix with resumable exact A/B evidence."""
    if jobs < 1:
        raise ValueError("jobs must be positive")
    baseline_path = Path(baseline_root).resolve()
    candidate_path = Path(candidate_root).resolve()
    checkpoint_path = Path(checkpoint).resolve()
    baseline_commit = _git_commit(baseline_path)
    candidate_commit = _git_commit(candidate_path)
    _require_clean_equivalence_tree(baseline_path)
    _require_clean_equivalence_tree(candidate_path)

    with (
        _isolated_equivalence_tree(baseline_path, baseline_commit) as baseline_source,
        _isolated_equivalence_tree(candidate_path, candidate_commit) as candidate_source,
    ):
        data = _baseline_data_provenance(
            baseline_source / "benchmarks" / "promotion_baseline.json"
        )
        cases = phase1_cases(
            baseline_source / "benchmarks" / "promotion_baseline.json"
        )
        identity = _checkpoint_identity(
            baseline_commit=baseline_commit,
            candidate_commit=candidate_commit,
            data=data,
            matrix_sha256=_matrix_sha256(cases),
        )
        case_traces = _load_checkpoint(checkpoint_path, identity=identity)
        expected_names = {case.name for case in cases}
        if not set(case_traces).issubset(expected_names):
            raise RuntimeError("economic equivalence checkpoint contains an unknown case")

        with _immutable_equivalence_data(Path(data_dir), data) as stable_data:
            pending: dict[Future[Mapping[str, str]], tuple[str, str]] = {}
            with ThreadPoolExecutor(max_workers=jobs) as executor:
                for side, source in (
                    ("baseline", baseline_source),
                    ("candidate", candidate_source),
                ):
                    for case in cases:
                        if side in case_traces.get(case.name, {}):
                            continue
                        future = executor.submit(
                            trace_phase1_case,
                            root=source,
                            data_dir=stable_data,
                            case=case,
                        )
                        pending[future] = (side, case.name)

                completed = sum(len(sides) for sides in case_traces.values())
                total = len(cases) * len(_SIDES)
                if completed:
                    print(f"resuming {completed}/{total} completed traces", flush=True)
                for future in as_completed(pending):
                    side, name = pending[future]
                    trace = dict(future.result())
                    if not _valid_trace(trace):
                        raise RuntimeError(f"economic equivalence trace is malformed: {name}")
                    case_traces.setdefault(name, {})[side] = trace
                    completed += 1
                    _write_checkpoint(
                        checkpoint_path,
                        identity=identity,
                        case_traces=case_traces,
                    )
                    print(f"[{completed}/{total}] {side} {name}", flush=True)

    _require_clean_equivalence_tree(baseline_path)
    _require_clean_equivalence_tree(candidate_path)
    if _git_commit(baseline_path) != baseline_commit:
        raise RuntimeError("economic equivalence baseline commit changed during replay")
    if _git_commit(candidate_path) != candidate_commit:
        raise RuntimeError("economic equivalence candidate commit changed during replay")
    if set(case_traces) != {case.name for case in cases}:
        raise RuntimeError("economic equivalence matrix is incomplete")
    return _report(identity=identity, case_traces=case_traces)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-root", required=True)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    report = compare_committed_economics(
        baseline_root=args.baseline_root,
        candidate_root=args.candidate_root,
        data_dir=args.data_dir,
        checkpoint=args.checkpoint,
        jobs=args.jobs,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True), file=sys.stdout, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
