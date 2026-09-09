"""Creation-day authority stays strict when later repair state changes."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from _absolute_generalization_metrics_fixture import payload
from test_absolute_generalization_reachability import _filled_chain

from uquant.validation.absolute_generalization import recovery_runtime as runtime


@pytest.mark.parametrize("creation", ["valid", "wrong", "absent", "normal"])
def test_crowning_binds_authority_to_the_actual_creation_session(creation):
    target, grant, epoch, order, _fill = _filled_chain()
    if creation == "normal":
        grant.authorization_id = ""
    first = {
        "strategic_grant": asdict(grant),
        "strategic_cash_rearm": {"authorization_id": grant.authorization_id,
                                 "authorized_session": grant.created_session},
    }
    if creation == "wrong":
        first["strategic_cash_rearm"]["authorization_id"] = "wrong"
    if creation == "absent":
        first["strategic_grant"] = {}
    later = {"strategic_grant": asdict(grant), "strategic_cash_rearm": {
        "authorization_id": "later-independent-repair", "authorized_session": "2026-02-02",
    }}
    observations = [SimpleNamespace(session=date, decision_payload=payload({
        "targets": [asdict(target)] if index == 0 else [],
        "pending_orders": [asdict(order)] if index == 0 else [], "risk_summary": risk,
    })) for index, (date, risk) in enumerate(((grant.created_session, first), ("2026-02-02", later)))]
    args = dict(target=target, grant=grant, epoch=epoch, order=order)
    replay = SimpleNamespace(observations=observations)
    if creation in {"wrong", "absent"}:
        with pytest.raises((ValueError, RuntimeError)):
            runtime._crowning_decision_sessions(replay, **args)
    else:
        assert runtime._crowning_decision_sessions(replay, **args) == (
            grant.created_session, grant.created_session,
            "" if creation == "normal" else grant.created_session,
        )
