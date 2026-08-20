from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.committed_economic_equivalence import (
    _assert_equivalent_case_traces,
    _checkpoint_identity,
    _load_checkpoint,
    _report,
)


def _trace(*, decision: str = "d" * 64, account: str = "a" * 64) -> dict[str, str]:
    return {
        "decision_payload_sha256": decision,
        "economic_account_sha256": account,
    }


def test_committed_equivalence_rejects_any_decision_or_account_difference() -> None:
    cases = {"a/bull": {"baseline": _trace(), "candidate": _trace()}}
    _assert_equivalent_case_traces(cases)

    cases["a/bull"]["candidate"] = _trace(decision="e" * 64)
    with pytest.raises(RuntimeError, match="decision payload diverged: a/bull"):
        _assert_equivalent_case_traces(cases)

    cases["a/bull"]["candidate"] = _trace(account="b" * 64)
    with pytest.raises(RuntimeError, match="economic account diverged: a/bull"):
        _assert_equivalent_case_traces(cases)


def test_checkpoint_resume_is_bound_to_commits_data_and_matrix(tmp_path: Path) -> None:
    identity = _checkpoint_identity(
        baseline_commit="a" * 40,
        candidate_commit="b" * 40,
        data={"snapshot_id": "fixture"},
        matrix_sha256="c" * 64,
    )
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({**identity, "case_traces": {"a/bull": {"baseline": _trace()}}}),
        encoding="utf-8",
    )

    loaded = _load_checkpoint(checkpoint, identity=identity)
    assert loaded == {"a/bull": {"baseline": _trace()}}

    wrong = dict(identity)
    wrong["candidate_commit"] = "d" * 40
    with pytest.raises(RuntimeError, match="identity differs"):
        _load_checkpoint(checkpoint, identity=wrong)


def test_report_records_exact_stage8_dimensions() -> None:
    cases = {
        "a/bull": {"baseline": _trace(), "candidate": _trace()},
        "e/year_2024": {
            "baseline": _trace(decision="e" * 64, account="b" * 64),
            "candidate": _trace(decision="e" * 64, account="b" * 64),
        },
    }
    payload = _report(
        identity={
            "schema": "uquant.committed-economic-equivalence.v1",
            "baseline_commit": "a" * 40,
            "candidate_commit": "b" * 40,
            "data": {"snapshot_id": "fixture"},
            "matrix_sha256": "c" * 64,
        },
        case_traces=cases,
    )

    assert payload["passed"] is True
    assert payload["cases"] == 2
    assert payload["baseline_trace_sha256"] == payload["candidate_trace_sha256"]
    assert all(payload["exact_dimensions"].values())
