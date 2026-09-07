"""Unit boundaries for the separately sealed CORE participation assertion."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace

import pytest
from test_core_transfer_attribution import _execution_scenario
from test_core_transfer_feasibility import CHALLENGER
from test_cross_ai_ownership_continuity import continuity_replay

import scripts.run_strategic_ownership_acceptance as runner
from research.strategic_evidence.trace import RouteTraceRow


def _native_entry(monkeypatch):
    policy, args, dates, planner, panel, submit = _execution_scenario(monkeypatch, "ready")
    account = args["account"]
    submit(dates[-3], policy.allocate(**args))
    planner.execute_open(date=dates[-2], account=account, panel=panel)
    args["date"] = dates[-2]
    targets = policy.allocate(**args)
    target = next(target for target in targets if target.symbol == CHALLENGER)
    row = RouteTraceRow(
        date=str(dates[-2].date()), reference_context={}, leaders=(),
        risk={"state": args["risk"].state.value, "freeze_new_risk": args["risk"].freeze_new_risk,
              "target_gross_cap": args["risk"].target_gross_cap,
              "core_allocation": copy.deepcopy(args["risk"].evidence["core_allocation"])},
        opportunity="TREND", targets=(), orders=(), fills=(), account_sha256="unit-native",
        equity=account.cash + sum(position.shares * 10 for position in account.positions.values()),
        cash=account.cash,
        position_shares={symbol: position.shares for symbol, position in account.positions.items()},
        close_marks={symbol: 10.0 for symbol in account.positions},
    )
    submit(dates[-2], targets)
    fills = planner.execute_open(date=dates[-1], account=account, panel=panel)
    fill = next(fill for fill in fills if fill.symbol == CHALLENGER and fill.side == "BUY")
    order = next(order for order in account.order_ledger if order.order_id == fill.order_id)
    assert fill.shares > 0 and not order.grant_id and not order.epoch_id
    return row, order, asdict(target)


def test_native_ordinary_entry_has_qualification_and_settled_common_capital(monkeypatch):
    row, order, target = _native_entry(monkeypatch)
    proof = runner._participation_entry(row, order, target)
    assert proof["qualification"]["block"] == "READY"
    assert proof["capital_budget"]["accepted"] is True


@pytest.mark.parametrize("mutation", ("confirmation", "freeze", "cash", "industry", "correlation", "risk", "missing", "required", "wrong_route", "base_freeze", "occupied"))
def test_participation_entry_rejects_missing_or_exceeded_authority(monkeypatch, mutation):
    row, order, target = _native_entry(monkeypatch)
    book = row.risk["core_allocation"]
    owner = book["symbols"][order.symbol]
    accepted = next(check for check in owner["budget_checks"] if check["accepted"])
    if mutation == "confirmation":
        owner["entry"]["confirmations"] = {"independent_core": 0}
    elif mutation == "freeze":
        book["freeze_new_risk"] = True
    elif mutation == "cash":
        row = replace(row, cash=0)
    elif mutation in {"industry", "correlation"}:
        accepted[f"{mutation}_room"] = 0
    elif mutation == "risk":
        row.risk["target_gross_cap"] = 0
    elif mutation == "required":
        owner["entry"]["required_confirmation"] += 1
        owner["entry"]["confirmations"] = {"independent_core": 999}
    elif mutation == "wrong_route":
        owner["entry"]["confirmations"] = {"unrelated": 999}
    elif mutation == "base_freeze":
        row.risk["freeze_new_risk"] = True
    elif mutation == "occupied":
        accepted["gross_room"] = book["gross_cap"]
    else:
        del owner["entry"]
    with pytest.raises((ValueError, RuntimeError)):
        runner._participation_entry(row, order, target)


def test_legacy_epoch_pair_does_not_create_independent_core_participation():
    result = continuity_replay()
    contract = runner.load_contract()
    summary = runner._continuity_summary(contract, result)
    summary.update(scenario_id="same-industry-crowning", source_scenario_id="remove-sz300502")
    report = runner._participation_alias(contract, summary)
    assert report["legacy_same_industry_crowning"]["status"] == "PASS"
    assert report["same_industry_core_participation"]["status"] == "FAIL"
    assert report["status"] == "FAIL"
    assert report["epochs"] == summary["epochs"]


def test_legacy_failure_remains_failure_and_is_explicitly_superseded():
    result = continuity_replay(("sz300308", "sh688008"))
    contract = runner.load_contract()
    summary = runner._continuity_summary(contract, result)
    summary.update(scenario_id="same-industry-crowning", source_scenario_id="remove-sz300502")
    report = runner._participation_alias(contract, summary)
    legacy = report["legacy_same_industry_crowning"]
    assert legacy["status"] == "FAIL" and "superseded" in legacy["disposition"]
    assert report["same_industry_core_participation"]["status"] == "FAIL"


def test_continuous_positions_are_reconstructed_from_fills_not_remaining_lot_dates():
    result = continuity_replay()
    expected = runner._core_participation_facts(result)
    row = result.trace[2]
    changed = replace(row, position_shares={})
    corrupt = replace(result, trace=(*result.trace[:2], changed, *result.trace[3:]))
    assert len(expected) == 3
    with pytest.raises(ValueError, match="continuous position"):
        runner._core_participation_facts(corrupt)


def test_participation_terminal_account_must_match_all_real_fills():
    result = continuity_replay()
    account = copy.deepcopy(result.final_account)
    account["cash"] += 1
    with pytest.raises(ValueError, match="terminal account"):
        runner._core_participation_facts(replace(result, final_account=account))


def test_participation_relationship_requires_later_independent_same_industry_owner():
    # Relationship unit facts only, not an economic replay or acceptance artifact.
    first = {"owner_symbol": "first", "industry_at_entry": "compute", "first_fill_session": "2025-01-03",
             "admission_session": "2025-01-02", "closed_session": "", "independent_entry": None}
    later = {**first, "owner_symbol": "later", "admission_session": "2025-01-06",
             "first_fill_session": "2025-01-07", "independent_entry": {"unit": "already validated"}}
    assert runner._participation_witness([first, later])["relationship"] == "continued_holding"
    for wrong in ({"admission_session": "2025-01-02"}, {"owner_symbol": "first"},
                  {"industry_at_entry": "materials"}, {"independent_entry": None}):
        assert runner._participation_witness([first, {**later, **wrong}]) is None
    assert runner._participation_witness([{**first, "closed_session": "2025-01-06"}, later])["relationship"] == "settled_exit"


def test_participation_filled_order_quantity_requires_actual_fill_sum():
    result = continuity_replay()
    account = copy.deepcopy(result.final_account)
    account["order_ledger"][0]["filled_shares"] -= 1
    with pytest.raises(ValueError, match="positive fill attribution"):
        runner._core_participation_facts(replace(result, final_account=account))
