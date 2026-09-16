"""Native route-scoped ordinary impulse permission; no injected authority."""
from dataclasses import replace
from hashlib import sha256

import pandas as pd
import pytest
from test_lifecycle_and_risk import _leader, _strategic_frame
from test_strategic_universe_quorum import _risk

from uquant.application.target_attribution import attach_target_attribution
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner, merge_pending_orders, plan_orders, reconcile_account_orders
from uquant.leader import apply_leader_tenure
from uquant.portfolio import PortfolioAllocator
from uquant.types import AccountState, Opportunity
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256, default_ai_universe


def _scenario():
    dates = pd.bdate_range("2023-01-02", periods=260)
    universe = default_ai_universe()
    as_of = str(dates[-9].date())
    groups = {}
    for symbol in sorted(universe.symbols_as_of(as_of)):
        groups.setdefault(universe.industry_of(symbol, as_of), symbol)
    symbols = tuple(groups.values())[:2]
    panel = {symbol: _strategic_frame(dates) for symbol in symbols}
    for frame in panel.values():
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] * 1.01
        frame["low"] = frame["close"] * .99
        frame["volume"] = 100_000_000.
        frame["amount"] = frame["volume"] * frame["close"]
    base = {symbol: _leader(symbol, .86 - index * .02,
                           industry=universe.industry_of(symbol, as_of))
            for index, symbol in enumerate(symbols)}
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.account_identity = "account:ordinary-trend-native"
    account.code_hash = "code:ordinary-trend-native"
    account.data_hash = sha256("".join(f.to_csv() for f in panel.values()).encode()).hexdigest()
    risk = _risk()
    risk.evidence.update(broad_ret120=.04, tech_ret120=-.001, tech_ret20=-.01,
                         ai_fast_return=.16, declining_ratio=.05,
                         below_ma20_ratio=.05, tech_speed=.16, broad_speed=.02)
    return PortfolioAllocator(DEFAULT_CONFIG), account, dates[-9:], panel, base, risk


def _decide(policy, account, date, panel, leaders, risk, *, roles=None):
    previous = list(account.pending_orders)
    prices = {symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()}
    targets = policy.allocate(date=date, opportunity=Opportunity.TREND, risk=risk,
                              user_panel=panel, leaders=leaders, account=account, prices=prices,
                              **({"qualification_panel": panel, "qualification_leaders": leaders,
                                  "strategic_universe": roles} if roles is not None else {}))
    attributed = tuple(item for target in targets for item in attach_target_attribution(
        leaders[target.symbol].industry, REQUIRED_AI_UNIVERSE_SHA256,
        signal_date=str(date.date()), targets=(target,), retained_orders=previous,
    ))
    planned = plan_orders(signal_date=str(date.date()), targets=attributed, account=account,
                          prices=prices, cfg=DEFAULT_CONFIG)
    merged = merge_pending_orders(retained=previous, planned=planned, targets=attributed,
                                  cfg=DEFAULT_CONFIG)
    account.pending_orders = list(reconcile_account_orders(
        account=account, previous=previous, current=merged, submitted_date=str(date.date()),
    ))
    return targets


def _confirmed_open():
    policy, account, dates, panel, base, risk = _scenario()
    for index, date in enumerate(dates[:5]):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        targets = _decide(policy, account, date, panel, leaders, risk)
        if index < 4:
            assert not account.pending_orders
    assert all(leader.mature for leader in leaders.values())
    assert all(account.leader_tenure[symbol] >= DEFAULT_CONFIG.leader_tenure_days for symbol in base)
    assert all(account.replacement_tenure.get(f"strategic_eligibility:independent_core:{symbol}", 0) == 0
               for symbol in base)
    return policy, account, dates, panel, leaders, risk, targets


def test_equal_nominal_trend_budget_preserves_actual_correlation_cap_and_unused_cash():
    _, account, dates, panel, leaders, risk, targets = _confirmed_open()
    assert {target.symbol: target.weight for target in targets} == pytest.approx(
        dict(zip(leaders, (.40, .35), strict=True)))
    trace = risk.evidence["core_allocation"]
    checks = [trace["symbols"][symbol]["budget_checks"][-1] for symbol in leaders]
    assert all(check["desired_increment"] == DEFAULT_CONFIG.trend_entry_gross / 2 for check in checks)
    assert checks[1]["industry_room"] == .75
    assert checks[1]["correlation_room"] == pytest.approx(.35)
    assert set(checks[1]["correlation_cluster"]) == set(leaders)
    assert trace["unreserved_cash_after"] == pytest.approx(.25)
    assert all(target.weight <= DEFAULT_CONFIG.single_core_entry_cap for target in targets)
    assert account.strategic_grant is None and not account.strategic_epochs
    assert len(account.pending_orders) == 2
    assert all(order.side == "BUY" and order.grant_id == order.epoch_id == ""
               for order in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 2 and all(fill.side == "BUY" and fill.shares > 0 for fill in fills)
    assert set(account.positions) == set(leaders)
    assert account.cash > DEFAULT_CONFIG.initial_cash * .24
    assert not account.pending_orders


def test_ordinary_partial_loses_common_permission_and_cannot_revive_after_strict_restart(tmp_path):
    from uquant.account import load_account, save_account

    policy, account, dates, panel, leaders, risk, _ = _confirmed_open()
    assert len(account.pending_orders) == 2
    for frame in panel.values():
        frame.loc[dates[5], "volume"] = 1_000_000.
        frame.loc[dates[5], "amount"] = 1_000_000. * frame.loc[dates[5], "close"]
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[5], account=account, panel=panel)
    assert len(fills) == 2 and account.pending_orders
    assert all(order.status == "PARTIALLY_FILLED" for order in account.order_ledger)
    before = ({symbol: pos.shares for symbol, pos in account.positions.items()}, account.cash)
    risk.evidence["ai_fast_return"] = .01
    assert all(leader.mature for leader in leaders.values())
    _decide(policy, account, dates[5], panel, leaders, risk)
    assert not account.pending_orders
    assert all(order.status == "CANCELLED" for order in account.order_ledger)
    path = tmp_path / "account.json"
    save_account(account, path)
    restored = load_account(path)
    _decide(policy, restored, dates[6], panel, leaders, risk)
    assert not restored.pending_orders
    assert not ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[7], account=restored, panel=panel)
    assert ({symbol: pos.shares for symbol, pos in restored.positions.items()}, restored.cash) == before
    assert restored.strategic_grant is None and not restored.strategic_epochs


def test_reference_only_credible_name_cannot_supply_impulse_permission():
    policy, account, dates, panel, base, risk = _scenario()
    for _ in range(DEFAULT_CONFIG.leader_tenure_days):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    reference = tuple(panel)[-1]
    panel.pop(reference)
    tradable = next(iter(panel))
    leaders[tradable] = replace(leaders[tradable], score=.81)
    result = _observe(policy, account, dates[0], panel, leaders, risk)
    assert not result["confirmed"] and not result["credible_symbols"]
    assert not _decide(policy, account, dates[0], panel, leaders, risk)
    assert not account.pending_orders


def test_real_consumed_repair_partial_cannot_use_mature_common_permission(tmp_path):
    from test_ordinary_cash_rearm import SYMBOL, _allocate, _roles
    from test_ordinary_cash_rearm import _decide as repair_decide
    from test_ordinary_cash_rearm import _scenario as repair_scenario

    from uquant.account import load_account, save_account
    from uquant.portfolio.strategic import rearm

    policy, account, dates, panel, base, risk = repair_scenario(sessions=0)
    for date in dates[:19]:
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
        rearm.observe_flat_book_capital_repair_state(
            account=account, risk=risk, universe=_roles(date),
            observed_session=str(date.date()), cfg=DEFAULT_CONFIG,
        )
        assert not _allocate(policy, account, date, panel, leaders, risk)
    leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    repair_decide(policy, account, dates[19], panel, leaders, risk)
    assert len(account.pending_orders) == 1
    original = account.pending_orders[0]
    reference = account.strategic_cash_rearm.consumed_order
    assert (reference.order_id, reference.event_id) == (original.order_id, original.event_id)
    frame = panel[SYMBOL]
    frame.loc[dates[20], "volume"] = 100_000.
    frame.loc[dates[20], "amount"] = 100_000. * frame.loc[dates[20], "close"]
    account.data_hash = sha256(frame.to_csv().encode()).hexdigest()
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[20], account=account, panel=panel)
    assert len(fills) == 1 and account.pending_orders
    assert account.order_ledger[0].status == "PARTIALLY_FILLED"
    before = (account.positions[SYMBOL].shares, account.cash)
    risk = replace(risk, freeze_new_risk=False, evidence={
        **risk.evidence, "freeze_new_risk": False, "broad_ret20": -.20,
        "broad_ret120": .04, "tech_ret120": -.001, "ai_fast_return": .16,
        "declining_ratio": .05, "below_ma20_ratio": .05, "tech_speed": .16, "broad_speed": .02,
    })
    assert leaders[SYMBOL].mature and account.leader_tenure[SYMBOL] >= 5
    repair_decide(policy, account, dates[20], panel, leaders, risk)
    assert account.replacement_tenure.get(f"strategic_eligibility:independent_core:{SYMBOL}", 0) == 0
    assert not account.pending_orders
    path = tmp_path / "repair-account.json"
    save_account(account, path)
    restored = load_account(path)
    repair_decide(policy, restored, dates[21], panel, leaders, risk)
    assert not restored.pending_orders
    assert (restored.positions[SYMBOL].shares, restored.cash) == before
    assert restored.strategic_grant is None and not restored.strategic_epochs


def _sustained_scenario():
    policy, account, dates, panel, base, risk = _scenario()
    for _ in range(DEFAULT_CONFIG.leader_tenure_days):
        leaders = apply_leader_tenure(base, account=account, cfg=DEFAULT_CONFIG)
    risk.evidence.update(broad_ret120=.04, tech_ret120=.04, ai_fast_return=.01)
    return policy, account, dates, panel, leaders, risk


def _observe(policy, account, date, panel, leaders, risk):
    from uquant.portfolio.ordinary import observe_ordinary_market

    return observe_ordinary_market(policy, date=date, opportunity=Opportunity.STRONG_TREND,
                                   risk=risk, leaders=leaders, user_panel=panel)


def test_current_impulse_is_nonpersistent_across_dates_and_strict_restart(tmp_path):
    from uquant.account import load_account, save_account

    policy, account, dates, panel, leaders, risk = _sustained_scenario()
    risk.evidence["ai_fast_return"] = .16
    before = dict(account.candidate_tenure)
    observed = _observe(policy, account, dates[0], panel, leaders, risk)
    assert observed["confirmed"]
    assert _observe(policy, account, dates[0], panel, leaders, risk) == observed
    assert account.candidate_tenure == before
    path = tmp_path / "market-permission.json"
    save_account(account, path)
    account = load_account(path)
    risk.evidence["ai_fast_return"] = .01
    assert not _observe(policy, account, dates[4], panel, leaders, risk)["confirmed"]
    assert account.candidate_tenure == before


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_missing_or_nonfinite_market_evidence_cannot_keep_confirmation(bad):
    policy, account, dates, panel, leaders, risk = _sustained_scenario()
    risk.evidence["ai_fast_return"] = .16
    assert _observe(policy, account, dates[0], panel, leaders, risk)["confirmed"]
    if bad is None:
        risk.evidence.pop("tech_speed")
    else:
        risk.evidence["tech_speed"] = bad
    result = _observe(policy, account, dates[3], panel, leaders, risk)
    assert not result["confirmed"]
    assert not _decide(policy, account, dates[3], panel, leaders, risk)
    assert not account.pending_orders
    assert "tech_speed" in result["missing_market_fields"]


def test_freeze_preserves_true_market_observations_but_prevents_real_buy():
    policy, account, dates, panel, leaders, risk = _sustained_scenario()
    risk.evidence["ai_fast_return"] = .16
    before = dict(account.candidate_tenure)
    frozen = replace(risk, freeze_new_risk=True)
    for date in dates[:3]:
        observed = _observe(policy, account, date, panel, leaders, frozen)
        targets = policy.allocate(
            date=date, opportunity=Opportunity.STRONG_TREND, risk=frozen,
            user_panel=panel, leaders=leaders, account=account,
            prices={symbol: float(frame.loc[date, "close"]) for symbol, frame in panel.items()},
        )
        assert not targets and not account.pending_orders
    assert observed["confirmed"]
    assert not {k for k in account.candidate_tenure if k.startswith("ordinary_market_")}
    assert not {k for k in before if k.startswith("ordinary_market_")}
    assert not account.order_ledger and not account.fills and not account.positions
    assert account.cash == DEFAULT_CONFIG.initial_cash


def test_sustained_market_cannot_accumulate_early_mature_entry_permission():
    policy, account, dates, panel, leaders, risk = _sustained_scenario()
    before = dict(account.candidate_tenure)
    for date in dates[:3]:
        observed = _observe(policy, account, date, panel, leaders, risk)
        assert not observed["confirmed"]
    assert account.candidate_tenure == before
