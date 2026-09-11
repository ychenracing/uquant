"""Research-only exception; original production freeze tests remain unchanged."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader

from uquant.config import DEFAULT_CONFIG
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity, PendingOrder, Risk, RiskAssessment


def _fixture():
    dates = pd.bdate_range("2025-01-02", periods=150)
    close = np.ones(len(dates))
    close[-1] = .94
    frame = pd.DataFrame({"close": close, "ma20": .9, "ma60": .9, "ma120": .9,
                          "ret5": -.05, "ret20": -.10, "ret60": -.3, "ret120": -.4,
                          "amount": 1_000_000_000.}, index=dates)
    risk = RiskAssessment(Risk.CAUTION, 1., 1,
                          {"broad_ret120": -.1, "tech_ret120": .04, "freeze_new_risk": False},
                          (), "NONE", freeze_new_risk=True, reduction_level=1)
    return frame, risk, AccountState.empty(100.)


def _decide(frame, risk, account):
    return PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=frame.index[-1], opportunity=Opportunity.CHOPPY, risk=risk,
        user_panel={"candidate": frame}, leaders={"candidate": _leader("candidate", .9)},
        account=account, prices={"candidate": .94})


def test_original_caution_probe_uses_budget_and_keeps_risk_frozen():
    frame, risk, account = _fixture()
    targets = _decide(frame, risk, account)
    assert len(targets) == 1
    assert targets[0].weight == pytest.approx(DEFAULT_CONFIG.tactical_probe_weight)
    assert targets[0].mechanism == "TACTICAL_REBOUND"
    assert targets[0].origin_subsystem == "RECOVERY"
    assert risk.freeze_new_risk is True
    assert account.cash == 100. and not account.positions


@pytest.mark.parametrize("block", ["overlay", "sentinel", "risk_off", "crisis", "capital",
                                 "chronic", "sector", "anchor", "protected", "restore", "pending", "repair_custody"])
def test_other_freezes_and_existing_rights_block_research_probe(block):
    frame, risk, account = _fixture()
    if block in {"overlay", "sentinel"}:
        risk.evidence["freeze_new_risk" if block == "overlay" else "sentinel_freeze_new_risk"] = True
    elif block in {"risk_off", "crisis"}:
        risk = replace(risk, state=Risk.RISK_OFF if block == "risk_off" else Risk.CRISIS)
    elif block in {"capital", "chronic"}:
        setattr(account, "capital_budget_level" if block == "capital" else "chronic_level", 1)
    elif block == "repair_custody":
        account.candidate_tenure["ordinary_repair_capital_active"] = 1
    elif block == "sector":
        account.sector_guard_active = True
    elif block in {"anchor", "protected", "restore"}:
        field = {"anchor": "anchor_weights", "protected": "protected_weights", "restore": "strategic_restore_weights"}[block]
        setattr(account, field, {"candidate": .6})
    else:
        account.pending_orders = [PendingOrder(str(frame.index[-1].date()), "candidate", "BUY", .6, "pending", "RECOVERY")]
    targets = _decide(frame, risk, account)
    assert not any(t.weight > 0 for t in targets)
    assert account.candidate_tenure.get("tactical_active", 0) == 0


def test_no_capital_does_not_commit_probe_ownership():
    frame, risk, account = _fixture()
    risk = replace(risk, target_gross_cap=0.)
    assert _decide(frame, risk, account) == ()
    assert account.candidate_tenure.get("tactical_active", 0) == 0
    assert account.tactical_anchor_symbol == ""
