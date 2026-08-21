from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from research.risk_differential_models import (
    CapabilityRecord,
    RiskDifferentialEvent,
    RiskTraceRow,
    canonical_sha256,
    hash_lock_files,
    hash_python_sources,
    hash_selected_sources,
    validate_capabilities,
    validate_registry_checkout,
)


def test_canonical_hash_ignores_order_and_own_seal() -> None:
    left = {"b": 2, "a": 1}
    right = {"payload_sha256": "ignored", "a": 1, "b": 2}
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left) != canonical_sha256({"a": 2, "b": 2})


def test_not_ready_trace_cannot_claim_normal() -> None:
    with pytest.raises(ValueError, match="severity"):
        RiskTraceRow.empty("2026-08-05", "trade", status="NOT_READY", severity_rank=0)


def test_differential_event_rejects_open_ended_classification() -> None:
    with pytest.raises(ValueError, match="classification"):
        RiskDifferentialEvent(
            date="2026-08-05",
            axis="warning_level",
            classification="INVENTED",
            trade_value=True,
            base_value=False,
            sentinel_value=False,
            actionable_buy_intents=0,
            actionable_pyramid_intents=0,
            base_already_protected=False,
        )


def test_capability_registry_is_closed_unique_and_non_promotable() -> None:
    record = CapabilityRecord(
        capability_id="risk.market_velocity",
        trade_source=("quantfusion/risk/overlay/evidence.py",),
        category="OBSERVATION",
        uquant_base_equivalent=("uquant/market_risk.py",),
        sentinel_equivalent=("uquant/risk_sentinel/evidence.py",),
        mapping_status="ABSORBED_BASE",
        action_classification="DIRECTLY_REPLAYABLE",
        exact_transfer_possible=True,
        economic_counterfactual_supported=True,
        production_promotion_allowed_this_phase=False,
        rationale="same causal market axis",
    )
    validate_capabilities((record,))
    with pytest.raises(ValueError, match="unique"):
        validate_capabilities((record, record))
    with pytest.raises(ValueError, match="production promotion"):
        validate_capabilities((replace(record, production_promotion_allowed_this_phase=True),))


def _git_identity(root: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "commit": commit,
        "python_source_sha256": hash_python_sources(root),
        "lock_files": ["requirements-lock.txt"],
        "lock_sha256": hash_lock_files(root, ("requirements-lock.txt",)),
        "risk_source_files": ["risk.py"],
        "risk_source_sha256": hash_selected_sources(root, ("risk.py",)),
    }


def test_registry_checkout_requires_real_git_head_full_source_and_lock(tmp_path: Path) -> None:
    (tmp_path / "risk.py").write_text("RISK = 1\n", encoding="utf-8")
    (tmp_path / "transitive.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "requirements-lock.txt").write_text("pandas==1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "frozen"],
        cwd=tmp_path,
        check=True,
    )
    identity = _git_identity(tmp_path)
    validate_registry_checkout(tmp_path, identity)

    (tmp_path / "transitive.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="full Python-source"):
        validate_registry_checkout(tmp_path, identity)
    subprocess.run(["git", "checkout", "--", "transitive.py"], cwd=tmp_path, check=True)

    (tmp_path / "requirements-lock.txt").write_text("pandas==2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="lock"):
        validate_registry_checkout(tmp_path, identity)


def test_registry_checkout_rejects_marker_only_export(tmp_path: Path) -> None:
    (tmp_path / ".frozen_commit").write_text("a" * 40, encoding="utf-8")
    (tmp_path / "risk.py").write_text("RISK = 1\n", encoding="utf-8")
    (tmp_path / "requirements-lock.txt").write_text("pandas==1\n", encoding="utf-8")
    identity = {
        "commit": "a" * 40,
        "python_source_sha256": hash_python_sources(tmp_path),
        "lock_files": ["requirements-lock.txt"],
        "lock_sha256": hash_lock_files(tmp_path, ("requirements-lock.txt",)),
        "risk_source_files": ["risk.py"],
        "risk_source_sha256": hash_selected_sources(tmp_path, ("risk.py",)),
    }
    with pytest.raises(ValueError, match="Git checkout"):
        validate_registry_checkout(tmp_path, identity)
