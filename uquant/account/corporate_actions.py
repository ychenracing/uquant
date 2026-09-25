"""Cash dividends, bonus/transfer shares and dividend tax on the account ledger.

Prices in the account stay in raw money terms. On an ex-date the holder of
record keeps equity continuous: shares grow by the distribution ratio, cash
dividends become a receivable until the pay date, and every price anchor is
multiplied by ``r = ex_reference / previous_close`` so that it stays in the
same coordinate as the raw post-event close.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import date as date_type
from typing import Any

import pandas as pd

from ..data import ex_reference_price
from ..types import AccountState, Tranche

DIVIDEND_TAX_SHORT_DAYS = 30
DIVIDEND_TAX_LONG_DAYS = 365


def receivable_total(account: AccountState) -> float:
    """Cash dividends already entitled but not yet paid."""
    return sum(float(item["amount"]) for item in account.receivables)


def _ex_prices(event: Mapping[str, Any], frame: pd.DataFrame | None) -> tuple[float, float]:
    """Return (ex ratio, previous raw close) for one event."""
    if frame is not None:
        before = frame.loc[: pd.Timestamp(event["ex_date"]) - pd.Timedelta(days=1)]
        if not before.empty:
            previous_close = float(before["close"].iloc[-1])
            reference = ex_reference_price(
                previous_close,
                cash_per_share=float(event.get("cash_per_share", 0.0)),
                share_ratio=float(event.get("share_ratio", 0.0)),
            )
            return reference / previous_close, previous_close
    raise ValueError(f"corporate action {event['event_id']} has no previous close for its ex-date ratio")


def _adjust_entitled_lots(
    account: AccountState,
    event: Mapping[str, Any],
    entitled: list[Tranche],
    *,
    target_total: int,
    ratio: float,
    share_ratio: float,
    cash_per_share: float,
) -> list[dict[str, Any]]:
    """Grow each entitled lot, keep exact-cash cost basis and register its dividend tax lot."""
    added: list[dict[str, Any]] = [
        {"tranche": tranche, "old": tranche.shares, "new": math.floor(tranche.shares * (1.0 + share_ratio) + 1e-9)}
        for tranche in entitled
    ]
    added[0]["new"] += target_total - sum(item["new"] for item in added)
    for item in added:
        tranche, old, new = item["tranche"], item["old"], item["new"]
        tranche.avg_cost = old * (tranche.avg_cost - cash_per_share) / new
        tranche.highest_close *= ratio
        tranche.lowest_close *= ratio
        tranche.shares = new
        if cash_per_share > 0:
            account.dividend_tax_lots.append(
                {
                    "event_id": event["event_id"],
                    "symbol": event["symbol"],
                    "tranche_id": tranche.tranche_id,
                    "entry_date": tranche.entry_date,
                    "dividend_per_share": old * cash_per_share / new,
                    "shares": new,
                }
            )
    return added


def apply_corporate_actions(
    account: AccountState,
    events: Iterable[Mapping[str, Any]],
    *,
    through: str,
    frames: Mapping[str, pd.DataFrame],
) -> int:
    """Apply unapplied events with ``ex_date <= through`` exactly once."""

    applied_ids = {item["event_id"] for item in account.corporate_actions if "event_id" in item}
    count = 0
    for event in sorted(events, key=lambda item: (item["ex_date"], item["event_id"])):
        if event["ex_date"] > through or event["event_id"] in applied_ids:
            continue
        position = account.positions.get(event["symbol"])
        entitled = [
            tranche
            for tranche in (position.tranches if position is not None else [])
            if tranche.shares > 0 and tranche.entry_date < event["ex_date"]
        ]
        if position is None or not entitled:
            continue
        record: dict[str, Any] = {"type": "corporate_action", **dict(event)}
        ratio, _ = _ex_prices(event, frames.get(event["symbol"]))
        if not 0.0 < ratio <= 1.0 + 1e-9:
            raise ValueError(f"corporate action {event['event_id']} has invalid ex ratio {ratio}")
        share_ratio = float(event.get("share_ratio", 0.0))
        cash_per_share = float(event.get("cash_per_share", 0.0))
        entitled_shares = sum(tranche.shares for tranche in entitled)
        target_total = math.floor(entitled_shares * (1.0 + share_ratio) + 1e-9)
        added = _adjust_entitled_lots(
            account, event, entitled, target_total=target_total, ratio=ratio,
            share_ratio=share_ratio, cash_per_share=cash_per_share,
        )
        position.shares = sum(tranche.shares for tranche in position.tranches)
        position.avg_cost = sum(t.shares * t.avg_cost for t in position.tranches) / position.shares
        position.highest_close *= ratio
        dividend = entitled_shares * cash_per_share
        if dividend > 0:
            account.receivables.append(
                {
                    "event_id": event["event_id"],
                    "symbol": event["symbol"],
                    "amount": dividend,
                    "pay_date": event.get("pay_date") or event["ex_date"],
                }
            )
        record.update(
            entitled_shares=entitled_shares,
            ratio=ratio,
            lot_share_additions=[
                {"symbol": event["symbol"], "lot_event_id": item["tranche"].event_id,
                 "added_shares": item["new"] - item["old"]}
                for item in added
                if item["new"] != item["old"]
            ],
        )
        account.corporate_actions.append(record)
        applied_ids.add(event["event_id"])
        count += 1
    settle_receivables(account, through=through)
    return count


def settle_receivables(account: AccountState, *, through: str, into_cash: bool = True) -> float:
    """Move due dividends into cash; a broker snapshot already contains them."""

    due = [item for item in account.receivables if item["pay_date"] <= through]
    total = sum(float(item["amount"]) for item in due)
    if due:
        account.receivables = [item for item in account.receivables if item["pay_date"] > through]
        if into_cash:
            account.cash += total
    return total


def _tax_rate(entry_date: str, sale_date: str) -> float:
    held = (date_type.fromisoformat(sale_date) - date_type.fromisoformat(entry_date)).days
    if held <= DIVIDEND_TAX_SHORT_DAYS:
        return 0.20
    if held <= DIVIDEND_TAX_LONG_DAYS:
        return 0.10
    return 0.0


def dividend_tax_on_sale(
    account: AccountState,
    *,
    symbol: str,
    sold_tranches: Iterable[dict[str, Any]],
    sale_date: str,
) -> float:
    """Charge holding-period dividend tax for the sold lot shares (individual investor rule).

    The per-lot tax is written onto the sold-lot allocation so attribution can
    charge it to the realized lot.
    """

    tax = 0.0
    for allocation in sold_tranches:
        sold = int(allocation["shares"])
        lot_tax = 0.0
        for lot in account.dividend_tax_lots:
            if lot["symbol"] != symbol or lot["tranche_id"] != allocation.get("tranche_id"):
                continue
            used = min(sold, int(lot["shares"]))
            lot_tax += used * float(lot["dividend_per_share"]) * _tax_rate(lot["entry_date"], sale_date)
            lot["shares"] = int(lot["shares"]) - used
        if lot_tax > 0:
            allocation["dividend_tax"] = lot_tax
            tax += lot_tax
    account.dividend_tax_lots = [lot for lot in account.dividend_tax_lots if lot["shares"] > 0]
    if tax > 0:
        account.cash -= tax
        account.corporate_actions.append(
            {"type": "dividend_tax", "symbol": symbol, "date": sale_date, "amount": tax}
        )
    return tax
