"""Bounded CORE profit protection uses actual entry and the existing MFE controls."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from test_strategic_probe_holding import OWNER, _decide_and_submit, _entry_deteriorated, _filled_probe
from test_strategic_universe_quorum import _risk

from uquant.account.codec import account_from_dict
from uquant.application.decision import mark_account_positions
from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner
from uquant.portfolio_core import strategic_dominant_symbol


def _marked_core(*, partial=False, crosses=True):
    allocator, account, dates, panel, leaders, roles = _filled_probe(partial=partial)
    threshold = DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe
    price = account.positions[OWNER].avg_cost * (1 + threshold + (.10 if crosses else -.10))
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    runtime = SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    )
    mark_account_positions(runtime, account, dates[0])
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    assert account.strategic_epoch == 0
    assert strategic_dominant_symbol(account) is None
    return allocator, account, dates, panel, _entry_deteriorated(leaders), roles


def _bounded_cap(account):
    first = next(fill for fill in account.fills if fill.side == "BUY" and fill.shares > 0)
    admission = next(order for order in account.order_ledger if order.order_id == first.order_id)
    assert admission.event_id == first.event_id
    assert admission.grant_id == first.grant_id == account.strategic_grant.grant_id
    assert admission.epoch_id == first.epoch_id == account.strategic_epochs[0].epoch_id
    return min(DEFAULT_CONFIG.strategic_dominant_retained_gross, admission.target_weight)


def test_completed_core_locks_once_at_original_entry_budget_without_completing_deployment():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    cap = _bounded_cap(account)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(cap)
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    assert account.strategic_epoch == 0
    assert strategic_dominant_symbol(account) is None
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    shares = account.positions[OWNER].shares
    # A further healthy price rise must not turn this one-time action into
    # repeated weight rebalancing, either live or after account round-trip.
    for column in ("open", "close", "high", "low", "ma20", "ma60"):
        panel[OWNER].loc[dates[2]:, column] *= 1.20
    assert not _decide_and_submit(allocator, account, dates[2], panel, leaders, roles)
    assert not _decide_and_submit(allocator, account, dates[2], panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[3], panel, leaders, roles)
    assert restored.positions[OWNER].shares == shares
    assert restored.strategic_epochs[0].realized_status == "ACTIVE"
    assert restored.strategic_epoch == 0


def test_completed_core_below_existing_mfe_threshold_keeps_healthy_holding():
    allocator, account, dates, panel, leaders, roles = _marked_core(crosses=False)
    shares = account.positions[OWNER].shares
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert account.positions[OWNER].shares == shares


def test_independent_risk_cap_precedes_new_core_profit_lock():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    lower_cap = _bounded_cap(account) / 2
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles,
                               risk=replace(_risk(), target_gross_cap=lower_cap, freeze_new_risk=True))
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= lower_cap


@pytest.mark.parametrize("incomplete", ("partial", "in-flight"))
def test_incomplete_core_entry_cannot_arm_profit_lock(incomplete):
    allocator, account, dates, panel, leaders, roles = _marked_core(partial=incomplete == "partial")
    if incomplete == "in-flight":
        unresolved = deepcopy(account.order_ledger[0])
        unresolved.order_id = "in-flight-core-entry"
        unresolved.status = "OPEN"
        unresolved.remaining_shares = 100
        account.order_ledger.append(unresolved)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert not orders


def test_ordinary_core_without_strategic_epoch_does_not_gain_profit_lock():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    account.strategic_epochs.clear()
    account.strategic_grant = None
    account.positions[OWNER].epoch_id = ""
    account.positions[OWNER].grant_id = ""
    for tranche in account.positions[OWNER].tranches:
        tranche.epoch_id = ""
        tranche.grant_id = ""
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)


def test_revoked_core_grant_retains_a_lower_existing_target():
    allocator, account, dates, panel, leaders, roles = _marked_core()
    assert account.strategic_grant is not None
    account.strategic_grant.status = "EXPIRED"
    lower_target = _bounded_cap(account) / 2
    account.strategic_cohort_targets[OWNER] = lower_target
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight <= lower_target


@pytest.mark.parametrize("cancelled", (False, True), ids=("unfilled", "broker-cancelled"))
def test_unexecuted_core_profit_lock_retries_after_restart(cancelled):
    from uquant.broker import sync_broker_snapshot

    allocator, account, dates, panel, leaders, roles = _marked_core()
    cap = _bounded_cap(account)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    old_order_id = orders[0].order_id
    if cancelled:
        position = account.positions[OWNER]
        sync_broker_snapshot(account, {
            "as_of": str(dates[1].date()), "cash": account.cash, "fills": [],
            "orders": [{"order_id": old_order_id, "status": "CANCELLED", "remaining_shares": 0}],
            "positions": [{"symbol": OWNER, "shares": position.shares,
                           "sellable_shares": position.shares, "avg_cost": position.avg_cost}],
        }, cfg=DEFAULT_CONFIG)
        assert not account.pending_orders
    restored = account_from_dict(asdict(account))
    orders = _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(cap)
    assert (orders[0].order_id != old_order_id) == cancelled
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[3], account=restored, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].side == "SELL" and fills[0].shares > 0
    assert not _decide_and_submit(allocator, restored, dates[4], panel, leaders, roles)


def _marked_after_band_sale(*, fresh_atr=False):
    from test_strategic_exit_band_settlement import _band_sale

    allocator, account, dates, panel, leaders, roles = _band_sale()
    price = account.positions[OWNER].avg_cost * (1 + DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe + 1.0)
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    mark_account_positions(SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    ), account, dates[0])
    if fresh_atr:
        panel[OWNER].loc[dates[1]:, ["open", "close", "high", "low"]] = price - 1
        panel[OWNER].loc[dates[1]:, "ma20"] = price
        panel[OWNER].loc[dates[1]:, "ret20"] = -.05
    return allocator, account, dates, panel, leaders, roles


@pytest.mark.parametrize("fresh_atr", (False, True), ids=("settled-atr", "fresh-atr"))
def test_settled_tighter_atr_plan_satisfies_soft_profit_protection(fresh_atr):
    allocator, account, dates, panel, leaders, roles = _marked_after_band_sale(fresh_atr=fresh_atr)
    old_bands = list(account.strategic_exit_bands[OWNER])
    old_fills = deepcopy(account.fills)
    assert 0 < sum(old_bands) < _bounded_cap(account)
    assert not _decide_and_submit(allocator, account, dates[1], panel, leaders, roles)
    restored = account_from_dict(asdict(account))
    assert not _decide_and_submit(allocator, restored, dates[2], panel, leaders, roles)
    assert restored.strategic_exit_bands[OWNER] == old_bands
    assert restored.fills == old_fills
    assert not any(order.mechanism == "STRATEGIC_PROFIT_LOCK" for order in restored.order_ledger)


@pytest.mark.parametrize("damage", ("partial", "pending", "wrong-event", "tighter-unexecuted-target"))
def test_unsettled_atr_cannot_discharge_soft_profit_responsibility(damage):
    from uquant.portfolio.strategic.lifecycle import _arm_completed_core_profit_lock

    allocator, account, _, _, _, _ = _marked_after_band_sale()
    sale = account.order_ledger[-1]
    if damage == "partial":
        sale.status = "PARTIALLY_FILLED"
        sale.remaining_shares = 100
    elif damage == "pending":
        account.pending_orders.append(deepcopy(sale))
    elif damage == "wrong-event":
        account.fills[-1].event_id = "wrong-event"
    else:
        account.strategic_exit_bands[OWNER] = [band / 2 for band in account.strategic_exit_bands[OWNER]]
    # Isolate arming from the execution planner: unresolved execution still
    # owns its existing order; this must not claim a second actual submission.
    context = SimpleNamespace(account=account, policy=allocator, weights_now={OWNER: .5},
                              core_profit_lock_symbol=None)
    _arm_completed_core_profit_lock(context, symbol=OWNER,
                                   peak_mfe=DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe + 1)
    assert context.core_profit_lock_symbol == OWNER



def test_settled_looser_atr_does_not_replace_a_stricter_profit_cap():
    allocator, account, dates, panel, leaders, roles = _filled_probe()
    leaders = _entry_deteriorated(leaders)
    price = account.positions[OWNER].avg_cost * 2
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    count = DEFAULT_CONFIG.strategic_cohort_trail_bands
    band_target = .25
    assert band_target > _bounded_cap(account)
    account.strategic_exit_bands[OWNER] = [band_target / count] * count
    account.strategic_active_bands[OWNER] = [False] * count
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].mechanism == "STRATEGIC_TRAILING_EXIT"
    assert orders[0].target_weight == pytest.approx(band_target)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[1], account=account, panel={OWNER: panel[OWNER]},
    )
    assert len(fills) == 1 and fills[0].shares > 0
    from uquant.portfolio.strategic.grant_lifecycle import settled_strategic_reduction

    assert settled_strategic_reduction(account, OWNER, band_target)
    price = account.positions[OWNER].avg_cost * (2 + DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe)
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[2]:, column] = price * factor
    mark_account_positions(SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    ), account, dates[2])
    orders = _decide_and_submit(allocator, account, dates[2], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].mechanism == "STRATEGIC_PROFIT_LOCK"
    assert orders[0].target_weight == pytest.approx(_bounded_cap(account))


def test_historical_mfe_below_current_cap_preserves_fresh_native_promotion():
    from test_lifecycle_and_risk import _leader

    allocator, account, dates, panel, leaders, roles = _marked_core()
    # The high closing mark is real fixture history, but current price recovered
    # into a healthy trend below the recorded initial admission budget.
    price = account.positions[OWNER].avg_cost * .95
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[dates[0]:, column] = price * factor
    shares = account.positions[OWNER].shares
    assert shares * price / (account.cash + shares * price) < _bounded_cap(account)
    assert account.positions[OWNER].highest_close / account.positions[OWNER].avg_cost - 1 \
        > DEFAULT_CONFIG.strategic_dominant_profit_lock_mfe
    assert not _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    leaders = {symbol: _leader(symbol, .95, industry="optical") for symbol in leaders}
    for index, date in enumerate(dates[1:DEFAULT_CONFIG.strategic_one_name_confirm_days + 2], start=1):
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if not orders:
            continue
        assert len(orders) == 1 and orders[0].side == "BUY"
        assert orders[0].mechanism == "STRATEGIC_COHORT"
        assert account.strategic_epochs[0].realized_status == "ACTIVE"
        assert account.strategic_epoch == 0
        fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
            date=dates[index + 1], account=account, panel={OWNER: panel[OWNER]},
        )
        assert len(fills) == 1 and fills[0].side == "BUY" and fills[0].shares > 0
        assert fills[0].mechanism == "STRATEGIC_COHORT"
        assert account.strategic_epochs[0].realized_status == "ACTIVE"
        break
    else:
        pytest.fail("fresh original qualification never authorized a native promotion BUY")


def _actual_budget_probe(budget, full):
    from test_strategic_probe_holding import _allocate, _submit

    from uquant.portfolio import PortfolioAllocator
    from uquant.types import AccountState

    _, _, _, panel, leaders, roles = _filled_probe()
    cfg = DEFAULT_CONFIG.override(core_admission_weight=budget, max_symbol_weight=max(.6, full),
                                  strategic_one_name_gross=full)
    allocator = PortfolioAllocator(cfg)
    account = AccountState.empty(2_000_000.)
    account.account_identity = "account:native-distinct-budget"
    account.code_hash = "source:regression"
    account.data_hash = "data:regression"
    dates = panel[OWNER].index
    for index in range(240, 255):
        targets = _allocate(allocator, account, dates[index], panel, leaders, roles)
        if account.strategic_grant is not None:
            break
    else:
        pytest.fail("native qualification did not produce the requested admission")
    _submit(account, targets, dates[index], panel, cfg)
    fills = ExecutionPlanner(cfg).execute_open(date=dates[index + 1], account=account, panel={OWNER: panel[OWNER]})
    assert len(fills) == 1 and fills[0].shares > 0
    assert account.order_ledger[0].target_weight == pytest.approx(budget)
    assert account.strategic_epochs[0].full_weight == pytest.approx(full)
    assert account.strategic_epochs[0].realized_status == "ACTIVE"
    assert account.strategic_epoch == 0
    remaining = dates[index + 2:]
    price = account.positions[OWNER].avg_cost * (1 + cfg.strategic_dominant_profit_lock_mfe + .1)
    for column, factor in (("open", 1), ("close", 1), ("high", 1.01), ("low", .99),
                           ("ma20", .95), ("ma60", .85)):
        panel[OWNER].loc[remaining[0]:, column] = price * factor
    mark_account_positions(SimpleNamespace(
        _raw=panel, _price=lambda symbol, date: float(panel[symbol].loc[date, "close"]),
    ), account, remaining[0])
    return allocator, account, remaining, panel, _entry_deteriorated(leaders), roles


@pytest.mark.parametrize("budget,full", ((.10, .50), (.20, .55)))
def test_original_native_admission_budget_is_independent_of_planned_full_deployment(budget, full):
    allocator, account, dates, panel, leaders, roles = _actual_budget_probe(budget, full)
    cap = _bounded_cap(account)
    assert cap == pytest.approx(budget)
    old_formula = DEFAULT_CONFIG.strategic_dominant_retained_gross * budget / full
    assert (cap > old_formula) == (full > DEFAULT_CONFIG.strategic_dominant_retained_gross)
    orders = _decide_and_submit(allocator, account, dates[0], panel, leaders, roles)
    assert len(orders) == 1 and orders[0].side == "SELL"
    assert orders[0].target_weight == pytest.approx(cap)
    assert orders[0].mechanism == "STRATEGIC_PROFIT_LOCK"


@pytest.mark.parametrize("missing", ("fill", "ledger", "event", "grant", "epoch", "target", "duplicate"))
def test_completed_core_profit_budget_fails_closed_without_unique_matching_first_fill(missing):
    from uquant.portfolio.strategic.grant_lifecycle import completed_core_admission_budget

    _, account, _, _, _, _ = _marked_core()
    assert completed_core_admission_budget(account) == pytest.approx(_bounded_cap(account))
    if missing == "fill":
        account.fills.clear()
    elif missing == "ledger":
        account.order_ledger.clear()
    elif missing in {"event", "grant", "epoch"}:
        setattr(account.order_ledger[0], missing + "_id", "unrelated-first-fill")
    elif missing == "target":
        account.order_ledger[0].target_weight = float("nan")
    else:
        duplicate = deepcopy(account.order_ledger[0])
        duplicate.target_weight += .1
        account.order_ledger.append(duplicate)
    assert completed_core_admission_budget(account) is None


def test_later_legal_promotion_budget_does_not_rewrite_original_admission_proof():
    from test_lifecycle_and_risk import _leader

    from uquant.portfolio.strategic.grant_lifecycle import completed_core_admission_budget

    allocator, account, dates, panel, leaders, roles = _marked_core(crosses=False)
    initial = _bounded_cap(account)
    leaders = {symbol: _leader(symbol, .95, industry="optical") for symbol in leaders}
    for date in dates[:DEFAULT_CONFIG.strategic_one_name_confirm_days + 2]:
        orders = _decide_and_submit(allocator, account, date, panel, leaders, roles)
        if orders:
            assert len(orders) == 1 and orders[0].side == "BUY"
            assert orders[0].target_weight > initial
            assert account.strategic_grant.target_weight > initial
            assert account.strategic_epochs[0].target_weight > initial
            assert completed_core_admission_budget(account) is None
            from uquant.broker import sync_broker_snapshot

            position = account.positions[OWNER]
            sync_broker_snapshot(account, {
                "as_of": str(date.date()), "cash": account.cash, "fills": [],
                "orders": [{"order_id": orders[0].order_id, "status": "CANCELLED", "remaining_shares": 0}],
                "positions": [{"symbol": OWNER, "shares": position.shares,
                               "sellable_shares": position.shares, "avg_cost": position.avg_cost}],
            }, cfg=DEFAULT_CONFIG)
            assert not account.pending_orders
            assert account.strategic_epochs[0].realized_status == "ACTIVE"
            assert account.strategic_epoch == 0
            assert completed_core_admission_budget(account) == pytest.approx(initial)
            break
    else:
        pytest.fail("fresh native qualification never authorized promotion")


def test_budget_helper_does_not_read_full_weight_even_outside_deployable_configuration():
    from uquant.portfolio.strategic.grant_lifecycle import completed_core_admission_budget

    _, account, _, _, _, _ = _marked_core()
    original = _bounded_cap(account)
    # Deliberate helper-input perturbation, NOT a deployable configuration or
    # native economic scenario: current single-symbol config is capped at .60.
    account.strategic_epochs[0].full_weight = .95
    cap = min(DEFAULT_CONFIG.strategic_dominant_retained_gross, completed_core_admission_budget(account))
    assert cap == pytest.approx(original)
    assert cap > DEFAULT_CONFIG.strategic_dominant_retained_gross * original / .95


def test_profit_cap_missing_native_budget_fails_closed_even_with_python_optimization():
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent

    # Exercise the actual runtime check under -O, where an assert guard would
    # disappear. The positive budget first comes from a real native CORE fill.
    script = dedent("""
        import sys
        from types import SimpleNamespace
        sys.path.insert(0, sys.argv[1])
        from test_strategic_core_profit_lock import _marked_core, _bounded_cap
        from uquant.portfolio.strategic.lifecycle import _completed_core_profit_cap

        if __debug__:
            raise RuntimeError("optimization was not enabled")
        policy, account, *_ = _marked_core()
        context = SimpleNamespace(policy=policy, account=account)
        if _completed_core_profit_cap(context) != _bounded_cap(account):
            raise RuntimeError("valid native budget unexpectedly changed")
        account.fills.clear()
        try:
            _completed_core_profit_cap(context)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("missing native budget gained profit-lock authority")
    """)
    tests = Path(__file__).resolve().parent
    subprocess.run([sys.executable, "-O", "-c", script, str(tests)],
                   cwd=tests.parent, check=True, capture_output=True, text=True)
