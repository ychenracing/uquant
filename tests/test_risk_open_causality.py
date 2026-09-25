"""Risk sizing and open-time information boundary."""

import unittest
from copy import deepcopy

import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.execution.market_constraints import market_execution_blocked
from uquant.execution.open_execution import _session_marks, _SessionBook, _size_open_order
from uquant.execution.order_planning import plan_orders
from uquant.execution.pending import merge_pending_orders
from uquant.types import AccountOrder, AccountState, PendingOrder, Position, Target
from uquant.validation.absolute_generalization._account_payload import _validate_account_runtime


def _book(account, panel, date):
    return _SessionBook(auction=True, buy_cash=account.cash, capacity_used={},
                        marks=_session_marks(account, panel, date, auction=True))


class RiskOpenCausalityTest(unittest.TestCase):
    def test_risk_trim_bypasses_only_ordinary_band(self):
        account = AccountState(initial_cash=2_000_000, cash=800_000,
                               positions={"sz002409": Position(symbol="sz002409", shares=12_000)})
        def orders(weight, policy):
            target = Target(symbol="sz002409", weight=weight, lifecycle="CORE", alpha_score=.7,
                            confidence=.7, reason="risk cap", reduction_policy=policy,
                            origin_subsystem="RISK", mechanism="RISK_GROSS_CAP")
            return plan_orders(signal_date="2026-07-27", targets=(target,), account=account,
                               prices={"sz002409": 100.0}, cfg=DEFAULT_CONFIG)

        self.assertEqual(len(orders(.58, "RISK_PRIORITY")), 1)
        self.assertEqual(orders(.58, "FIFO"), ())
        self.assertEqual(len(orders(0.0, "FIFO")), 1)

    def test_partly_filled_risk_order_keeps_identity_when_cap_tightens(self):
        old = PendingOrder(signal_date="2026-08-05", symbol="sz002409", side="SELL",
                           target_weight=.20, reason="risk cap", lifecycle="CORE", order_id="O000000004",
                           remaining_shares=200, reduction_policy="RISK_PRIORITY")
        tighter = Target(symbol=old.symbol, weight=.19, lifecycle="CORE", alpha_score=.7,
                         confidence=.7, reason="risk cap", reduction_policy="RISK_PRIORITY")
        merged = merge_pending_orders(retained=[old], planned=(), targets=(tighter,), cfg=DEFAULT_CONFIG)
        self.assertEqual(merged[0].order_id, old.order_id)

    def test_open_quantity_ignores_same_day_future_fields(self):
        date = pd.Timestamp("2026-07-28")
        order = PendingOrder(signal_date="2026-07-27", symbol="sz002409", side="BUY",
                             target_weight=.2, reason="entry", lifecycle="CORE")
        def quantity(volume, amount, close, low, high):
            account = AccountState(initial_cash=2_000_000, cash=2_000_000)
            ledger = AccountOrder(order_id="O000000001", signal_date=order.signal_date,
                                  submitted_date=order.signal_date, symbol=order.symbol, side=order.side,
                                  target_weight=order.target_weight, reason=order.reason, lifecycle=order.lifecycle)
            frame = pd.DataFrame([
                {"date": "2026-07-27", "open": 100, "close": 100, "volume": 1_000_000,
                 "amount": 100_000_000, "low": 99, "high": 101},
                {"date": "2026-07-28", "open": 101, "close": close, "volume": volume,
                 "amount": amount, "low": low, "high": high},
            ]).set_index("date")
            frame.index = pd.to_datetime(frame.index)
            row = frame.loc[date]
            self.assertFalse(market_execution_blocked(order.symbol, order.side, row, 100.0))
            request = _size_open_order(cfg=DEFAULT_CONFIG, date=date, order=order,
                                       account=account, account_order=ledger,
                                       panel={order.symbol: frame}, row=row, retained=[],
                                       book=_book(account, {order.symbol: frame}, date))
            self.assertIsNotNone(request)
            return request.shares

        self.assertEqual(quantity(10_000_000, 2_000_000_000, 130, 98, 131),
                         quantity(1, 1, 90, 80, 101))
        at_limit = pd.Series({"open": 110, "low": 100, "high": 110, "volume": 10_000_000})
        self.assertTrue(market_execution_blocked("sz002409", "BUY", at_limit, 100.0))

    def test_sub_lot_risk_shortfall_is_not_reported_as_satisfied(self):
        date = pd.Timestamp("2026-07-28")
        order = PendingOrder(signal_date="2026-07-27", symbol="sz002409", side="SELL",
                             target_weight=.009995, reason="risk cap", lifecycle="CORE",
                             reduction_policy="RISK_PRIORITY")
        ledger = AccountOrder(order_id="O000000001", signal_date=order.signal_date,
                              submitted_date=order.signal_date, symbol=order.symbol, side=order.side,
                              target_weight=order.target_weight, reason=order.reason,
                              lifecycle=order.lifecycle, reduction_policy=order.reduction_policy)
        account = AccountState(initial_cash=1_000_000, cash=990_000,
                               positions={order.symbol: Position(symbol=order.symbol, shares=100)})
        frame = pd.DataFrame([{"date": "2026-07-27", "open": 100., "close": 99.9,
                              "volume": 1_000_000., "amount": 100_000_000.},
                             {"date": "2026-07-28", "open": 100., "close": 100.,
                              "volume": 1_000_000., "amount": 100_000_000.}]).set_index("date")
        frame.index = pd.to_datetime(frame.index)
        retained = []
        self.assertIsNone(_size_open_order(cfg=DEFAULT_CONFIG, date=date, order=order,
                                           account=account, account_order=ledger,
                                           panel={order.symbol: frame}, row=frame.loc[date], retained=retained,
                                           book=_book(account, {order.symbol: frame}, date)))
        self.assertEqual(retained, [order])
        self.assertEqual(ledger.last_event, "RISK_TARGET_UNMET_LOT")
        self.assertNotEqual(ledger.status, "CANCELLED")
        account.order_ledger.append(ledger)
        _validate_account_runtime(account)

    def test_blocked_risk_sell_validation_preserves_account_and_rejects_false_completion(self):
        for event in ("T_PLUS_ONE_BLOCKED", "LIQUIDITY_PROXY_BLOCKED", "RISK_TARGET_UNMET_LOT"):
            for status, filled in (("OPEN", 0), ("PARTIALLY_FILLED", 100)):
                with self.subTest(event=event, status=status):
                    order = AccountOrder(order_id="O000000001", signal_date="2026-07-27",
                                         submitted_date="2026-07-27", symbol="sz002409", side="SELL",
                                         target_weight=.1, reason="risk cap", lifecycle="CORE",
                                         status=status, requested_shares=200, filled_shares=filled,
                                         remaining_shares=200-filled, last_event=event,
                                         reduction_policy="RISK_PRIORITY")
                    account = AccountState(initial_cash=1_000_000, cash=990_000,
                                           order_ledger=[order])
                    before = deepcopy(account)
                    _validate_account_runtime(account)
                    self.assertEqual(account, before)
                    for invalid_status in ("SUBMITTED", "FILLED", "CANCELLED"):
                        order.status = invalid_status
                        with self.assertRaisesRegex(ValueError, "status/event"):
                            _validate_account_runtime(account)
                    order.status = status
                    order.side = "BUY"
                    with self.assertRaisesRegex(ValueError, "risk sell event"):
                        _validate_account_runtime(account)
                    order.side = "SELL"
                    if event == "RISK_TARGET_UNMET_LOT":
                        order.reduction_policy = "FIFO"
                        with self.assertRaisesRegex(ValueError, "risk sell event"):
                            _validate_account_runtime(account)


if __name__ == "__main__":
    unittest.main()
