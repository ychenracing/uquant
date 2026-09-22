"""Preserve risk precedence, native settlement and account wire-state semantics."""
from __future__ import annotations

import ast
import copy
import itertools
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from uquant.models.ordinary_state import (
    CoreTransfer, clear_repair_origins, record_repair_origin, repair_origin_recorded, transfers,
)
from uquant.portfolio.freeze import commit_frozen_observations
from uquant.portfolio.pipeline import _prepare_account
from uquant.risk.confirmed_break import _confirmed_break_code, _confirmed_break_reason
from uquant.risk.recovery_state import _last_shock_was_market_backed
from uquant.types import AccountState, Risk, RiskAssessment


def test_structured_break_trigger_keeps_every_original_priority():
    source = subprocess.check_output([
        "git", "show", "7bc5cd5e20038c94ab5cf556ce107104692236ac:uquant/risk/confirmed_break.py"
    ], cwd=Path(__file__).resolve().parents[1], text=True)
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                    and n.name == "_confirmed_break_reason")
    function.args.args[0].annotation = None
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "base-reason", "exec"), scope)
    names = ("held_cohort_break_confirmed", "terminal_market_backed_restoration_relapse",
             "market_backed_restoration_relapse", "capital_drawdown_relapse",
             "incomplete_universe_tail_break", "credible_reserve", "strategic_active")
    resets = {"capital drawdown relapse in restored holdings",
              "market-backed portfolio break in incomplete restoration"}
    for flags in itertools.product((False, True), repeat=len(names)):
        ctx = SimpleNamespace(**dict(zip(names, flags, strict=True)))
        original = scope["_confirmed_break_reason"](ctx)
        assert _confirmed_break_reason(ctx) == original
        assert (_confirmed_break_code(ctx) in {"INCOMPLETE_RESTORATION_BREAK", "CAPITAL_RESTORATION_RELAPSE"}) == (original in resets)


def test_current_risk_reset_uses_structured_authority_not_prose():
    for authorized in (False, True):
        calls = []
        policy = SimpleNamespace(
            _release_stale_recovery_anchor=lambda **kwargs: None,
            _release_recovery_anchor=lambda account: calls.append("release"),
            _retire_strategic_member=lambda account, symbol: calls.append(symbol),
        )
        state = AccountState.empty(1000)
        state.protected_weights["sz300308"] = .3
        risk = RiskAssessment(Risk.CRISIS, 0.0, 3,
                              {"recovery_owner_reset_required": authorized},
                              ("arbitrary translated display text",), "SHOCK")
        _prepare_account(policy, risk=risk, account=state, weights_now={})
        assert bool(calls) == authorized
        assert bool(state.protected_weights) != authorized


def test_recovery_history_prefers_codes_with_only_legacy_fallback():
    state = AccountState.empty(1000)
    state.last_shock_date = "2026-01-06"
    event = {"date": state.last_shock_date, "to": "CRISIS",
             "reasons": ["market-backed drawdown relapse in restored holdings"]}
    state.risk_events.append(event)
    ctx = SimpleNamespace(account=state)
    assert _last_shock_was_market_backed(ctx)
    event["break_reason_code"] = "DYNAMIC_COHORT_BREAK"
    assert not _last_shock_was_market_backed(ctx)
    event["break_reason_code"] = "INCOMPLETE_RESTORATION_BREAK"
    event["reasons"] = ["display prose no longer controls recovery"]
    assert _last_shock_was_market_backed(ctx)


def test_transfer_wire_view_is_continuous_idempotent_and_isolated():
    state = AccountState.empty(1000)
    transfer = CoreTransfer("sz300308", "sz300502")
    assert CoreTransfer.from_key(transfer.key) == transfer
    transfer.observe(state, session=100, previous_session=99, qualified=True)
    transfer.observe(state, session=100, previous_session=99, qualified=True)
    assert transfer.confirmations(state) == 1
    transfer.observe(state, session=101, previous_session=100, qualified=True)
    assert transfer.confirmations(state) == 2
    transfer.observe(state, session=103, previous_session=102, qualified=True)
    assert transfer.confirmations(state) == 1
    before = copy.deepcopy(state.to_dict())
    with pytest.raises(RuntimeError, match="backwards"):
        transfer.observe(state, session=102, previous_session=101, qualified=True)
    assert state.to_dict() == before
    other = CoreTransfer("sz300394", "sz300502")
    other.observe(state, session=103, previous_session=102, qualified=True)
    transfer.record_request(state, 104)
    assert other.confirmations(state) == 0
    assert transfer.observed_session(state) == 104
    assert transfers(state) == (transfer, other)
    assert state.candidate_tenure["core_transfer_session:sz300308->sz300502"] == 104


def test_repair_origin_view_keeps_account_wire_format_and_unrelated_state():
    state = AccountState.empty(1000)
    state.candidate_tenure["unrelated_clock"] = 20
    record_repair_origin(state, "O000000001", "evt_1")
    assert repair_origin_recorded(state, "O000000001", "evt_1")
    assert not repair_origin_recorded(state, "O000000002", "evt_1")
    assert state.candidate_tenure["ordinary_repair_origin:O000000001:evt_1"] == 1
    clear_repair_origins(state)
    assert state.candidate_tenure == {"unrelated_clock": 20}


def test_frozen_observation_commit_does_not_copy_capital_or_grants():
    current = AccountState.empty(1000)
    planned = copy.deepcopy(current)
    planned.cash = 500
    planned.protected_weights["sz300308"] = .5
    planned.candidate_tenure.update({"ordinary_repair_capital_active": 1,
                                     "strategic_repair_observed_session": 100})
    planned.replacement_tenure["lifecycle_exit:sz300308"] = 2
    commit_frozen_observations(current, planned)
    assert current.cash == 1000 and not current.protected_weights
    assert "ordinary_repair_capital_active" not in current.candidate_tenure
    assert current.candidate_tenure["strategic_repair_observed_session"] == 100
    assert current.replacement_tenure["lifecycle_exit:sz300308"] == 2
