from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.post_generalization_trust_closure_checkpoint_c_adjudication import (
    adjudicate_report,
)
from uquant.contracts.strict_json import canonical_json_sha256

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "benchmarks/pre_cleanup_current_behavior_reference.json"
REPORT = ROOT / "benchmarks/post_generalization_trust_closure_checkpoint_c.json"


def test_identity_only_report_drift_is_deterministically_equivalent() -> None:
    result = adjudicate_report(reference=REFERENCE, report=REPORT)

    assert result["exact_economic_equivalence"] is False
    assert result["deterministic_economic_equivalence"] is True
    assert result["identity_only_difference_paths"] == [
        "strategic_epochs[0].config_identity",
        "strategic_epochs[0].source_identity",
        "strategic_grant.production_source_identity",
    ]
    assert result["dimensions"] == {
        "account_sessions": True,
        "attribution_accounting": True,
        "config_semantic_sha256": True,
        "current_account_codec": True,
        "decision_sessions": True,
        "empty_current_account_roundtrip_sha256": True,
        "fills": True,
        "final_account_observed_economics": True,
        "ledger_sessions": True,
        "metrics": True,
        "orders": True,
        "strategic_epochs_economic": True,
        "strategic_grant_economic": True,
        "window": True,
    }
    assert result["candidate_report_canonical_sha256"] == (
        "3aab551b05cd8630a9a7458a2b4b9e6a280f1a33502907420f2d828bf51a7eb8"
    )


def test_adjudication_recomputes_dimensions_instead_of_trusting_report(
    tmp_path: Path,
) -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(report)
    mutated["candidate_projection"]["metrics"]["final_wealth"] = 0.0
    mutated.pop("canonical_sha256")
    mutated["canonical_sha256"] = canonical_json_sha256(mutated)
    path = tmp_path / "mutated-report.json"
    path.write_text(json.dumps(mutated, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dimensions differ from recomputation"):
        adjudicate_report(reference=REFERENCE, report=path)
