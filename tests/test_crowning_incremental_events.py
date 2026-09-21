"""A later same-epoch buy cannot replace or duplicate its activation evidence."""
from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from _absolute_generalization_metrics_fixture import payload
from test_absolute_generalization_reachability import _filled_chain

from uquant.validation.absolute_generalization import recovery_runtime as runtime


@pytest.mark.parametrize("include_activation", [True, False])
def test_same_epoch_increment_keeps_first_activation(monkeypatch, include_activation):
    target, grant, epoch, order, fill = _filled_chain()
    grant.authorization_id = ""
    closed = deepcopy(epoch)
    closed.closed_session = "2026-02-02"
    first = dict(target=target, grant=grant, epoch=epoch, orders=(order,), fills=(fill,))
    later_target = replace(target, event_id="later-event")
    later_fill = replace(fill, event_id="later-event", fill_date="2026-01-08", order_id="O000000002")
    later = dict(first, target=later_target, fills=(fill, later_fill))
    qualification = {"qualification_ready": True, "candidate_symbol": target.symbol,
                     "qualification_signature": grant.qualification_signature}
    observation = SimpleNamespace(session=grant.created_session,
        decision_runtime_payload=payload(dict(strategic_qualification=qualification,
                                              strategic_successor_qualification={})),
        decision_payload=payload(dict(targets=[asdict(target)], pending_orders=[asdict(order)],
                                      risk_summary={"strategic_grant": asdict(grant)})))
    replay = SimpleNamespace(observations=[observation], final_account_payload=payload({}))
    # This unit tests event projection; the real-account codec is verified separately.
    final_order = replace(order, status="CANCELLED", cancel_reason="remaining quantity cancelled")
    monkeypatch.setattr(runtime, "_account", lambda _: SimpleNamespace(
        strategic_epochs=[closed], order_ledger=[final_order]))
    outlets = [first, later] if include_activation else [later]
    transitions = [{"state": {"outlet_evidence": outlet}} for outlet in outlets]
    if not include_activation:
        with pytest.raises(ValueError, match="activation fill is absent"):
            runtime._crowning_payload(replay, transitions, source_name="test", cross=False)
        return
    evidence = runtime._crowning_payload(replay, transitions, source_name="test", cross=False)
    assert len(evidence["chains"]) == 1
    assert evidence["chains"][0]["fill"] == asdict(fill)
    assert evidence["chains"][0]["order"] == asdict(final_order)
    assert evidence["chains"][0]["target"] == asdict(target)


@pytest.mark.parametrize("include_creation", [True, False])
def test_pending_order_repeats_keep_creation_session(include_creation):
    target, grant, epoch, order, _ = _filled_chain()
    grant.authorization_id = ""
    dates = [grant.created_session, "2026-01-06", "2026-01-07"]
    observations = [SimpleNamespace(session=day, decision_payload=payload({
        "targets": [asdict(target)],
        "pending_orders": [asdict(order)] if include_creation or day != dates[0] else [],
        "risk_summary": {"strategic_grant": asdict(grant)},
    })) for day in dates]
    replay = SimpleNamespace(observations=observations)
    args = dict(target=target, grant=grant, epoch=epoch, order=order)
    if not include_creation:
        with pytest.raises(RuntimeError, match="order session differs"):
            runtime._crowning_decision_sessions(replay, **args)
        return
    assert runtime._crowning_decision_sessions(replay, **args) == (dates[0], dates[0], "")
