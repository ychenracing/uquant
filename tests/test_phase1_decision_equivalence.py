from __future__ import annotations

import pytest

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
