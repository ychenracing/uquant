"""Native probe fills stay accountable without becoming an ACTIVE coronation."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

import pytest
from _absolute_generalization_metrics_fixture import EPOCH_ID, OWNER, complete_replay

from uquant.models.strategic_epoch import (
    StrategicEpoch,
    _advance_strategic_epoch_fill,
    close_strategic_epoch,
    derive_strategic_epoch_id,
)
from uquant.validation.absolute_generalization._execution_chain_reconciliation import (
    validate_exact_execution_chain,
)
from uquant.validation.absolute_generalization._metric_primitives import metric_trace_row
from uquant.validation.absolute_generalization._metrics_reconciliation import _downstream_chain_flags
from uquant.validation.absolute_generalization.metrics import actual_epoch_facts_from_rows


def _native_probe(*, activated: bool = False, terminal: str = ""):
    replay = complete_replay()
    account = json.loads(replay.final_account_payload.canonical_json)
    trace = [metric_trace_row(
        session=row.session, decision=json.loads(row.decision_payload.canonical_json),
        qualification_coverage=1.0,
    ) for row in replay.observations]
    original = account["strategic_epochs"][0]
    identity = dict(
        account_identity="account:metric-probe", owner_symbol=OWNER,
        qualification_signature=original["qualification_signature"],
        qualification_route=original["qualification_route"], grant_id=original["grant_id"],
        opened_session=original["opened_session"], previous_epoch_id="",
        source_identity="code:metric-probe", config_identity="config:metric-probe",
        evidence_sha256="a" * 64,
    )
    epoch = StrategicEpoch(
        epoch_id=derive_strategic_epoch_id(**identity), **identity,
        qualification_quorum="ABSOLUTE_SINGLE", realized_status="PROBE",
        target_weight=0.2, full_weight=0.5,
    )
    account = json.loads(json.dumps(account).replace(EPOCH_ID, epoch.epoch_id))
    trace = json.loads(json.dumps(trace).replace(EPOCH_ID, epoch.epoch_id))
    _advance_strategic_epoch_fill(
        epoch, grant_id=epoch.grant_id, symbol=OWNER,
        fill_session="2023-01-04", filled_shares=5 if activated else 10,
    )
    assert epoch.realized_status == "CORE" and not epoch.active_session
    if activated:
        first = account["fills"][0]
        first.update(shares=5, gross_value=50.0)
        later = {**first, "fill_date": "2023-01-05", "fill_id": "activation-fill"}
        account["fills"].append(later)
        account["order_ledger"][0]["last_update_date"] = "2023-01-05"
        trace.append({**deepcopy(trace[-1]), "session": "2023-01-05"})
        _advance_strategic_epoch_fill(
            epoch, grant_id=epoch.grant_id, symbol=OWNER,
            fill_session="2023-01-05", filled_shares=5,
        )
        assert epoch.realized_status == "ACTIVE"
    if terminal:
        close_strategic_epoch(
            epoch, closed_session="2023-01-06", close_reason="probe settled",
            expired=terminal == "EXPIRED",
        )
    account["strategic_epochs"] = [asdict(epoch)]
    return account, trace


@pytest.mark.parametrize("terminal", ("", "CLOSED", "EXPIRED"))
def test_native_core_fill_is_validated_without_counting_an_active_epoch(terminal):
    account, trace = _native_probe(terminal=terminal)
    fills_before = deepcopy(account["fills"])
    facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
    assert facts == ()
    validate_exact_execution_chain(final_account=account, trace=trace, epochs=facts)
    assert account["fills"] == fills_before
    assert not _downstream_chain_flags(orders=(), fills=account["fills"], epochs=facts)[1]


@pytest.mark.parametrize("terminal", ("", "CLOSED"))
def test_later_native_buy_activates_once_and_preserves_first_fill_causality(terminal):
    account, trace = _native_probe(activated=True, terminal=terminal)
    facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
    assert len(facts) == 1
    assert facts[0].fill_session == "2023-01-04"
    assert facts[0].active_session == "2023-01-05"
    validate_exact_execution_chain(final_account=account, trace=trace, epochs=facts)
    assert _downstream_chain_flags(orders=(), fills=account["fills"], epochs=facts)[1]
    assert not _downstream_chain_flags(orders=(), fills=account["fills"][:1], epochs=facts)[1]


@pytest.mark.parametrize("mutation", ("missing_buy", "zero_shares", "sell", "wrong_grant", "wrong_date"))
def test_active_date_requires_a_positive_matching_activation_buy(mutation):
    account, trace = _native_probe(activated=True)
    if mutation == "missing_buy":
        account["fills"].pop()
    elif mutation == "zero_shares":
        account["fills"][-1]["shares"] = 0
    elif mutation == "sell":
        account["fills"][-1]["side"] = "SELL"
    elif mutation == "wrong_grant":
        account["fills"][-1]["grant_id"] = "other-grant"
    else:
        account["strategic_epochs"][0]["active_session"] = "2023-01-06"
    with pytest.raises(ValueError):
        facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=facts)


@pytest.mark.parametrize("mutation", ("qualification", "authorization", "target", "order", "grant_evidence"))
def test_uncounted_core_probe_cannot_hide_broken_authority_or_execution(mutation):
    account, trace = _native_probe()
    if mutation == "qualification":
        for row in trace:
            row["risk"]["strategic_qualification"]["qualification_ready"] = False
    elif mutation == "authorization":
        for row in trace:
            row["risk"]["strategic_cash_rearm"]["consumed_grant_id"] = "other-grant"
    elif mutation == "target":
        trace[0]["targets"] = []
    elif mutation == "order":
        trace[0]["orders"] = []
    else:
        for row in trace:
            row["risk"]["strategic_grant"]["qualification_evidence_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
        validate_exact_execution_chain(final_account=account, trace=trace, epochs=facts)


@pytest.mark.parametrize("mutation", ("first_fill_missing", "active_missing", "core_with_active"))
def test_realized_epoch_cannot_erase_or_mislabel_its_fill_history(mutation):
    account, trace = _native_probe(activated=True)
    epoch = account["strategic_epochs"][0]
    if mutation == "first_fill_missing":
        epoch["first_fill_session"] = ""
    elif mutation == "active_missing":
        epoch["active_session"] = ""
    else:
        epoch["realized_status"] = "CORE"
    with pytest.raises(ValueError):
        actual_epoch_facts_from_rows(final_account=account, trace=trace)


@pytest.mark.parametrize("mutation", ("sell", "zero_shares", "wrong_epoch", "wrong_grant"))
def test_downstream_activation_does_not_substitute_an_unrelated_fill(mutation):
    account, trace = _native_probe(activated=True)
    facts = actual_epoch_facts_from_rows(final_account=account, trace=trace)
    fill = account["fills"][-1]
    if mutation == "sell":
        fill["side"] = "SELL"
    elif mutation == "zero_shares":
        fill["shares"] = 0
    elif mutation == "wrong_epoch":
        fill["epoch_id"] = "other-epoch"
    else:
        fill["grant_id"] = "other-grant"
    assert not _downstream_chain_flags(orders=(), fills=account["fills"], epochs=facts)[1]
