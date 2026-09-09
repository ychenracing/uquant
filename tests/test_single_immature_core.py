"""Native shared qualification and execution for one ordinary early capital slot."""
from dataclasses import replace

from test_shared_core_qualification import CHALLENGER, WITNESSES, _decide, _held_book
from test_strategic_probe_holding import OWNER
from test_strategic_universe_quorum import _risk

from uquant.config import DEFAULT_CONFIG
from uquant.execution import ExecutionPlanner


def _early_book():
    policy, account, dates, panel, leaders, roles = _held_book()
    leaders = {s: replace(leader, mature=False) if s in WITNESSES else leader
               for s, leader in leaders.items()}
    risk = _risk()
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
    return policy, account, dates, panel, leaders, roles, risk


def test_real_shared_qualification_selects_only_highest_rank_immature_core():
    _, account, dates, panel, leaders, _, risk = _early_book()
    entries = risk.evidence["core_allocation"]["symbols"]
    assert all(entries[s]["entry"]["block"] == "READY" for s in WITNESSES)
    assert all(not leaders[s].mature for s in WITNESSES)
    orders = [o for o in account.pending_orders if o.side == "BUY" and not o.grant_id]
    assert [o.symbol for o in orders] == [CHALLENGER]
    before = (account.positions[OWNER].shares, account.positions[OWNER].grant_id)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(
        date=dates[DEFAULT_CONFIG.strategic_cohort_confirm_days], account=account, panel=panel)
    assert [f.symbol for f in fills if f.side == "BUY" and not f.grant_id] == [CHALLENGER]
    assert (account.positions[OWNER].shares, account.positions[OWNER].grant_id) == before


def test_partial_price_drift_and_restart_keep_early_slot_occupied(tmp_path):
    from hashlib import sha256

    from uquant.account import load_account, save_account

    policy, account, dates, panel, leaders, roles, risk = _early_book()
    order = next(o for o in account.pending_orders if o.symbol == CHALLENGER)
    frame = panel[CHALLENGER]
    day = dates[2]
    frame.loc[day, "volume"] = 100_000.
    frame.loc[day, "amount"] = float(frame.loc[day, "close"]) * 100_000.
    # A real lower opening price changes mark weight, not the inventory identity.
    frame.loc[day, "open"] *= .99
    account.data_hash = sha256("".join(f.to_csv() for f in panel.values()).encode()).hexdigest()
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=day, account=account, panel=panel)
    assert any(f.symbol == CHALLENGER and f.shares > 0 for f in fills)
    assert account.positions[CHALLENGER].shares > 0
    assert next(o for o in account.order_ledger if o.order_id == order.order_id).status == "PARTIALLY_FILLED"
    _decide(policy, account, day, panel, leaders, roles, risk=risk)
    assert {o.symbol for o in account.pending_orders if o.side == "BUY" and not o.grant_id} <= {CHALLENGER}
    path = tmp_path / "early-partial.json"
    save_account(account, path)
    account = load_account(path)
    _decide(policy, account, dates[3], panel, leaders, roles, risk=risk)
    assert {o.symbol for o in account.pending_orders if o.side == "BUY" and not o.grant_id} <= {CHALLENGER}
    assert not any(s in account.positions for s in WITNESSES if s != CHALLENGER)


def test_actual_pending_buy_reserves_early_slot_before_first_fill():
    policy, account, dates, panel, leaders, roles, risk = _early_book()
    original = next(o for o in account.pending_orders if o.symbol == CHALLENGER)
    assert CHALLENGER not in account.positions
    _decide(policy, account, dates[2], panel, leaders, roles, risk=risk)
    buys = [o for o in account.pending_orders if o.side == "BUY" and not o.grant_id]
    assert [o.symbol for o in buys] == [CHALLENGER]
    assert buys[0].grant_id == original.grant_id == ""


def test_current_maturity_frees_early_slot_without_selling_old_holding():
    policy, account, dates, panel, leaders, roles, risk = _early_book()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
    shares = account.positions[CHALLENGER].shares
    leaders[CHALLENGER] = replace(leaders[CHALLENGER], mature=True)
    _decide(policy, account, dates[3], panel, leaders, roles, risk=risk)
    assert account.positions[CHALLENGER].shares == shares
    buys = [o for o in account.pending_orders if o.side == "BUY" and not o.grant_id]
    assert len(buys) == 1 and buys[0].symbol == WITNESSES[1]
    assert not any(o.side == "SELL" for o in account.pending_orders)


def test_mature_candidates_are_not_limited_by_the_early_slot():
    policy, account, dates, panel, leaders, roles = _held_book()
    leaders[CHALLENGER] = replace(leaders[CHALLENGER], mature=False)
    for date in dates[:DEFAULT_CONFIG.strategic_cohort_confirm_days]:
        _decide(policy, account, date, panel, leaders, roles)
    assert {o.symbol for o in account.pending_orders if o.side == "BUY" and not o.grant_id} == set(WITNESSES)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
    assert {f.symbol for f in fills if f.side == "BUY" and not f.grant_id} == set(WITNESSES)
    before = {s: (account.positions[s].shares, account.positions[s].grant_id, account.positions[s].epoch_id)
              for s in WITNESSES}
    # Existing multiple holdings can lose maturity; admission limiting never forces an exit.
    leaders = {s: replace(leader, mature=False) for s, leader in leaders.items()}
    _decide(policy, account, dates[3], panel, leaders, roles)
    assert not any(o.side == "SELL" for o in account.pending_orders)
    assert {s: (account.positions[s].shares, account.positions[s].grant_id, account.positions[s].epoch_id)
            for s in WITNESSES} == before


def test_real_full_exit_releases_early_slot_for_new_qualified_candidate():
    from uquant.types import Risk

    policy, account, dates, panel, leaders, roles, risk = _early_book()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
    shares = account.positions[CHALLENGER].shares
    liquidation = replace(risk, state=Risk.RISK_OFF, target_gross_cap=0.,
                          freeze_new_risk=True, reduction_level=3)
    _decide(policy, account, dates[3], panel, leaders, roles, risk=liquidation)
    # Normal permission returns before the real SELL fills; anticipated cash and
    # a pending exit cannot release the still-positive early holding slot.
    _decide(policy, account, dates[3], panel, leaders, roles, risk=risk)
    assert any(o.side == "SELL" and o.symbol == CHALLENGER for o in account.pending_orders)
    assert not any(o.side == "BUY" and not o.grant_id for o in account.pending_orders)
    fills = ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[4], account=account, panel=panel)
    assert sum(f.shares for f in fills if f.symbol == CHALLENGER and f.side == "SELL") == shares
    assert CHALLENGER not in account.positions
    successor = WITNESSES[1]
    leaders[successor] = replace(leaders[successor], score=.96)
    for date in dates[4:6]:
        _decide(policy, account, date, panel, leaders, roles, risk=risk)
    buys = [o for o in account.pending_orders if o.side == "BUY" and not o.grant_id]
    assert len(buys) == 1 and buys[0].symbol == successor
    assert not buys[0].epoch_id


def test_missing_current_leader_does_not_release_existing_early_inventory():
    policy, account, dates, panel, leaders, roles, risk = _early_book()
    ExecutionPlanner(DEFAULT_CONFIG).execute_open(date=dates[2], account=account, panel=panel)
    before = account.positions[CHALLENGER].shares
    del leaders[CHALLENGER]
    _decide(policy, account, dates[3], panel, leaders, roles, risk=risk)
    records = risk.evidence["core_allocation"]["symbols"]
    assert any(records[s].get("entry", {}).get("block") == "READY"
               for s in WITNESSES if s != CHALLENGER)
    assert account.positions[CHALLENGER].shares == before
    assert not any(o.side == "BUY" and not o.grant_id for o in account.pending_orders)
