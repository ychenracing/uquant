from __future__ import annotations

import json

import pytest

from uquant.account import (
    AccountConflictError,
    account_transaction,
    load_account,
    migrate_account_schema,
    save_account,
)
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG
from uquant.types import AccountState, OriginSubsystem


def _account() -> AccountState:
    state = AccountState.empty(10_000.0)
    state.data_hash = "data"
    state.code_hash = "code"
    return state


def _snapshot(as_of: str, cash: float, **extra: object) -> dict[str, object]:
    return {"as_of": as_of, "cash": cash, "positions": [], "fills": [], **extra}


def test_stale_account_copy_cannot_overwrite_newer_state(tmp_path):
    path = tmp_path / "account.json"
    save_account(_account(), path)
    with account_transaction(path) as first:
        stale = first.load()
        fresh = load_account(path)
        fresh.cash += 100
        save_account(fresh, path)
        stale.cash += 200
        with pytest.raises(AccountConflictError):
            first.save(stale)
    assert load_account(path).cash == 10_100.0
    with pytest.raises(FileExistsError), account_transaction(path, create=True):
        pass


def test_snapshot_identity_ordering_and_account_binding():
    account = _account()
    newer = _snapshot("2026-01-06", 1_100.0, broker_account="A1", sequence=2)
    sync_broker_snapshot(account, newer)
    sync_broker_snapshot(account, newer)
    assert len(account.broker_snapshots) == 1 and account.broker_binding == "A1"
    with pytest.raises(ValueError, match="same as_of"):
        sync_broker_snapshot(account, _snapshot("2026-01-06", 1_000.0, broker_account="A1", sequence=1))
    with pytest.raises(ValueError, match="different broker account"):
        sync_broker_snapshot(account, _snapshot("2026-01-07", 1_000.0, broker_account="B2"))
    with pytest.raises(ValueError, match="complete"):
        sync_broker_snapshot(account, _snapshot("2026-01-07", 1_000.0, broker_account="A1", complete=False))
    assert account.cash == 1_100.0


def test_external_trades_and_cash_flows_round_trip(tmp_path):
    account = _account()
    buy = {
        "trade_id": "T1", "source": "MANUAL", "side": "BUY", "symbol": "sz300308",
        "shares": 100, "price": 10.0, "trade_date": "2026-01-06", "commission": 5.0,
    }
    snapshot = _snapshot(
        "2026-01-06", 9_995.0,
        positions=[{"symbol": "sz300308", "shares": 100, "sellable_shares": 0, "avg_cost": 10.05}],
        external_trades=[buy],
        cash_flows=[{"flow_id": "D1", "amount": 1_000.0, "flow_date": "2026-01-06"}],
    )
    sync_broker_snapshot(account, snapshot)
    order = account.order_ledger[0]
    assert order.origin_subsystem == OriginSubsystem.EXTERNAL_TRADE.value and order.status == "FILLED"
    assert account.pending_orders == [] and account.positions["sz300308"].shares == 100
    assert account.operating_peak == account.capital_peak == 11_000.0
    path = tmp_path / "account.json"
    save_account(account, path)
    assert load_account(path).to_dict() == account.to_dict()
    sell = {**buy, "trade_id": "T2", "side": "SELL", "trade_date": "2026-01-07", "shares": 100}
    sync_broker_snapshot(account, _snapshot("2026-01-07", 10_990.0, external_trades=[sell]))
    assert "sz300308" not in account.positions
    conflict = {**buy, "trade_id": "T2", "side": "BUY"}
    with pytest.raises(ValueError, match="reused"):
        sync_broker_snapshot(account, _snapshot("2026-01-08", 10_990.0, external_trades=[conflict]))


def test_positions_above_cap_are_recorded_not_rejected():
    account = _account()
    trades = [
        {"trade_id": f"T{i}", "source": "OTHER_SYSTEM", "side": "BUY", "symbol": symbol,
         "shares": 100, "price": 10.0, "trade_date": "2026-01-06", "execution_sequence": i + 1}
        for i, symbol in enumerate(("sz300308", "sz300502", "sh688256"))
    ]
    positions = [{"symbol": t["symbol"], "shares": 100, "sellable_shares": 0, "avg_cost": 10.0} for t in trades]
    sync_broker_snapshot(
        account,
        _snapshot("2026-01-06", 7_000.0, positions=positions, external_trades=trades),
        cfg=DEFAULT_CONFIG.override(max_positions=2),
    )
    assert len(account.positions) == 3
    assert account.reconciliation_events[-1]["event"] == "position_cap_exceeded"


def test_schema_8_account_requires_explicit_migration(tmp_path):
    path = tmp_path / "account.json"
    payload = _account().to_dict()
    for key in ("account_revision", "broker_binding", "broker_snapshots", "external_cash_flows",
                "corporate_actions", "receivables", "dividend_tax_lots"):
        payload.pop(key)
    payload["schema_version"] = 8
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="account-schema-migrate"):
        load_account(path)
    migrated = migrate_account_schema(path, code_hash="new-code")
    assert load_account(path).to_dict() == migrated.to_dict()
    assert migrated.account_migrations[-1]["migration_type"] == "schema_upgrade"


def test_corporate_action_keeps_equity_continuous_and_is_idempotent():
    import pandas as pd

    from uquant.account.corporate_actions import (
        apply_corporate_actions,
        dividend_tax_on_sale,
        receivable_total,
    )

    account = _account()
    buy = {
        "trade_id": "T1", "source": "MANUAL", "side": "BUY", "symbol": "sz300308",
        "shares": 100, "price": 10.0, "trade_date": "2026-01-06", "commission": 0.0,
    }
    sync_broker_snapshot(account, _snapshot(
        "2026-01-06", 9_000.0,
        positions=[{"symbol": "sz300308", "shares": 100, "sellable_shares": 0, "avg_cost": 10.0}],
        external_trades=[buy],
    ))
    event = {
        "event_id": "sz300308:2026-01-08:distribution", "symbol": "sz300308",
        "ex_date": "2026-01-08", "pay_date": "2026-01-12", "cash_per_share": 0.5, "share_ratio": 0.3,
    }
    frame = pd.DataFrame({"close": [10.0]}, index=pd.to_datetime(["2026-01-07"]))
    position = account.positions["sz300308"]
    cost_before = position.shares * position.avg_cost
    for _ in range(2):
        apply_corporate_actions(account, [event], through="2026-01-08", frames={"sz300308": frame})
    reference = (10.0 - 0.5) / 1.3
    assert position.shares == 130 and receivable_total(account) == pytest.approx(50.0)
    assert position.shares * reference + receivable_total(account) == pytest.approx(1_000.0)
    assert position.shares * position.avg_cost == pytest.approx(cost_before - 50.0)
    assert account.cash == 9_000.0
    apply_corporate_actions(account, [event], through="2026-01-12", frames={"sz300308": frame})
    assert account.receivables == [] and account.cash == pytest.approx(9_050.0)
    sold = [{"tranche_id": position.tranches[0].tranche_id, "shares": 130}]
    assert dividend_tax_on_sale(account, symbol="sz300308", sold_tranches=sold, sale_date="2026-01-20") == (
        pytest.approx(10.0)
    )
    assert sold[0]["dividend_tax"] == pytest.approx(10.0) and account.dividend_tax_lots == []


def test_deposits_and_withdrawals_are_not_investment_returns():
    from uquant.application.metrics import flow_adjusted_performance

    rows = [("2026-01-05", 1_000.0), ("2026-01-06", 1_100.0), ("2026-01-07", 1_210.0), ("2026-01-08", 1_110.0)]
    flows = [{"flow_date": "2026-01-06", "amount": 100.0}, {"flow_date": "2026-01-08", "amount": -100.0}]
    result = flow_adjusted_performance(rows, flows)
    assert result["net_external_flow"] == 0.0
    assert result["investment_pnl"] == pytest.approx(110.0)
    assert result["time_weighted_return"] == pytest.approx(0.1)
    flat = flow_adjusted_performance(rows[:2], flows[:1])
    assert flat["investment_pnl"] == 0.0 and flat["time_weighted_return"] == 0.0
