from __future__ import annotations

from dataclasses import replace

import pytest

from research.risk_differential_models import (
    CapabilityRecord,
    RiskTraceRow,
    canonical_sha256,
    validate_capabilities,
)


def test_canonical_hash_ignores_order_and_own_seal() -> None:
    left = {"b": 2, "a": 1}
    right = {"payload_sha256": "ignored", "a": 1, "b": 2}
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left) != canonical_sha256({"a": 2, "b": 2})


def test_not_ready_trace_cannot_claim_normal() -> None:
    with pytest.raises(ValueError, match="severity"):
        RiskTraceRow.empty("2026-08-05", "trade", status="NOT_READY", severity_rank=0)


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
