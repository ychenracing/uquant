"""The ordinary ownership summary must audit probes excluded from crown counts."""

from __future__ import annotations

import pytest
from test_epoch_activation_metrics import _native_probe

from research.strategic_evidence.replay import ReplayRequest, ReplayResult
from research.strategic_evidence.trace import RouteTraceRow
from scripts.run_strategic_ownership_acceptance import actual_epoch_facts


def _ownership_probe_result(*, activated: bool = False, broken: str = "") -> ReplayResult:
    account, trace = _native_probe(activated=activated)
    if broken == "authorization":
        for row in trace:
            row["risk"]["strategic_cash_rearm"]["consumed_grant_id"] = "other-grant"
    elif broken == "grant_evidence":
        for row in trace:
            row["risk"]["strategic_grant"]["qualification_evidence_sha256"] = "b" * 64
    owner = account["strategic_epochs"][0]["owner_symbol"]
    rows = tuple(
        RouteTraceRow(
            date=row["session"], reference_context={"reference_coverage": 1.0}, leaders=(),
            risk=row["risk"], opportunity=row["opportunity"], targets=tuple(row["targets"]),
            orders=tuple(row["orders"]),
            fills=tuple(fill for fill in account["fills"] if fill["fill_date"] == row["session"]),
            account_sha256="a" * 64, equity=1_100.0, target_gross=row["target_gross"],
            intervention_provenance=None, cash=900.0, position_shares={}, close_marks={},
        )
        for row in trace
    )
    return ReplayResult(
        request=ReplayRequest(symbols=(owner,), start=rows[0].date, end=rows[-1].date),
        metrics={}, trace=rows, final_account=account, intervention_provenance=None,
    )


@pytest.mark.parametrize("activated", (False, True))
def test_ownership_summary_audits_native_probe_before_counting_activation(activated):
    result = _ownership_probe_result(activated=activated)
    facts = actual_epoch_facts(result)
    assert len(facts) == int(activated)
    assert len(result.final_account["fills"]) == 1 + int(activated)
    if activated:
        assert facts[0]["fill_session"] == "2023-01-04"
        assert facts[0]["active_session"] == "2023-01-05"


@pytest.mark.parametrize("broken", ("authorization", "grant_evidence"))
def test_ownership_summary_rejects_uncounted_core_with_broken_authority(broken):
    with pytest.raises(ValueError):
        actual_epoch_facts(_ownership_probe_result(broken=broken))
