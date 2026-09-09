"""Admission coverage must not depend on the selected risk-anchor identities."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine
from uquant.portfolio.strategic.quorum import _market_confirmation
from uquant.types import AccountState, Risk, RiskAssessment


def _risk() -> RiskAssessment:
    return RiskAssessment(
        state=Risk.NORMAL, target_gross_cap=0.5, votes=0, reasons=(), shock_state="NONE",
        evidence={
            "breadth20": 0.8, "broad_ret20": 0.05, "tech_ret20": 0.08,
            "broad_ret120": 0.12, "tech_ret120": 0.2,
            "risk_anchor_group_count": 0,
            "reference_visible_groups": ["compute", "equipment", "materials"],
            "reference_coverage": 26 / 27,
        },
    )


def test_independent_reference_groups_admit_without_a_selected_anchor_basket() -> None:
    risk = _risk()
    original = deepcopy(risk)
    assert _market_confirmation(risk, DEFAULT_CONFIG) == (True, True)
    assert risk == original  # Observing admission evidence grants no risk permission.
    risk.evidence["risk_anchor_group_count"] = 3
    assert _market_confirmation(risk, DEFAULT_CONFIG) == (True, True)


@pytest.mark.parametrize("field,value", [
    ("reference_visible_groups", None),
    ("reference_visible_groups", ["compute", "compute", "materials"]),
    ("reference_visible_groups", ["compute", "unknown", "materials"]),
    ("reference_visible_groups", ["compute", 3, "materials"]),
    ("reference_coverage", None), ("reference_coverage", True),
    ("reference_coverage", float("nan")), ("reference_coverage", 0.0),
    ("reference_coverage", 1.1), ("tech_ret120", float("nan")),
])
def test_anchor_basket_cannot_replace_missing_or_invalid_reference_evidence(field, value) -> None:
    risk = _risk()
    risk.evidence["risk_anchor_group_count"] = 3
    risk.evidence[field] = value
    assert not _market_confirmation(risk, DEFAULT_CONFIG)[1]


def test_current_risk_state_still_controls_market_confirmation() -> None:
    risk = _risk()
    risk = replace(risk, state=Risk.CRISIS)
    assert not _market_confirmation(risk, DEFAULT_CONFIG)[1]


def test_real_decision_publishes_reference_identity_before_allocation(monkeypatch) -> None:
    engine = ProductionEngine(Path(__file__).resolve().parents[1] / "data/frozen")
    allocate = engine.allocator.allocate
    observed = {}

    def record_then_allocate(**kwargs):
        observed.update(deepcopy(kwargs["risk"].evidence))
        return allocate(**kwargs)

    monkeypatch.setattr(engine.allocator, "allocate", record_then_allocate)
    engine.decide(symbols=("sz300308", "sz300502", "sz300394"), as_of="2023-03-06",
                  account=AccountState.empty(DEFAULT_CONFIG.initial_cash))
    assert len(observed["reference_visible_groups"]) >= DEFAULT_CONFIG.strategic_cohort_min_size
    assert set(observed["reference_visible_symbols"]) <= set(observed["reference_expected_symbols"])
    assert 0 < observed["reference_coverage"] <= 1
