from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from uquant.validation import equivalence
from uquant.validation.equivalence import (
    FROZEN_CHAMPION_COMMIT,
    Phase1DecisionTrace,
    assert_equivalent_phase1_traces,
    phase1_cases,
)


def _trace(*, decision: str = "decision", account: str = "account") -> Phase1DecisionTrace:
    return Phase1DecisionTrace(
        production_commit=FROZEN_CHAMPION_COMMIT,
        cases={
            "a/h1_2023": {
                "decision_payload_sha256": decision,
                "economic_account_sha256": account,
            }
        },
    )


@contextmanager
def _passthrough_tree(root: Path, _commit: str) -> Iterator[Path]:
    yield root


@contextmanager
def _passthrough_data(source: Path, _expected: object) -> Iterator[Path]:
    yield source


def _frozen_data_fixture(root: Path) -> dict[str, object]:
    csv = root / "sz000001.csv"
    root.mkdir(parents=True)
    csv.write_text("date,open,high,low,close,volume\n2023-01-03,1,1,1,1,1\n", encoding="utf-8")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    (root / "SHA256SUMS").write_text(f"{digest}  {csv.name}\n", encoding="utf-8")
    (root / "DATA_MANIFEST.json").write_text(
        json.dumps(
            {
                "snapshot_id": "fixture",
                "results": [{"symbol": "sz000001", "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )
    return equivalence.verify_data_manifest(root)


def test_performance_equivalence_rejects_any_cross_commit_decision_or_account_divergence() -> None:
    """Breaks if a Phase 2 candidate changes a frozen Phase 1 economic trace."""
    assert_equivalent_phase1_traces(_trace(), _trace())

    with pytest.raises(RuntimeError, match="decision payload"):
        assert_equivalent_phase1_traces(_trace(), _trace(decision="changed"))
    with pytest.raises(RuntimeError, match="economic account"):
        assert_equivalent_phase1_traces(_trace(), _trace(account="changed"))


def test_performance_equivalence_covers_every_official_and_protected_pool_case() -> None:
    """Breaks if the differential proof silently omits a Phase 1 replay case."""
    cases = phase1_cases()

    assert len(cases) == 45
    assert len({case.name for case in cases}) == len(cases)
    assert {case.name.rsplit("/", 1)[1] for case in cases} == {
        "h1_2023",
        "h2_2023",
        "h1_2024",
        "h2_2024",
        "bull_crash_2025_2026",
        "continuous_ai_era",
        "year_2023",
        "year_2024",
        "bull",
    }


def test_cross_commit_matrix_ignores_a_candidate_baseline_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Breaks if candidate-controlled baseline edits can omit a frozen replay case."""
    frozen = tmp_path / "frozen"
    candidate = tmp_path / "candidate"
    frozen_benchmark = frozen / "benchmarks"
    candidate_benchmark = candidate / "benchmarks"
    frozen_benchmark.mkdir(parents=True)
    candidate_benchmark.mkdir(parents=True)
    source = Path("benchmarks") / "promotion_baseline.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    (frozen_benchmark / "promotion_baseline.json").write_text(json.dumps(payload), encoding="utf-8")
    payload["pools"].pop("e")
    (candidate_benchmark / "promotion_baseline.json").write_text(json.dumps(payload), encoding="utf-8")
    captured: list[str] = []

    monkeypatch.setattr(equivalence, "_git_commit", lambda root: FROZEN_CHAMPION_COMMIT)
    monkeypatch.setattr(equivalence, "_require_clean_equivalence_tree", lambda _root: None)
    monkeypatch.setattr(equivalence, "_isolated_equivalence_tree", _passthrough_tree)
    monkeypatch.setattr(equivalence, "_immutable_equivalence_data", _passthrough_data)
    def trace(**kwargs: object) -> dict[str, str]:
        case = kwargs["case"]
        assert isinstance(case, equivalence.Phase1Case)
        captured.append(case.name)
        return {"decision_payload_sha256": case.name, "economic_account_sha256": "state"}

    monkeypatch.setattr(equivalence, "trace_phase1_case", trace)

    report = equivalence.compare_phase1_commits(
        frozen_root=frozen,
        candidate_root=candidate,
        data_dir=tmp_path / "data",
        cases=None,
    )

    assert report["cases"] == 45
    assert len(captured) == 90
    assert captured.count("e/h1_2023") == 2


def test_performance_equivalence_rejects_a_dirty_frozen_checkout_before_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a frozen commit label authenticating mutable working-tree inputs."""

    checkout = tmp_path / "frozen"
    benchmark = checkout / "benchmarks" / "promotion_baseline.json"
    benchmark.parent.mkdir(parents=True)
    payload = json.loads(Path("benchmarks/promotion_baseline.json").read_text(encoding="utf-8"))
    benchmark.write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    payload["pools"]["a"][0] = "sz000001"
    benchmark.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(equivalence, "_git_commit", lambda _root: FROZEN_CHAMPION_COMMIT)
    monkeypatch.setattr(
        equivalence,
        "trace_phase1_case",
        lambda **_kwargs: pytest.fail("dirty checkout must be rejected before replay"),
    )

    with pytest.raises(RuntimeError, match="clean committed inputs"):
        equivalence.compare_phase1_commits(
            frozen_root=checkout,
            candidate_root=checkout,
            data_dir=tmp_path / "data",
        )


def test_performance_equivalence_rejects_a_checkout_dirtied_during_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a replay result being attributed after its source changes in flight."""

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    monkeypatch.setattr(equivalence, "_git_commit", lambda _root: FROZEN_CHAMPION_COMMIT)
    monkeypatch.setattr(
        equivalence,
        "_baseline_data_provenance",
        lambda _path: {"snapshot_id": "fixture"},
    )
    monkeypatch.setattr(equivalence, "_immutable_equivalence_data", _passthrough_data)
    monkeypatch.setattr(equivalence, "_isolated_equivalence_tree", _passthrough_tree)
    calls = 0

    def trace(**_kwargs: object) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            source = checkout / "uquant" / "engine.py"
            source.parent.mkdir()
            source.write_text("# changed during replay\n", encoding="utf-8")
        return {
            "decision_payload_sha256": "a" * 64,
            "economic_account_sha256": "b" * 64,
        }

    monkeypatch.setattr(equivalence, "trace_phase1_case", trace)
    case = equivalence.Phase1Case(
        name="a/h1_2023",
        symbols=("sz000001",),
        start="2023-01-01",
        end="2023-01-02",
    )

    with pytest.raises(RuntimeError, match="clean committed inputs"):
        equivalence.compare_phase1_commits(
            frozen_root=checkout,
            candidate_root=checkout,
            data_dir=tmp_path / "data",
            cases=(case,),
        )


def test_performance_equivalence_rejects_an_untracked_runtime_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches an untracked root sitecustomize hook outside a pathspec status check."""

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "sitecustomize.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    monkeypatch.setattr(equivalence, "_git_commit", lambda _root: FROZEN_CHAMPION_COMMIT)
    monkeypatch.setattr(
        equivalence,
        "trace_phase1_case",
        lambda **_kwargs: pytest.fail("untracked runtime hook must be rejected before replay"),
    )
    case = equivalence.Phase1Case(
        name="a/h1_2023",
        symbols=("sz000001",),
        start="2023-01-01",
        end="2023-01-02",
    )

    with pytest.raises(RuntimeError, match="clean committed inputs"):
        equivalence.compare_phase1_commits(
            frozen_root=checkout,
            candidate_root=checkout,
            data_dir=tmp_path / "data",
            cases=(case,),
        )


def test_performance_equivalence_rejects_unbound_market_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches equal traces on data that is not the frozen baseline snapshot."""

    checkout = tmp_path / "checkout"
    baseline = checkout / "benchmarks/promotion_baseline.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(
        json.dumps(
            {
                "provenance": {
                    "data": {
                        "snapshot_id": "expected",
                        "files_verified": 1,
                        "manifest_sha256": "a" * 64,
                        "checksums_sha256": "b" * 64,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(equivalence, "_git_commit", lambda _root: FROZEN_CHAMPION_COMMIT)
    monkeypatch.setattr(equivalence, "_require_clean_equivalence_tree", lambda _root: None)
    monkeypatch.setattr(equivalence, "_isolated_equivalence_tree", _passthrough_tree)
    monkeypatch.setattr(
        equivalence,
        "trace_phase1_case",
        lambda **_kwargs: {
            "decision_payload_sha256": "a" * 64,
            "economic_account_sha256": "b" * 64,
        },
    )
    case = equivalence.Phase1Case(
        name="a/h1_2023",
        symbols=("sz000001",),
        start="2023-01-01",
        end="2023-01-02",
    )

    with pytest.raises(RuntimeError, match="frozen data"):
        equivalence.compare_phase1_commits(
            frozen_root=checkout,
            candidate_root=checkout,
            data_dir=tmp_path / "substituted-data",
            cases=(case,),
        )


def test_performance_equivalence_retains_one_data_snapshot_and_detects_source_drift(
    tmp_path: Path,
) -> None:
    """Catches cases observing different data bytes during one comparison matrix."""

    source = tmp_path / "data"
    expected = _frozen_data_fixture(source)
    original = (source / "sz000001.csv").read_bytes()

    with (
        pytest.raises(RuntimeError, match="changed during replay"),
        equivalence._immutable_equivalence_data(source, expected) as snapshot,
    ):
        assert (snapshot / "sz000001.csv").read_bytes() == original
        (source / "sz000001.csv").write_bytes(original + b"changed\n")
        assert (snapshot / "sz000001.csv").read_bytes() == original


def test_performance_equivalence_snapshot_excludes_unauthenticated_entries(
    tmp_path: Path,
) -> None:
    """Catches private snapshots copying files outside the authenticated inventory."""

    source = tmp_path / "data"
    expected = _frozen_data_fixture(source)
    outside = tmp_path / "outside-secret"
    outside.write_text("not authenticated\n", encoding="utf-8")
    (source / "untracked-extra").symlink_to(outside)

    with equivalence._immutable_equivalence_data(source, expected) as snapshot:
        assert {path.name for path in snapshot.iterdir()} == {
            "DATA_MANIFEST.json",
            "SHA256SUMS",
            "sz000001.csv",
        }


def test_performance_equivalence_snapshot_fails_closed_at_copy_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises unsafe roots, baseline mismatch, and authenticated-copy failure."""

    source = tmp_path / "data"
    expected = _frozen_data_fixture(source)
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with (
        pytest.raises(RuntimeError, match="frozen data is unsafe"),
        equivalence._immutable_equivalence_data(alias, expected),
    ):
        pass

    changed = dict(expected)
    changed["snapshot_id"] = "different"
    with (
        pytest.raises(RuntimeError, match="differs from the baseline"),
        equivalence._immutable_equivalence_data(source, changed),
    ):
        pass

    verify_calls = 0

    def drift_after_copy(_: Path) -> dict[str, object]:
        nonlocal verify_calls
        verify_calls += 1
        return dict(expected) if verify_calls == 1 else changed

    with monkeypatch.context() as patch:
        patch.setattr(equivalence, "verify_data_manifest", drift_after_copy)
        with (
            pytest.raises(RuntimeError, match="changed during snapshot"),
            equivalence._immutable_equivalence_data(source, expected),
        ):
            pass

    monkeypatch.setattr(
        equivalence.shutil,
        "copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with (
        pytest.raises(RuntimeError, match="cannot snapshot"),
        equivalence._immutable_equivalence_data(source, expected),
    ):
        pass


def test_performance_equivalence_rejects_private_data_snapshot_mutation(
    tmp_path: Path,
) -> None:
    """Catches evaluated code changing the shared snapshot between replay cases."""

    source = tmp_path / "data"
    expected = _frozen_data_fixture(source)

    with (
        pytest.raises(RuntimeError, match="private data changed during replay"),
        equivalence._immutable_equivalence_data(source, expected) as snapshot,
    ):
        csv = snapshot / "sz000001.csv"
        csv.chmod(0o600)
        csv.write_bytes(csv.read_bytes() + b"changed\n")


def test_performance_equivalence_replays_private_source_and_data_snapshots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches executing caller-mutable checkout or data paths after authentication."""

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    (checkout / "marker.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(equivalence, "FROZEN_CHAMPION_COMMIT", commit)
    def baseline_provenance(path: Path) -> dict[str, str]:
        assert not path.resolve().is_relative_to(checkout.resolve())
        return {"snapshot_id": "fixture"}

    monkeypatch.setattr(
        equivalence,
        "_baseline_data_provenance",
        baseline_provenance,
        raising=False,
    )

    @contextmanager
    def immutable_data(
        _source: Path,
        _expected: object,
    ) -> Iterator[Path]:
        snapshot = tmp_path / "private-data"
        snapshot.mkdir()
        yield snapshot

    monkeypatch.setattr(
        equivalence,
        "_immutable_equivalence_data",
        immutable_data,
        raising=False,
    )
    original_data = tmp_path / "original-data"
    original_data.mkdir()

    def trace(**kwargs: object) -> dict[str, str]:
        assert Path(str(kwargs["root"])) != checkout.resolve()
        assert Path(str(kwargs["data_dir"])) != original_data.resolve()
        return {
            "decision_payload_sha256": "a" * 64,
            "economic_account_sha256": "b" * 64,
        }

    monkeypatch.setattr(equivalence, "trace_phase1_case", trace)
    case = equivalence.Phase1Case(
        name="a/h1_2023",
        symbols=("sz000001",),
        start="2023-01-01",
        end="2023-01-02",
    )

    report = equivalence.compare_phase1_commits(
        frozen_root=checkout,
        candidate_root=checkout,
        data_dir=original_data,
        cases=(case,),
    )

    assert report["passed"] is True


def test_performance_trace_disables_site_initialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches global ``.pth`` or ``sitecustomize`` execution before source binding."""

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[1:3] == ["-I", "-S"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "decision_payload_sha256": "a" * 64,
                    "economic_account_sha256": "b" * 64,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(equivalence.subprocess, "run", run)
    observed = equivalence.trace_phase1_case(
        root=tmp_path,
        data_dir=tmp_path / "data",
        case=equivalence.Phase1Case(
            name="a/h1_2023",
            symbols=("sz000001",),
            start="2023-01-01",
            end="2023-01-02",
        ),
    )

    assert observed["decision_payload_sha256"] == "a" * 64


def test_performance_partial_worktree_add_is_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches partial worktree materialization bypassing detached-tree cleanup."""

    removals: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "add" in command:
            Path(command[-2]).mkdir(parents=True)
            raise subprocess.CalledProcessError(1, command)
        if "remove" in command:
            removals.append(command)
            shutil.rmtree(Path(command[-1]))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(equivalence.subprocess, "run", run)

    with (
        pytest.raises(RuntimeError, match="materialize"),
        equivalence._isolated_equivalence_tree(tmp_path, "a" * 40),
    ):
        pytest.fail("partial materialization must not yield")

    assert removals
