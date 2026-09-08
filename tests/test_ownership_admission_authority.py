"""Ownership audits actual admission authority and observed repair tiers."""
from copy import deepcopy

import pytest

from scripts import run_strategic_ownership_acceptance as runner


def admission(*, rearm=False):
    grant = {"grant_id": "grant-one", "candidate_symbol": "owner", "created_session": "2024-04-26",
             "authorization_id": "rearm-one" if rearm else ""}
    return {"session": "2024-04-26", "risk": {
        "state": "NORMAL", "votes": 0, "target_gross_cap": 1.0,
        "capital_budget_level": int(rearm), "chronic_level": 0, "freeze_new_risk": rearm,
        "strategic_grant": grant,
        "strategic_cash_rearm": {"authorization_id": "rearm-one", "status": "CONSUMED",
                                 "consumed_grant_id": "grant-one", "candidate_symbol": "owner",
                                 "authorized_session": "2024-04-26"} if rearm else {},
    }}


def test_normal_admission_does_not_need_unrelated_later_rearm():
    row = admission()
    assert runner._validate_admission_authority([row])[0]["path"] == "NORMAL"


@pytest.mark.parametrize("field,value", [("capital_budget_level", 1), ("chronic_level", 1),
                                          ("freeze_new_risk", True)])
def test_rearm_required_path_cannot_strip_authorization(field, value):
    row = admission()
    row["risk"][field] = value
    with pytest.raises(ValueError, match="required rearm"):
        runner._validate_admission_authority([row])


@pytest.mark.parametrize("field,value", [("state", "RISK_OFF"), ("state", "CRISIS"),
                                          ("target_gross_cap", 0), ("votes", float("nan"))])
def test_rearm_does_not_override_base_risk(field, value):
    row = admission(rearm=True)
    row["risk"][field] = value
    with pytest.raises(ValueError):
        runner._validate_admission_authority([row])


def test_caution_with_two_votes_cannot_admit():
    row = admission(rearm=True)
    row["risk"].update(state="CAUTION", votes=2)
    with pytest.raises(ValueError, match="Base Risk"):
        runner._validate_admission_authority([row])


@pytest.mark.parametrize("field,value", [("authorization_id", "wrong"), ("candidate_symbol", "wrong"),
                                          ("consumed_grant_id", "wrong"), ("status", "AUTHORIZED"),
                                          ("authorized_session", "2025-01-01")])
def test_rearm_requires_creation_time_consumed_chain(field, value):
    row = admission(rearm=True)
    assert runner._validate_admission_authority([row])[0]["path"] == "REARM"
    row["risk"]["strategic_cash_rearm"][field] = value
    with pytest.raises(ValueError, match="authorization chain"):
        runner._validate_admission_authority([row])


def removal_summary():
    return {"scenario_id": "remove-sz300308", "final_wealth": 1.8, "max_drawdown": .17,
            "longest_healthy_zero_target_streak": 0, "positive_target_sessions": 1, "distinct_owners": ["owner"],
            "repair_episodes": [{"capital_budget_level": 1, "reported_healthy_sessions": 20,
                                 "actual_healthy_sessions_to_ready": 20,
                                 "last_ready_session": "2026-05-19"}]}


def test_level_one_ready_is_not_level_three_coverage():
    summary = removal_summary()
    runner._validate_full_removal(runner.load_contract(), removed_symbol="sz300308", summary=summary)
    assert summary["level_three_repair_coverage"] == {
        "observed_episodes": 0, "ready_episodes": 0, "status": "NOT_OBSERVED"}


def test_later_level_three_failure_is_not_hidden_by_first_ready():
    summary = removal_summary()
    later = deepcopy(summary["repair_episodes"][0])
    later.update(capital_budget_level=3, reported_healthy_sessions=61,
                 actual_healthy_sessions_to_ready=61)
    summary["repair_episodes"].append(later)
    with pytest.raises(RuntimeError, match="bounded clock"):
        runner._validate_full_removal(runner.load_contract(), removed_symbol="sz300308", summary=summary)


def test_removal_still_requires_absolute_economic_thresholds():
    summary = removal_summary()
    summary["max_drawdown"] = .31
    with pytest.raises(RuntimeError):
        runner._validate_full_removal(runner.load_contract(), removed_symbol="sz300308", summary=summary)


def test_normal_budget_cannot_hide_a_stripped_consumed_authorization():
    row = admission(rearm=True)
    row["risk"].update(capital_budget_level=0, freeze_new_risk=False)
    row["risk"]["strategic_grant"]["authorization_id"] = ""
    with pytest.raises(ValueError, match="consumed rearm"):
        runner._validate_admission_authority([row])


@pytest.mark.parametrize("field", ["votes", "capital_budget_level", "freeze_new_risk"])
def test_missing_creation_risk_evidence_fails_closed(field):
    row = admission()
    del row["risk"][field]
    with pytest.raises(ValueError):
        runner._validate_admission_authority([row])
