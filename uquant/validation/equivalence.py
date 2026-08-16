"""Cross-commit proof that Phase 1 decisions and economic state are unchanged."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess  # nosec B404 - fixed Python command and JSON argument only
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .universe import FROZEN_CHAMPION_COMMIT


@dataclass(frozen=True, slots=True)
class Phase1DecisionTrace:
    """Content hashes for canonical decisions and economic account state by case."""

    production_commit: str
    cases: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True, slots=True)
class Phase1Case:
    """One mandatory Phase 1 replay interval and its exact reviewed pool."""

    name: str
    symbols: tuple[str, ...]
    start: str
    end: str


def phase1_cases(
    baseline: str | Path = Path("benchmarks") / "promotion_baseline.json",
) -> tuple[Phase1Case, ...]:
    """Expand every reviewed official and protected Phase 1 pool/interval exactly once."""
    payload = json.loads(Path(baseline).read_text(encoding="utf-8"))
    pools = payload.get("pools")
    contract = payload.get("contract")
    if not isinstance(pools, dict) or not isinstance(contract, dict):
        raise RuntimeError("Phase 1 promotion baseline is malformed")
    intervals = {
        **contract.get("windows", {}),
        **contract.get("protected_intervals", {}),
    }
    if set(pools) != {"a", "b", "c", "d", "e"} or len(intervals) != 9:
        raise RuntimeError("Phase 1 promotion baseline does not define the complete replay matrix")
    cases: list[Phase1Case] = []
    for pool in sorted(pools):
        symbols = pools[pool]
        if not isinstance(symbols, list) or not all(isinstance(symbol, str) for symbol in symbols):
            raise RuntimeError(f"Phase 1 pool is malformed: {pool}")
        for interval in sorted(intervals):
            window = intervals[interval]
            if not isinstance(window, dict) or set(window) != {"start", "end"}:
                raise RuntimeError(f"Phase 1 interval is malformed: {interval}")
            cases.append(
                Phase1Case(
                    name=f"{pool}/{interval}",
                    symbols=tuple(symbols),
                    start=str(window["start"]),
                    end=str(window["end"]),
                )
            )
    return tuple(cases)


def assert_equivalent_phase1_traces(
    frozen: Phase1DecisionTrace,
    candidate: Phase1DecisionTrace,
) -> None:
    """Reject a candidate if any Phase 1 decision payload or economic state differs."""
    if frozen.production_commit != FROZEN_CHAMPION_COMMIT:
        raise RuntimeError("reference trace is not bound to the frozen Phase 1 champion")
    if set(frozen.cases) != set(candidate.cases):
        raise RuntimeError("Phase 1 trace cases differ across commits")
    required: Final = {"decision_payload_sha256", "economic_account_sha256"}
    for case in sorted(frozen.cases):
        reference = frozen.cases[case]
        observed = candidate.cases[case]
        if set(reference) != required or set(observed) != required:
            raise RuntimeError(f"Phase 1 trace payload is malformed: {case}")
        if reference["decision_payload_sha256"] != observed["decision_payload_sha256"]:
            raise RuntimeError(f"Phase 1 decision payload diverged: {case}")
        if reference["economic_account_sha256"] != observed["economic_account_sha256"]:
            raise RuntimeError(f"Phase 1 economic account diverged: {case}")


_TRACE_PROGRAM: Final = """
import hashlib
import json
import sys

from uquant.config import config_fingerprint
from uquant.engine import ProductionEngine
from uquant.types import AccountState

case = json.loads(sys.argv[1])
engine = ProductionEngine(case["data_dir"])
payloads = []
original_decide = engine.decide

def capture_decision(**kwargs):
    decision = original_decide(**kwargs)
    payloads.append(decision.canonical_payload(effective_config_sha256=config_fingerprint(engine.cfg)))
    return decision

engine.decide = capture_decision
result = engine.backtest(
    symbols=case["symbols"],
    start=case["start"],
    end=case["end"],
    initial_cash=AccountState.empty(engine.cfg.initial_cash).initial_cash,
)
economic_account = dict(result["final_account"])
economic_account.pop("code_hash", None)
canonical = lambda value: json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
print(json.dumps({
    "decision_payload_sha256": hashlib.sha256(canonical(payloads)).hexdigest(),
    "economic_account_sha256": hashlib.sha256(canonical(economic_account)).hexdigest(),
}, separators=(",", ":"), sort_keys=True))
"""


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("cannot resolve git executable for Phase 1 equivalence")
    return executable


def _git_commit(root: Path) -> str:
    try:
        return subprocess.run(
            [_git_executable(), "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()  # nosec B603
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot resolve commit for Phase 1 equivalence: {root}") from exc


def trace_phase1_case(
    *,
    root: str | Path,
    data_dir: str | Path,
    case: Phase1Case,
) -> Mapping[str, str]:
    """Replay one case in the selected tree and hash canonical decisions plus economic state."""
    source = Path(root).resolve()
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source)
    payload = {
        "data_dir": str(Path(data_dir).resolve()),
        "symbols": list(case.symbols),
        "start": case.start,
        "end": case.end,
    }
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _TRACE_PROGRAM, json.dumps(payload, sort_keys=True)],
            cwd=source,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )  # nosec B603
        trace = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot capture Phase 1 decision trace: {case.name}") from exc
    if not isinstance(trace, dict) or set(trace) != {"decision_payload_sha256", "economic_account_sha256"}:
        raise RuntimeError(f"Phase 1 decision trace is malformed: {case.name}")
    if any(not isinstance(value, str) or len(value) != 64 for value in trace.values()):
        raise RuntimeError(f"Phase 1 decision trace digest is malformed: {case.name}")
    return trace


def compare_phase1_commits(
    *,
    frozen_root: str | Path,
    candidate_root: str | Path,
    data_dir: str | Path,
    cases: tuple[Phase1Case, ...] | None = None,
) -> dict[str, Any]:
    """Compare every required replay from the frozen commit to the candidate tree."""
    frozen_path = Path(frozen_root).resolve()
    candidate_path = Path(candidate_root).resolve()
    frozen_commit = _git_commit(frozen_path)
    if frozen_commit != FROZEN_CHAMPION_COMMIT:
        raise RuntimeError("frozen equivalence tree does not match the Phase 1 champion commit")
    replay_cases = phase1_cases(frozen_path / "benchmarks" / "promotion_baseline.json") if cases is None else cases
    frozen_cases = {
        case.name: trace_phase1_case(root=frozen_path, data_dir=data_dir, case=case) for case in replay_cases
    }
    candidate_cases = {
        case.name: trace_phase1_case(root=candidate_path, data_dir=data_dir, case=case) for case in replay_cases
    }
    frozen_trace = Phase1DecisionTrace(production_commit=frozen_commit, cases=frozen_cases)
    candidate_trace = Phase1DecisionTrace(production_commit=_git_commit(candidate_path), cases=candidate_cases)
    assert_equivalent_phase1_traces(frozen_trace, candidate_trace)
    return {
        "frozen_commit": frozen_commit,
        "candidate_commit": candidate_trace.production_commit,
        "cases": len(replay_cases),
        "frozen_trace_sha256": _sha256_json(frozen_cases),
        "candidate_trace_sha256": _sha256_json(candidate_cases),
        "passed": True,
    }
