"""Reversal stock evidence survives an unrelated macro phase; weak witnesses do not."""
from dataclasses import replace

import pytest
from test_strategic_witness_selection import _strong_decisive_inputs

from research.cross_ai_robustness import deletion_evidence
from uquant.config import DEFAULT_CONFIG
from uquant.engine import code_fingerprint
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.strategic.qualification_candidates import strategic_route_candidates


@pytest.mark.parametrize("index_return", (-0.02, 0.02, 0.40))
def test_stock_reversal_sync_has_no_macro_return_veto(index_return):
    snapshots, leaders, risk = _strong_decisive_inputs()
    risk = replace(risk, evidence={**risk.evidence, "tech_ret120": index_return})
    routes = strategic_route_candidates(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots=snapshots, leaders=leaders, risk=risk,
    )
    groups = [route for route in routes if route.route == "reversal_industry" and len(route.symbols) >= 2]
    assert groups and all(route.synchronized_reversal for route in groups)


def test_weak_stock_rebound_still_cannot_claim_synchronized_reversal():
    snapshots, leaders, risk = _strong_decisive_inputs()
    for snapshot in snapshots.values():
        snapshot["ret20"] = -0.20
    routes = strategic_route_candidates(
        PortfolioAllocator(DEFAULT_CONFIG), snapshots=snapshots, leaders=leaders, risk=risk,
    )
    assert all(not route.synchronized_reversal for route in routes)
    assert all(route.decisive_reversal_symbol is None for route in routes)


def test_retired_index_override_fails_closed_and_has_real_deletion_evidence():
    field = "strategic_reversal_max_tech_ret120"
    with pytest.raises(TypeError, match="unexpected keyword"):
        DEFAULT_CONFIG.override(**{field: -0.015})
    evidence = deletion_evidence([field], code_fingerprint())
    assert evidence["config_absent"] and evidence["executable_references"] == []
