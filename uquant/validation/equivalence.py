"""Cross-commit proof that Phase 1 decisions and economic state are unchanged."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed Python command and JSON argument only
import sys
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .manifest import verify_data_manifest
from .universe import FROZEN_CHAMPION_COMMIT

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
from pathlib import Path

case = json.loads(sys.argv[1])
source_root = Path(case.pop("source_root")).resolve()
dependency_paths = case.pop("dependency_paths")
sys.path[:0] = [str(source_root), *dependency_paths]
if any(name == "uquant" or name.startswith("uquant.") for name in sys.modules):
    raise RuntimeError("Phase 1 trace imported production before source binding")

from uquant.config import config_fingerprint
from uquant.engine import ProductionEngine
from uquant.types import AccountState

for name, module in tuple(sys.modules.items()):
    if name == "uquant" or name.startswith("uquant."):
        origin = getattr(module, "__file__", None)
        if origin is None or not Path(origin).resolve().is_relative_to(source_root):
            raise RuntimeError("Phase 1 trace imported production outside its checkout")

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


def _sha256_equivalence_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _equivalence_git_executable() -> str:
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


def _require_clean_equivalence_tree(root: Path) -> None:
    """Reject every tracked or untracked byte in a mutable checkout."""

    try:
        status = subprocess.run(  # nosec B603
            [
                _git_executable(),
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot inspect Phase 1 equivalence tree: {root}") from exc
    if status.strip():
        raise RuntimeError("Phase 1 equivalence requires clean committed inputs")


def _reject_duplicate_equivalence_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise RuntimeError(f"Phase 1 baseline contains duplicate key: {key}")
        payload[key] = value
    return payload


def _baseline_data_provenance(path: Path) -> dict[str, Any]:
    """Read the exact frozen-data identity claimed by the frozen baseline."""

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except RuntimeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Phase 1 frozen baseline is unreadable") from exc
    provenance = payload.get("provenance") if isinstance(payload, Mapping) else None
    data = provenance.get("data") if isinstance(provenance, Mapping) else None
    if not isinstance(data, Mapping) or set(data) != {
        "snapshot_id",
        "files_verified",
        "manifest_sha256",
        "checksums_sha256",
    }:
        raise RuntimeError("Phase 1 frozen baseline data provenance is malformed")
    if (
        not isinstance(data["snapshot_id"], str)
        or not data["snapshot_id"]
        or isinstance(data["files_verified"], bool)
        or not isinstance(data["files_verified"], int)
        or data["files_verified"] <= 0
        or any(
            not isinstance(data[field], str) or not _SHA256.fullmatch(data[field])
            for field in ("manifest_sha256", "checksums_sha256")
        )
    ):
        raise RuntimeError("Phase 1 frozen baseline data provenance is malformed")
    return dict(data)


@contextmanager
def _immutable_equivalence_data(
    source: Path,
    expected: Mapping[str, Any],
) -> Iterator[Path]:
    """Replay every case from one private copy of the authenticated frozen data."""

    data_root = source.resolve()
    if source.is_symlink():
        raise RuntimeError("Phase 1 equivalence frozen data is unsafe")
    try:
        authenticated_files = (
            data_root / "DATA_MANIFEST.json",
            data_root / "SHA256SUMS",
            *sorted(data_root.glob("*.csv")),
        )
        observed = verify_data_manifest(data_root)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("Phase 1 equivalence frozen data is invalid") from exc
    if observed != dict(expected):
        raise RuntimeError("Phase 1 equivalence frozen data differs from the baseline")
    with tempfile.TemporaryDirectory(prefix="uquant-phase1-equivalence-data-") as temporary:
        snapshot = Path(temporary) / "data"
        try:
            snapshot.mkdir()
            for path in authenticated_files:
                shutil.copy2(path, snapshot / path.name, follow_symlinks=False)
            copied = verify_data_manifest(snapshot)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError("cannot snapshot Phase 1 equivalence frozen data") from exc
        if copied != dict(expected):
            raise RuntimeError("Phase 1 equivalence frozen data changed during snapshot")
        for path in snapshot.rglob("*"):
            path.chmod(0o500 if path.is_dir() else 0o400)
        snapshot.chmod(0o500)
        try:
            yield snapshot
        finally:
            try:
                private = verify_data_manifest(snapshot)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError("Phase 1 equivalence private data changed during replay") from exc
            if private != dict(expected):
                raise RuntimeError("Phase 1 equivalence private data changed during replay")
            try:
                current = verify_data_manifest(data_root)
            except (OSError, RuntimeError) as exc:
                raise RuntimeError("Phase 1 equivalence frozen data changed during replay") from exc
            if current != dict(expected):
                raise RuntimeError("Phase 1 equivalence frozen data changed during replay")


@contextmanager
def _isolated_equivalence_tree(root: Path, commit: str) -> Iterator[Path]:
    """Execute only from a private detached worktree at one captured commit."""

    git = _git_executable()
    with tempfile.TemporaryDirectory(prefix="uquant-phase1-equivalence-source-") as temporary:
        checkout = Path(temporary) / "checkout"
        primary: BaseException | None = None
        add_attempted = False
        try:
            try:
                add_attempted = True
                subprocess.run(  # nosec B603
                    [
                        git,
                        "-C",
                        str(root),
                        "worktree",
                        "add",
                        "--detach",
                        str(checkout),
                        commit,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise RuntimeError("cannot materialize Phase 1 equivalence source") from exc
            if _git_commit(checkout) != commit:
                raise RuntimeError("Phase 1 equivalence source commit differs")
            _require_clean_equivalence_tree(checkout)
            yield checkout
            _require_clean_equivalence_tree(checkout)
            if _git_commit(checkout) != commit:
                raise RuntimeError("Phase 1 equivalence source commit changed during replay")
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if add_attempted:
                try:
                    subprocess.run(  # nosec B603
                        [
                            git,
                            "-C",
                            str(root),
                            "worktree",
                            "remove",
                            "--force",
                            str(checkout),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except (OSError, subprocess.CalledProcessError) as exc:
                    if primary is not None:
                        primary.add_note(f"Phase 1 worktree cleanup also failed: {exc}")
                    else:
                        raise RuntimeError("cannot remove Phase 1 equivalence source") from exc


def _trusted_dependency_paths() -> tuple[str, ...]:
    """Expose installed dependencies without executing global site initialization."""

    paths: list[str] = []
    for raw in sys.path:
        if not raw:
            continue
        path = Path(raw).resolve()
        if path.is_dir() and any(part in {"site-packages", "dist-packages"} for part in path.parts):
            value = str(path)
            if value not in paths:
                paths.append(value)
    return tuple(paths)


def trace_phase1_case(
    *,
    root: str | Path,
    data_dir: str | Path,
    case: Phase1Case,
) -> Mapping[str, str]:
    """Replay one case in the selected tree and hash canonical decisions plus economic state."""
    source = Path(root).resolve()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    payload = {
        "source_root": str(source),
        "dependency_paths": list(_trusted_dependency_paths()),
        "data_dir": str(Path(data_dir).resolve()),
        "symbols": list(case.symbols),
        "start": case.start,
        "end": case.end,
    }
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                _TRACE_PROGRAM,
                json.dumps(payload, sort_keys=True),
            ],
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
    candidate_commit = _git_commit(candidate_path)
    _require_clean_equivalence_tree(frozen_path)
    _require_clean_equivalence_tree(candidate_path)
    with (
        _isolated_equivalence_tree(frozen_path, frozen_commit) as frozen_source,
        _isolated_equivalence_tree(candidate_path, candidate_commit) as candidate_source,
    ):
        data_provenance = _baseline_data_provenance(frozen_source / "benchmarks" / "promotion_baseline.json")
        with _immutable_equivalence_data(Path(data_dir), data_provenance) as stable_data:
            replay_cases = (
                phase1_cases(frozen_source / "benchmarks" / "promotion_baseline.json")
                if cases is None
                else cases
            )
            frozen_cases = {
                case.name: trace_phase1_case(
                    root=frozen_source,
                    data_dir=stable_data,
                    case=case,
                )
                for case in replay_cases
            }
            candidate_cases = {
                case.name: trace_phase1_case(
                    root=candidate_source,
                    data_dir=stable_data,
                    case=case,
                )
                for case in replay_cases
            }
    _require_clean_equivalence_tree(frozen_path)
    _require_clean_equivalence_tree(candidate_path)
    if _git_commit(frozen_path) != frozen_commit or _git_commit(candidate_path) != candidate_commit:
        raise RuntimeError("Phase 1 equivalence commit changed during replay")
    frozen_trace = Phase1DecisionTrace(production_commit=frozen_commit, cases=frozen_cases)
    candidate_trace = Phase1DecisionTrace(production_commit=candidate_commit, cases=candidate_cases)
    assert_equivalent_phase1_traces(frozen_trace, candidate_trace)
    return {
        "frozen_commit": frozen_commit,
        "candidate_commit": candidate_trace.production_commit,
        "cases": len(replay_cases),
        "frozen_trace_sha256": _sha256_json(frozen_cases),
        "candidate_trace_sha256": _sha256_json(candidate_cases),
        "data": data_provenance,
        "passed": True,
    }


_git_executable = _equivalence_git_executable
_reject_duplicate_keys = _reject_duplicate_equivalence_keys
_sha256_json = _sha256_equivalence_json

baseline_data_provenance = _baseline_data_provenance
git_commit = _git_commit
immutable_equivalence_data = _immutable_equivalence_data
isolated_equivalence_tree = _isolated_equivalence_tree
require_clean_equivalence_tree = _require_clean_equivalence_tree
