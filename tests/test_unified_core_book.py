"""Capital and lifecycle invariants of the combined production book."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from uquant.account import load_account, save_account
from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, plan_orders, reconcile_account_orders
from uquant.portfolio import PortfolioAllocator
from uquant.portfolio.capital import admission_room
from uquant.portfolio.pipeline import _degraded_transfer, allocate_strategy
from uquant.portfolio.strategic.lifecycle import (
    _final_strategic_proposal,
    _strategic_lifecycle_context,
)
from uquant.risk.strategic_guard import strategic_grace_supported
from uquant.types import (
    AccountState,
    LeaderScore,
    Opportunity,
    PendingOrder,
    Position,
    Risk,
    RiskAssessment,
    Target,
)
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


def _inputs():
    dates = pd.bdate_range("2025-01-02", periods=150)
    panel = {}
    for index, symbol in enumerate(("sh600001", "sh600002", "sh600003")):
        returns = 0.001 + 0.004 * np.sin(np.arange(150) * (0.73 + index * 0.48))
        close = 10 * np.cumprod(1 + returns)
        panel[symbol] = pd.DataFrame(
            {
                "close": close,
                "ma20": close * 0.99,
                "ma60": close * 0.97,
                "ma120": close * 0.95,
                "ret20": 0.10,
                "ret60": 0.20,
                "ret120": 0.30,
                "amount": 100_000_000.0,
            },
            index=dates,
        )
    leaders = {
        s: LeaderScore(s, 0.9, 0.9, True, False, str(i), {"unknown_industry": 0.0})
        for i, s in enumerate(panel)
    }
    risk = RiskAssessment(Risk.NORMAL, 1.0, 0, {}, (), "NORMAL")
    return dates[-1], panel, leaders, risk


@pytest.mark.parametrize("restriction", ("none", "freeze", "profit_lock", "trailing_exit"))
def test_nearly_funded_cohort_keeps_registered_buy_remainder_until_a_real_reduction(restriction):
    date, panel, leaders, risk = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(2_000_000.0)
    account.strategic_cohort_targets = {"sh600001": 0.95}
    account.strategic_cohort_symbols = ["sh600001"]
    account.pending_orders = [PendingOrder(
        "2025-07-28", "sh600001", "BUY", 0.95, "registered founding intent", "CORE",
    )]
    current = {"sh600001": 0.931}
    ctx = _strategic_lifecycle_context(
        policy, date=date, risk=replace(risk, freeze_new_risk=restriction == "freeze"),
        user_panel=panel, leaders=leaders, account=account, prices={"sh600001": 10.0},
        weights_now=current,
    )
    assert account.candidate_tenure["strategic_cohort_started"] == 1
    if restriction == "profit_lock":
        ctx.dominant_profit_lock_armed_now = True
        ctx.dominant_symbol = "sh600001"
    elif restriction == "trailing_exit":
        account.strategic_exit_bands["sh600001"] = [0.20, 0.20, 0.20]
    proposed = _final_strategic_proposal(ctx, active_symbols=set(current), current_selected=current)
    expected = {"none": 0.95, "freeze": 0.931, "profit_lock": 0.70, "trailing_exit": 0.60}
    assert proposed["sh600001"] == pytest.approx(expected[restriction])


def _evaluate(monkeypatch, *, cash=400.0, frozen=False, pending=False, strategic_exit=False):
    date, panel, leaders, risk = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1000.0)
    account.cash = cash
    account.positions["sh600001"] = Position("sh600001", 60, 8.0, "2025-01-02", 10.0)
    account.strategic_cohort_symbols = ["sh600001"]
    account.strategic_cohort_targets = {"sh600001": 0.6}
    account.leader_tenure = {s: policy.cfg.leader_tenure_days for s in leaders}
    account.replacement_tenure.update(
        {f"strategic_eligibility:established:{s}": policy.cfg.leader_tenure_days for s in leaders}
    )
    if pending:
        account.pending_orders = [
            PendingOrder(str(date.date()), "sh600003", "BUY", 0.35, "existing commitment", "CORE")
        ]
    strategic = Target(
        "sh600001",
        0.0 if strategic_exit else 0.6,
        "CORE",
        0.9,
        0.9,
        "incumbent lifecycle",
        origin_subsystem="STRATEGIC",
        mechanism="STRATEGIC_COHORT",
    )
    monkeypatch.setattr(policy, "_strategic_cohort_targets", lambda **kwargs: (strategic,))
    result = allocate_strategy(
        policy,
        date=date,
        opportunity=Opportunity.TREND,
        risk=replace(risk, freeze_new_risk=frozen),
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={s: 10.0 for s in leaders},
    )
    return policy, account, {t.symbol: t for t in result}


@pytest.mark.parametrize("guard", ("none", "sentinel", "crisis"))
def test_saved_strategic_restoration_permission_survives_same_decision_completion(guard):
    date, panel, leaders, risk = _inputs()
    symbol = "sh600001"
    date = panel[symbol].index[-2]
    account = AccountState.empty(1_000_000.0)
    account.cash = 445_000.0
    account.positions[symbol] = Position(symbol, 55_500, 10.0, "2025-01-02", 10.0)
    account.strategic_cohort_symbols = [symbol]
    account.strategic_cohort_targets = {symbol: 0.6}
    account.strategic_restore_weights = {symbol: 0.6}
    account.protected_weights = {symbol: 0.6}
    account.candidate_tenure.update(strategic_cohort_active=1, strategic_cohort_started=1)
    account.capital_budget_level = 2
    guarded = replace(
        risk, state=Risk.CRISIS if guard == "crisis" else Risk.NORMAL,
        target_gross_cap=0.6, freeze_new_risk=True,
        evidence={"sentinel_freeze_new_risk": guard == "sentinel"},
    )
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date, opportunity=Opportunity.TREND, risk=guarded,
        user_panel=panel, leaders=leaders, account=account,
        prices={s: 10.0 for s in panel},
    )
    observed = {target.symbol: target.weight for target in targets}
    assert observed[symbol] == pytest.approx(0.6 if guard == "none" else 0.555)
    assert account.cash == 445_000.0 and account.positions[symbol].shares == 55_500
    assert account.capital_budget_level == 2
    if guard == "none":
        assert account.strategic_restore_weights == {symbol: 0.6}
        account.cash = 430_000.0
        account.positions[symbol].shares = 57_000
        remainder = PendingOrder(str(date.date()), symbol, "BUY", 0.6, "saved restoration", "CORE", remaining_shares=3000)
        account.pending_orders = [remainder]
        retried = PortfolioAllocator(DEFAULT_CONFIG).allocate(
            date=panel[symbol].index[-1], opportunity=Opportunity.TREND, risk=guarded,
            user_panel=panel, leaders=leaders, account=account, prices={s: 10.0 for s in panel},
        )
        assert {target.symbol: target.weight for target in retried}[symbol] == pytest.approx(0.6)
        assert account.strategic_restore_weights == {symbol: 0.6}
        assert account.pending_orders == [remainder] and remainder.remaining_shares == 3000


@pytest.mark.parametrize("evidence", ("ready", "missing_leader", "missing_panel", "missing_session"))
def test_retained_partial_core_intent_does_not_requalify_as_a_new_entry(evidence):
    date, panel, leaders, risk = _inputs()
    symbol = "sh600001"
    account = AccountState.empty(1_000_000.0)
    account.cash = 900_000.0
    account.positions[symbol] = Position(symbol, 10_000, 10.0, "2025-01-02", 10.0)
    account.pending_orders = [PendingOrder(
        str(date.date()), symbol, "BUY", 0.2, "confirmed core admitted from available account capital", "CORE",
    )]
    if evidence == "missing_leader":
        leaders.pop(symbol)
    elif evidence == "missing_panel":
        panel.pop(symbol)
    elif evidence == "missing_session":
        panel[symbol] = panel[symbol].drop(index=date)
    targets = PortfolioAllocator(DEFAULT_CONFIG).allocate(
        date=date, opportunity=Opportunity.TREND, risk=risk,
        user_panel=panel, leaders=leaders, account=account,
        prices={**dict.fromkeys(panel, 10.0), symbol: 10.0},
    )
    assert account.replacement_tenure.get(f"strategic_eligibility:established:{symbol}", 0) < DEFAULT_CONFIG.leader_tenure_days
    assert {target.symbol: target.weight for target in targets}[symbol] == pytest.approx(0.2 if evidence == "ready" else 0.1)
    assert account.cash == 900_000.0 and account.positions[symbol].shares == 10_000


def test_confirmed_new_core_uses_spare_capital_without_trimming_incumbent(monkeypatch):
    _, _, targets = _evaluate(monkeypatch)
    assert targets["sh600001"].weight == 0.6
    assert any(t.weight >= 0.2 for s, t in targets.items() if s != "sh600001")
    assert sum(t.weight for t in targets.values()) <= 1.0
    assert all(not t.grant_id and not t.epoch_id for s, t in targets.items() if s != "sh600001")


@pytest.mark.parametrize("constraint", ("freeze", "reserved", "missing_correlation"))
def test_unusable_capital_cannot_fund_an_unrelated_new_core(monkeypatch, constraint):
    if constraint == "missing_correlation":
        original = _inputs

        def missing():
            date, panel, leaders, risk = original()
            panel["sh600002"] = panel["sh600002"].tail(10)
            panel["sh600003"] = panel["sh600003"].tail(10)
            return date, panel, leaders, risk

        monkeypatch.setattr(__import__(__name__), "_inputs", missing)
    _, _, targets = _evaluate(monkeypatch, frozen=constraint == "freeze", pending=constraint == "reserved")
    assert targets["sh600001"].weight == 0.6
    assert targets.get("sh600002", replace(targets["sh600001"], weight=0.0)).weight == 0.0


def test_strategic_exit_does_not_close_an_unrelated_ordinary_holding(monkeypatch):
    date, panel, leaders, risk = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1000.0)
    account.cash = 0.0
    account.positions = {s: Position(s, 50, 8.0, "2025-01-02", 10.0) for s in list(panel)[:2]}
    account.strategic_cohort_symbols = ["sh600001"]
    monkeypatch.setattr(
        policy,
        "_strategic_cohort_targets",
        lambda **kwargs: tuple(
            Target(s, 0.0, "CORE", 0.9, 0.9, "strategic completed") for s in account.positions
        ),
    )
    targets = allocate_strategy(
        policy,
        date=date,
        opportunity=Opportunity.CHOPPY,
        risk=risk,
        user_panel=panel,
        leaders=leaders,
        account=account,
        prices={s: 10.0 for s in panel},
    )
    assert {t.symbol: t.weight for t in targets} == {"sh600001": 0.0, "sh600002": 0.5}


def test_correlated_increment_is_capped_against_incumbent_exposure():
    date, panel, leaders, _ = _inputs()
    panel["sh600002"] = panel["sh600001"].copy()
    room = admission_room(
        cfg=DEFAULT_CONFIG,
        symbol="sh600002",
        committed={"sh600001": 0.7},
        leaders=leaders,
        user_panel=panel,
        date=date,
        gross_cap=1.0,
    )
    assert room == pytest.approx(0.05)


@pytest.mark.parametrize("ordinary_weight", (0.3, 0.6))
def test_strategic_increase_cannot_take_an_ordinary_incumbents_capital(monkeypatch, ordinary_weight):
    date, panel, leaders, risk = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1000.0)
    account.cash = 1000.0 * (0.7 - ordinary_weight)
    account.positions = {
        "sh600001": Position("sh600001", 30, 8.0, "2025-01-02", 10.0),
        "sh600002": Position("sh600002", int(ordinary_weight * 100), 8.0, "2025-01-02", 10.0),
    }
    account.strategic_cohort_symbols = ["sh600001"]
    leaders["sh600002"] = replace(leaders["sh600002"], industry=leaders["sh600001"].industry)
    monkeypatch.setattr(policy, "_strategic_cohort_targets", lambda **kwargs: (
        Target("sh600001", 0.6, "CORE", 0.9, 0.9, "strategic restore"),
    ))
    targets = allocate_strategy(
        policy, date=date, opportunity=Opportunity.CHOPPY, risk=risk,
        user_panel=panel, leaders=leaders, account=account, prices={s: 10.0 for s in panel},
    )
    weights = {target.symbol: target.weight for target in targets}
    assert weights["sh600002"] == pytest.approx(ordinary_weight)
    assert weights["sh600001"] == pytest.approx(max(0.3, 0.75 - ordinary_weight))
    assert sum(weights.values()) <= 1.0


def test_confirmed_transfer_reduces_saved_rights_without_creating_cash():
    date, panel, leaders, _ = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1000.0)
    account.cash = 0.0
    account.positions["sh600001"] = Position("sh600001", 100, 10.0, "2025-01-02", 10.0)
    account.strategic_cohort_targets = {"sh600001": 1.0}
    account.strategic_restore_weights = {"sh600001": 0.9}
    account.protected_weights = {"sh600001": 1.0}
    panel["sh600001"].loc[:, ["close", "ma20", "ret20"]] = [8.0, 10.0, -0.2]
    leaders["sh600001"] = replace(leaders["sh600001"], score=0.2, mature=False)
    proposed = {"sh600001": 1.0}
    for day in panel["sh600001"].index[-3:]:
        owner = _degraded_transfer(
            policy,
            challenger="sh600002",
            proposed=proposed,
            weights_now={"sh600001": 1.0},
            leaders=leaders,
            user_panel=panel,
            date=day,
            account=account,
        )
    assert owner == "sh600001"
    assert proposed["sh600001"] == pytest.approx(0.7)
    assert account.cash == 0.0
    assert account.positions["sh600001"].shares == 100
    assert account.strategic_restore_weights["sh600001"] == pytest.approx(0.7)
    assert account.protected_weights["sh600001"] == pytest.approx(0.7)
    assert _degraded_transfer(
        policy, challenger="sh600002", proposed=proposed, weights_now={"sh600001": 1.0},
        leaders=leaders, user_panel=panel, date=date, account=account,
    ) is None
    assert proposed["sh600001"] == pytest.approx(0.7)
    assert len(account.rotation_dates) == 1
    account.pending_orders = [PendingOrder(str(date.date()), "sh600001", "SELL", 0.7, "rotation", "CORE")]
    assert (
        _degraded_transfer(
            policy,
            challenger="sh600002",
            proposed=proposed,
            weights_now={"sh600001": 1.0},
            leaders=leaders,
            user_panel=panel,
            date=date,
            account=account,
        )
        is None
    )
    assert proposed["sh600001"] == pytest.approx(0.7)


def test_strategic_grace_cannot_hide_an_unrelated_positions_damage():
    account = AccountState.empty(1000.0)
    account.strategic_epoch = 1
    account.candidate_tenure["strategic_early_cycle_epoch"] = 1
    account.strategic_cohort_symbols = ["sh600001"]
    account.positions["sh600001"] = Position("sh600001", 50, 10.0)
    assert strategic_grace_supported(account=account)
    account.positions["sh600002"] = Position("sh600002", 10, 10.0)
    assert not strategic_grace_supported(account=account)


def test_transfer_requires_consecutive_sessions_and_same_day_is_idempotent():
    date, panel, leaders, _ = _inputs()
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    account = AccountState.empty(1000.0)
    account.cash = 0.0
    account.positions["sh600001"] = Position("sh600001", 100, 10.0, "2025-01-02", 10.0)
    panel["sh600001"].loc[:, ["close", "ma20", "ret20"]] = [8.0, 10.0, -0.2]
    leaders["sh600001"] = replace(leaders["sh600001"], score=0.2)
    proposed = {"sh600001": 1.0}
    sessions = panel["sh600001"].index
    for observed in (sessions[-5], sessions[-4], date, date):
        assert _degraded_transfer(
            policy, challenger="sh600002", proposed=proposed, weights_now={"sh600001": 1.0},
            leaders=leaders, user_panel=panel, date=observed, account=account,
        ) is None
    assert proposed["sh600001"] == 1.0
    assert account.replacement_tenure["core_transfer:sh600001->sh600002"] == 1


def test_partial_rotation_retry_keeps_one_order_and_event_after_restart(monkeypatch, tmp_path):
    date, panel, leaders, risk = _inputs()
    names = dict(zip(panel, ("sh688008", "sh688012", "sh688200"), strict=True))
    panel = {names[s]: frame for s, frame in panel.items()}
    leaders = {names[s]: replace(score, symbol=names[s]) for s, score in leaders.items()}
    dates = panel["sh688008"].index
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    signal = str(dates[-3].date())
    initial_targets = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
        targets=(Target(
            "sh688008", 0.2, "CORE", 0.9, 0.9, "leader rotation after prior reduction filled",
            origin_subsystem="LEADER", mechanism="LEADER_ROTATION", origin_lifecycle="CORE",
            replaces_symbol="sh688012",
        ),),
    )
    prices = {s: 10.0 for s in panel}
    planned = plan_orders(signal_date=signal, targets=initial_targets, account=account,
                          prices=prices, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=planned, submitted_date=signal
    ))
    execution_panel = {"sh688008": pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
         "volume": [1_000_000.0, 1_000_000.0, 10_000_000.0], "amount": 100_000_000.0},
        index=dates[-3:],
    )}
    first_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[-2], account=account, panel=execution_panel
    )
    assert len(first_fills) == 1 and account.pending_orders
    original_order = account.pending_orders[0]
    original_identity = (original_order.order_id, original_order.event_id)
    original_quantity = account.order_ledger[0].requested_shares
    account.replacement_tenure["strategic_eligibility:established:sh688008"] = DEFAULT_CONFIG.leader_tenure_days
    policy = PortfolioAllocator(DEFAULT_CONFIG)
    monkeypatch.setattr(policy, "_strategic_cohort_targets", lambda **kwargs: None)
    targets = policy.allocate(date=dates[-2], opportunity=Opportunity.TREND, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices=prices)
    resumed_target = next(t for t in targets if t.symbol == "sh688008")
    assert resumed_target.mechanism == "LEADER_ROTATION"
    assert resumed_target.replaces_symbol == "sh688012"
    assert resumed_target.event_id == original_identity[1]
    next_signal = str(dates[-2].date())
    planned = plan_orders(signal_date=next_signal, targets=targets, account=account,
                          prices=prices, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=account.pending_orders, current=planned, submitted_date=next_signal
    ))
    assert len(account.order_ledger) == 1
    assert (account.pending_orders[0].order_id, account.pending_orders[0].event_id) == original_identity
    state_path = tmp_path / "partial-rotation.json"
    save_account(account, state_path)
    resumed = load_account(state_path)
    final_fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=date, account=resumed, panel=execution_panel
    )
    assert len(final_fills) == 1 and not resumed.pending_orders
    assert {f.order_id for f in resumed.fills} == {original_identity[0]}
    assert {f.event_id for f in resumed.fills} == {original_identity[1]}
    assert sum(f.shares for f in resumed.fills) == original_quantity
    save_account(resumed, state_path)
    assert load_account(state_path).to_dict() == resumed.to_dict()


def test_partial_ordinary_restore_survives_restart_without_new_entry_qualification(tmp_path):
    date, panel, leaders, risk = _inputs()
    symbol = "sh688008"
    panel = {symbol: panel["sh600001"]}
    leaders = {symbol: replace(leaders["sh600001"], symbol=symbol, mature=False)}
    dates = panel[symbol].index
    signal = str(dates[-3].date())
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.code_hash, account.data_hash = "code:fixture", "data:fixture"
    entry_signal = str(dates[-5].date())
    entry = attach_target_attribution(
        "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=entry_signal,
        targets=(Target(symbol, 0.2, "CORE", 0.9, 0.9, "prior core entry",
                        origin_subsystem="LEADER", mechanism="LEADER_SELECTION",
                        origin_lifecycle="CORE"),),
    )
    entry_orders = plan_orders(signal_date=entry_signal, targets=entry, account=account,
                               prices={symbol: 10.0}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=entry_orders, submitted_date=entry_signal,
    ))
    planner = ExecutionPlanner(DEFAULT_CONFIG)
    initial_panel = {symbol: pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
         "volume": 100_000_000.0, "amount": 1_000_000_000.0}, index=dates[-5:-2],
    )}
    assert planner.execute_open(date=dates[-4], account=account, panel=initial_panel)
    assert not account.pending_orders
    account.protected_weights[symbol] = 0.6
    policy = PortfolioAllocator(DEFAULT_CONFIG)

    def allocate(day, state):
        return policy.allocate(
            date=day, opportunity=Opportunity.RECOVERY, risk=risk,
            user_panel=panel, leaders=leaders, account=state, prices={symbol: 10.0},
        )

    targets = attach_target_attribution(
        "semiconductor", REQUIRED_AI_UNIVERSE_SHA256, signal_date=signal,
        targets=allocate(dates[-3], account),
    )
    assert targets[0].weight == pytest.approx(0.6)
    assert targets[0].origin_subsystem == "RECOVERY"
    assert targets[0].mechanism == "POST_SHOCK_RESTORATION"
    planned = plan_orders(signal_date=signal, targets=targets, account=account,
                          prices={symbol: 10.0}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=[], current=planned, submitted_date=signal,
    ))
    execution_panel = {symbol: pd.DataFrame(
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
         "volume": [1_000_000.0, 1_000_000.0, 100_000_000.0],
         "amount": [100_000_000.0, 100_000_000.0, 1_000_000_000.0]},
        index=dates[-3:],
    )}
    assert planner.execute_open(date=dates[-2], account=account, panel=execution_panel)
    assert len(account.pending_orders) == 1
    original = account.pending_orders[0]
    original_quantity = account.order_ledger[-1].requested_shares
    remaining = allocate(dates[-2], account)
    assert remaining[0].weight == pytest.approx(0.6)
    assert remaining[0].event_id == original.event_id
    observed = risk.evidence["core_allocation"]["symbols"][symbol]
    assert observed["entry"]["block"] == "NOT_MATURE"
    assert observed["allocation_reason"] == "POST_SHOCK_RESTORATION"
    assert observed["budget_checks"][-1]["reserved_for_intent"] > 0
    assert account.candidate_tenure.get(f"core_restored:{symbol}", -1) == -1
    next_signal = str(dates[-2].date())
    planned = plan_orders(signal_date=next_signal, targets=remaining, account=account,
                          prices={symbol: 10.0}, cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=account.pending_orders, current=planned,
        submitted_date=next_signal,
    ))
    path = tmp_path / "partial-ordinary-restore.json"
    save_account(account, path)
    resumed = load_account(path)
    assert planner.execute_open(date=date, account=resumed, panel=execution_panel)
    assert not resumed.pending_orders
    assert len(resumed.order_ledger) == 2
    restore_fills = [fill for fill in resumed.fills if fill.order_id == original.order_id]
    assert len(restore_fills) == 2
    assert sum(fill.shares for fill in restore_fills) == original_quantity
    completed = allocate(date, resumed)
    assert resumed.candidate_tenure[f"core_restored:{symbol}"] == 0
    assert plan_orders(signal_date=str(date.date()), targets=completed, account=resumed,
                       prices={symbol: 10.0}, cfg=DEFAULT_CONFIG) == ()
    save_account(resumed, path)
    assert load_account(path).to_dict() == resumed.to_dict()


def test_order_planning_explains_a_real_no_trade_band_without_changing_intents():
    account = AccountState.empty(1_000_000.0)
    account.positions["sh688008"] = Position("sh688008", 20_000, 10.0)
    account.cash = 800_000.0
    target = Target("sh688008", .21, "CORE", .9, .9, "small change")
    diagnostics = {}
    before = account.to_dict()
    arguments = dict(signal_date="2025-01-06", targets=(target,), account=account,
                     prices={"sh688008": 10.0}, cfg=DEFAULT_CONFIG)
    assert plan_orders(**arguments) == plan_orders(**arguments, diagnostics=diagnostics) == ()
    assert diagnostics["sh688008"] == {
        "block": "NO_TRADE_BAND", "difference_value": 10_000.0,
        "standard_trade_threshold": DEFAULT_CONFIG.min_trade_weight * 1_000_000.0,
        "restoration_exception": False,
    }
    assert account.to_dict() == before


@pytest.mark.parametrize("carried_event", (False, True))
def test_binding_budget_reduction_cannot_retain_a_larger_pending_buy(carried_event):
    original = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2025-07-28",
        targets=(Target(
            "sz300308", 1 / 3, "CORE", 0.9, 0.9, "confirmed entry",
            origin_subsystem="LEADER", mechanism="LEADER_SELECTION", origin_lifecycle="CORE",
        ),),
    )[0]
    retained = PendingOrder(
        "2025-07-28", original.symbol, "BUY", original.weight, original.reason, original.lifecycle,
        event_id=original.event_id, origin_subsystem=original.origin_subsystem,
        mechanism=original.mechanism, origin_lifecycle=original.origin_lifecycle,
        industry_at_entry=original.industry_at_entry,
        industry_manifest_sha256=original.industry_manifest_sha256,
    )
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.pending_orders = [retained]
    capped = replace(original, weight=0.30, event_id=original.event_id if carried_event else "")
    targets = attach_target_attribution(
        "optical", REQUIRED_AI_UNIVERSE_SHA256, signal_date="2025-07-29",
        targets=(capped,), retained_orders=account.pending_orders,
    )
    assert targets[0].event_id != original.event_id
    planned = plan_orders(
        signal_date="2025-07-29", targets=targets, account=account,
        prices={original.symbol: 10.0}, cfg=DEFAULT_CONFIG,
    )
    assert len(planned) == 1 and planned[0] is not retained
    assert planned[0].target_weight == 0.30
