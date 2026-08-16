from __future__ import annotations

import json
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


def test_phase1_equivalence_rejects_any_cross_commit_decision_or_account_divergence() -> None:
    """Breaks if a Phase 2 candidate changes a frozen Phase 1 economic trace."""
    assert_equivalent_phase1_traces(_trace(), _trace())

    with pytest.raises(RuntimeError, match="decision payload"):
        assert_equivalent_phase1_traces(_trace(), _trace(decision="changed"))
    with pytest.raises(RuntimeError, match="economic account"):
        assert_equivalent_phase1_traces(_trace(), _trace(account="changed"))


def test_phase1_equivalence_covers_every_official_and_protected_pool_case() -> None:
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
