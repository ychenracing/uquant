"""Historical grant authority survives later, unrelated rearm observations."""
from __future__ import annotations

import json

import pytest
from _absolute_generalization_metrics_fixture import AUTHORIZATION_ID, GRANT_ID, OWNER, complete_replay

from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
    _validate_grant_qualification_provenance,
    validate_exact_execution_chain,
)
from uquant.validation.absolute_generalization.metrics import EpochFact, actual_epoch_facts_from_rows


def _execution_rows():
    replay = complete_replay()
    account = json.loads(replay.final_account_payload.canonical_json)
    trace = []
    for observation in replay.observations:
        decision = json.loads(observation.decision_payload.canonical_json)
        risk = decision["risk_summary"]
        risk["strategic_cash_rearm"].update(
            status="CONSUMED", candidate_symbol=OWNER, consumed_grant_id=GRANT_ID,
        )
        trace.append({
            "session": decision["date"], "risk": risk,
            "targets": decision["targets"], "orders": decision["pending_orders"],
            "fills": [json.loads(item.canonical_json) for item in observation.new_fills],
        })
    return account, trace


def _validate(account, trace):
    facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
    validate_exact_execution_chain(final_account=account, trace=trace, epochs=facts)
    return facts


@pytest.mark.parametrize("new_status", ("INVALIDATED", "OBSERVING", "CONSUMED"))
def test_historical_authorization_does_not_follow_new_candidate_rearm(new_status):
    account, trace = _execution_rows()
    # The native j remove-sz300308 trace retains the expired 2024-07-22 grant
    # while its live rearm slot moves to another candidate on 2025-07-29.
    trace[-1]["risk"]["strategic_grant"]["status"] = "EXPIRED"
    trace[-1]["risk"]["strategic_cash_rearm"] = {
        "status": new_status, "candidate_symbol": "sz300502",
        "authorization_id": "rearm_" + "9" * 64 if new_status == "CONSUMED" else "",
        "authorized_session": trace[-1]["session"] if new_status == "CONSUMED" else "",
        "consumed_grant_id": "grant_" + "8" * 64 if new_status == "CONSUMED" else "",
    }

    facts = _validate(account, trace)

    assert len(facts) == 1
    assert facts[0].authorization_id == AUTHORIZATION_ID
    assert facts[0].authorization_session == "2023-01-03"


@pytest.mark.parametrize("field,value", (
    ("authorized_session", ""),
    ("authorization_id", "rearm_" + "9" * 64),
))
def test_missing_creation_authority_cannot_be_backfilled_from_later_trace(field, value):
    account, trace = _execution_rows()
    trace[0]["risk"]["strategic_cash_rearm"][field] = value

    with pytest.raises(ValueError, match="authorization"):
        _validate(account, trace)


@pytest.mark.parametrize("field,value", (
    ("authorized_session", "2023-01-02"),
    ("candidate_symbol", "sz300502"),
    ("consumed_grant_id", "grant_" + "9" * 64),
))
def test_same_authorization_identity_still_rejects_conflicting_later_evidence(field, value):
    account, trace = _execution_rows()
    trace[-1]["risk"]["strategic_cash_rearm"][field] = value

    with pytest.raises(ValueError, match="authorization"):
        _validate(account, trace)


@pytest.mark.parametrize("authorization_id", ("", "rearm_" + "9" * 64), ids=("lost", "switched"))
def test_nonterminal_grant_cannot_discard_its_current_authorization(authorization_id):
    account, trace = _execution_rows()
    trace[-1]["risk"]["strategic_grant"]["status"] = "PARTIALLY_FILLED"
    trace[-1]["risk"]["strategic_cash_rearm"].update(
        authorization_id=authorization_id,
        authorized_session="" if not authorization_id else trace[-1]["session"],
        candidate_symbol="sz300502", consumed_grant_id="",
    )

    with pytest.raises(ValueError, match="authorization"):
        _validate(account, trace)


def _native_delayed_qualification():
    # Literal minimal rows from SHA-verified CI artifact 9975665568,
    # remove-sz300308; dates and evidence hashes are unchanged native facts.
    fact = EpochFact(**{'epoch_id': 'epoch_922978b80bcca79ac9d30a8f8369a772e047d4e47ec50d1d472b20e6ebb995d6',
     'grant_id': 'grant_8138e6459c00d4e3db9af5b705624dbab518763565ffe93ed9dd95dd41906ba1',
     'owner_symbol': 'sh601869',
     'qualification_signature': 'strategic_qualification:SECULAR:sh600487:optical,sh601869:optical,sz300394:optical:evidence=established',
     'qualification_route': 'established',
     'qualification_quorum': 'FULL_COHORT',
     'qualification_session': '2026-04-16',
     'grant_session': '2026-04-22',
     'target_session': '2026-04-22',
     'order_session': '2026-04-22',
     'fill_session': '2026-04-23',
     'active_session': '2026-04-23',
     'closed_session': '',
     'close_reason': '',
     'realized_status': 'ACTIVE',
     'previous_epoch_id': 'epoch_07a7a6607ae715b19368e6ea13a7fc50c24383a0cbb7f5237cbd2ec16f12df7e',
     'previous_grant_id': 'grant_7833e85df1d7d03585066d4cabe20f79682d071a3bcd9ec60f48db6f69c64ac5',
     'authorization_id': 'rearm_8654ca40fab9af6039ca51442ac53b6bb19bf41cc8b680d60ebfe82d25c9e0f4',
     'authorization_session': '2026-04-22'})
    grant = {'candidate_symbol': 'sh601869',
     'created_session': '2026-04-22',
     'qualification_signature': 'strategic_qualification:SECULAR:sh600487:optical,sh601869:optical,sz300394:optical:evidence=established',
     'qualification_route': 'established',
     'qualification_quorum': 'FULL_COHORT',
     'qualification_evidence_sha256': '1aea4da92ed12e6d657ad103962106120644f16c487afd4790c6bafda4760421'}
    trace = [{'session': '2026-04-16',
      'risk': {'strategic_qualification': {'candidate_symbol': 'sh601869',
                                           'qualification_ready': True,
                                           'qualification_signature': 'strategic_qualification:SECULAR:sh600487:optical,sh601869:optical,sz300394:optical:evidence=established',
                                           'qualification_route': 'established',
                                           'qualification_quorum': 'FULL_COHORT',
                                           'qualification_evidence_sha256': '94288ca490e76e5925e1c41ac9e67e64e102544bb89a8da43d6ca5c634166398'}}},
     {'session': '2026-04-22',
      'risk': {'strategic_qualification': {'candidate_symbol': 'sh601869',
                                           'qualification_ready': True,
                                           'qualification_signature': 'strategic_qualification:SECULAR:sh600487:optical,sh601869:optical,sz300394:optical:evidence=established',
                                           'qualification_route': 'established',
                                           'qualification_quorum': 'FULL_COHORT',
                                           'qualification_evidence_sha256': '1aea4da92ed12e6d657ad103962106120644f16c487afd4790c6bafda4760421'}}}]
    return fact, grant, trace


def test_delayed_native_grant_binds_creation_day_evidence_not_first_ready_hash():
    fact, grant, trace = _native_delayed_qualification()
    first = trace[0]["risk"]["strategic_qualification"]
    created = trace[1]["risk"]["strategic_qualification"]
    assert first["qualification_evidence_sha256"] != grant["qualification_evidence_sha256"]
    assert created["qualification_evidence_sha256"] == grant["qualification_evidence_sha256"]

    _validate_grant_qualification_provenance(trace=trace, fact=fact, grant=grant)


@pytest.mark.parametrize("tamper", (
    "first_not_ready", "first_wrong_signature", "creation_not_ready",
    "creation_wrong_signature", "creation_wrong_hash", "missing_creation",
))
def test_delayed_grant_still_requires_both_qualification_witnesses(tamper):
    fact, grant, trace = _native_delayed_qualification()
    first = trace[0]["risk"]["strategic_qualification"]
    created = trace[1]["risk"]["strategic_qualification"]
    if tamper == "first_not_ready":
        first["qualification_ready"] = False
    elif tamper == "first_wrong_signature":
        first["qualification_signature"] = "tampered-unrelated-qualification"
    elif tamper == "creation_not_ready":
        created["qualification_ready"] = False
    elif tamper == "creation_wrong_signature":
        created["qualification_signature"] = "tampered-unrelated-qualification"
    elif tamper == "creation_wrong_hash":
        created["qualification_evidence_sha256"] = first["qualification_evidence_sha256"]
    else:
        trace.pop()

    with pytest.raises(ValueError, match="qualification"):
        _validate_grant_qualification_provenance(trace=trace, fact=fact, grant=grant)
