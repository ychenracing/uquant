from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import unified_ai_quant.validation.robustness as robustness_module
import unified_ai_quant.validation.stress as stress_module
from unified_ai_quant.validation.provenance import (
    assert_replay_signature_unchanged,
    bounded_data_fingerprint,
    validation_fingerprint,
)
from unified_ai_quant.validation.robustness import promotion_holdback_status
from unified_ai_quant.validation.runner import (
    _assert_evidence_inputs_unchanged,
    _evidence_input_hashes,
)
from unified_ai_quant.validation.stress import build_scenarios


def test_stress_matrix_contains_every_required_universe_structure(data_dir):
    scenarios = build_scenarios(data_dir)
    random_rows = [item for item in scenarios if item.scenario_type == "random_subset"]
    assert len(random_rows) == 900
    assert {len(item.symbols) for item in random_rows} == {3, 5, 9, 15, 22, 32}
    identifiers = {item.scenario_id for item in scenarios}
    for required in (
        "prefix-01",
        "structure-optical",
        "structure-equipment",
        "structure-materials",
        "structure-memory-compute",
        "structure-diversified",
        "structure-high-correlation",
        "structure-low-correlation",
        "structure-mature-heavy",
        "structure-emerging-heavy",
        "structure-loser-heavy",
        "permutation-primary-reversed",
    ):
        assert required in identifiers
    assert sum(item.scenario_type == "replace_one" for item in scenarios) == 5


def test_promotion_holdback_lock_preserves_sealed_or_consumed_contract(data_dir):
    root = Path(__file__).resolve().parents[1]
    lock = json.loads(
        (root / "benchmarks" / "PROMOTION_HOLDBACK.json").read_text(
            encoding="utf-8"
        )
    )
    status = promotion_holdback_status(data_dir)
    assert status["canonical_hash_match"] is True
    assert status["files"] == 36
    assert status["rows"] == 432
    assert status["expected_sessions"] == 12
    assert status["complete_coverage"] is True
    assert status["incomplete_files"] == []
    if lock["status"] == "SEALED_UNEVALUATED":
        assert status["candidate_hash_match"] is True
        assert status["untouched"] is True
        return

    assert lock["status"] in {"CONSUMED_PASS", "CONSUMED_FAIL"}
    assert status["untouched"] is False
    result_path = root / "benchmarks" / "promotion_holdback_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert lock["status"] == f"CONSUMED_{result['status']}"
    assert hashlib.sha256(result_path.read_bytes()).hexdigest() == lock[
        "consumed_result_sha256"
    ]
    assert result["canonical_sha256"] == status["canonical_sha256"]
    assert result["production_code_sha256"] == lock[
        "consumed_production_code_sha256"
    ]
    assert result["validation_code_sha256"] == lock[
        "consumed_validation_code_sha256"
    ]


def test_artifact_signatures_bind_validation_code(data_dir, monkeypatch):
    scenarios = stress_module.build_scenarios(data_dir)
    original = stress_module._signature(data_dir, scenarios)
    assert original["validation_code_sha256"] == validation_fingerprint()
    assert original["data_sha256"] == bounded_data_fingerprint(
        data_dir,
        end=stress_module.STRESS_END,
    )
    robust = robustness_module._signature(
        data_dir,
        robustness_module.build_experiments(),
    )
    assert robust["data_sha256"] == bounded_data_fingerprint(
        data_dir,
        end=robustness_module.THROUGH_JULY[1],
    )

    monkeypatch.setattr(stress_module, "validation_fingerprint", lambda: "changed")
    changed = stress_module._signature(data_dir, scenarios)
    assert changed != original


def test_long_replay_rejects_mixed_input_snapshots():
    initial = {
        "production_code_sha256": "before",
        "validation_code_sha256": "validation",
        "data_sha256": "data",
    }
    current = {**initial, "production_code_sha256": "after"}

    with pytest.raises(RuntimeError, match="refusing mixed-version evidence"):
        assert_replay_signature_unchanged(
            initial,
            current,
            replay="stress",
        )

    assert_replay_signature_unchanged(
        initial,
        dict(initial),
        replay="stress",
    )


def test_promotion_holdback_rejects_candidate_hash_mismatch(data_dir, monkeypatch):
    monkeypatch.setattr(robustness_module, "code_fingerprint", lambda: "changed")
    status = robustness_module.promotion_holdback_status(data_dir)
    assert status["candidate_hash_match"] is False
    assert status["untouched"] is False


def test_acceptance_rejects_inputs_changed_during_long_replay(data_dir):
    root = Path(__file__).resolve().parents[1]
    legacy_path = root / "benchmarks" / "legacy_common_adapter.json"
    expected = _evidence_input_hashes(root, data_dir, legacy_path)
    expected["production_code_sha256"] = "changed-after-workers-started"

    with pytest.raises(RuntimeError, match="mixed-version evidence"):
        _assert_evidence_inputs_unchanged(
            expected,
            root=root,
            data_dir=data_dir,
            legacy_path=legacy_path,
        )
