"""Same-condition simple references, fixed execution stresses and start-date diagnostics.

Research diagnostics only. They never change production targets, the frozen
15x/23.284x acceptance gates or any threshold; they add relative context.

Every reference account uses the production money rules: raw prices, the
decision close as signal, next-open fills at the open with the configured
slippage, dated fees, legal lot quantities, the limit-open block and the
prior-session capacity proxy. Reference books credit cash dividends and
bonus shares on the ex-date without dividend tax or pay-date lag, a small bias
in favour of the references. REF1 is an index analysis reference, not a
tradable account.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from uquant.config import DEFAULT_CONFIG, SystemConfig
from uquant.contracts.universe import EVIDENCE_SCOPE
from uquant.data import DataStore, normalize_symbol
from uquant.execution.fees import fee_components
from uquant.execution.market_constraints import legal_buy_shares, legal_sell_shares, market_execution_blocked
from uquant.features import signal_close

ROOT = Path(__file__).resolve().parents[1]

# Frozen before any run; uncalibrated sensitivity ranges, not confidence levels.
STRESS_SCENARIOS: Mapping[str, Mapping[str, float]] = {
    "base": {},
    "cost_stress": {"slippage": 0.004},
    "fill_stress": {"max_volume_participation": 0.00125},
}

# REF3 is fixed before comparison: top 3 by 60-session signal return, monthly.
REF3_TOP_K = 3
REF3_LOOKBACK = 60
REFERENCE_INDEX = "sh000300"


@dataclass(slots=True)
class _Book:
    cash: float
    shares: dict[str, int]
    fills: int = 0
    unfilled: int = 0
    fees: float = 0.0


def _sessions(store: DataStore, start: str, end: str) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(store.load(REFERENCE_INDEX).index)
    return pd.DatetimeIndex(index[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))])


def _equity(book: _Book, frames: Mapping[str, pd.DataFrame], date: pd.Timestamp) -> float:
    value = book.cash
    for symbol, shares in book.shares.items():
        if shares:
            closes = frames[symbol]["close"].loc[:date].dropna()
            value += shares * float(closes.iloc[-1])
    return value


def _rebalance(
    book: _Book, targets: Mapping[str, float], frames: Mapping[str, pd.DataFrame],
    date: pd.Timestamp, cfg: SystemConfig, *, buy_only: bool = False,
) -> bool:
    """Fill next-open orders toward target weights; sells settle before buys.

    Returns whether every requested quantity filled.
    """
    tradable = {s: f for s, f in frames.items() if date in f.index and len(f.loc[:date]) >= 2}
    marks = {s: float(f.loc[:date].iloc[-2]["close"]) for s, f in tradable.items()}
    equity = book.cash + sum(book.shares.get(s, 0) * marks.get(s, 0.0) for s in book.shares)
    desired = {s: legal_buy_shares(s, math.floor(w * equity / marks[s])) if s in marks else 0
               for s, w in targets.items()}
    orders = {s: desired.get(s, 0) - book.shares.get(s, 0) for s in {*book.shares, *desired}}
    if buy_only:
        orders = {s: max(0, delta) for s, delta in orders.items()}
    before = book.unfilled
    for symbol, delta in sorted(orders.items(), key=lambda item: item[1]):
        if delta == 0 or symbol not in tradable:
            book.unfilled += delta != 0
            continue
        history = tradable[symbol].loc[:date]
        row = history.iloc[-1]
        side = "BUY" if delta > 0 else "SELL"
        capacity = int(float(history.iloc[-2]["volume"]) * cfg.max_volume_participation // 100 * 100)
        if market_execution_blocked(symbol, side, row, float(history.iloc[-2]["close"]), date) or capacity <= 0:
            book.unfilled += 1
            continue
        price = float(row["open"]) * (1 + cfg.slippage if side == "BUY" else 1 - cfg.slippage)
        if side == "SELL":
            shares = legal_sell_shares(symbol, min(-delta, capacity), holding=book.shares[symbol])
        else:
            affordable = int(book.cash / (price * (1 + cfg.commission_rate + 0.0001)))
            shares = legal_buy_shares(symbol, min(delta, capacity, affordable))
        if shares <= 0:
            book.unfilled += 1
            continue
        gross = shares * price
        fees = sum(fee_components(side, gross, cfg, date))
        book.cash += -gross - fees if side == "BUY" else gross - fees
        book.shares[symbol] = book.shares.get(symbol, 0) + (shares if side == "BUY" else -shares)
        book.fills += 1
        book.fees += fees
        book.unfilled += shares < abs(delta)
    return book.unfilled == before


def _summary(equity: pd.Series, book: _Book | None) -> dict[str, Any]:
    from uquant.application.metrics import equity_drawdown_stats

    stats = equity_drawdown_stats(equity)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    result: dict[str, Any] = {
        "final_wealth_multiple": 1.0 + total,
        "total_return": total,
        "max_drawdown": stats["max_drawdown"],
        "drawdown_recovered": stats["drawdown_recovered"],
        "sessions": len(equity),
    }
    if book is not None:
        result.update(fills=book.fills, unfilled_orders=book.unfilled, fees=book.fees)
    return result


def run_reference(
    kind: str, *, data_dir: Path, symbols: Sequence[str], start: str, end: str,
    cfg: SystemConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Run REF0a, REF0b, REF1 or REF3 on the same pool, window and money rules."""
    store = DataStore(data_dir)
    sessions = _sessions(store, start, end)
    if len(sessions) < 2:
        raise ValueError("reference window has fewer than two sessions")
    if kind == "REF1":
        index = store.load(REFERENCE_INDEX)["close"].reindex(sessions).ffill()
        return {"reference": kind, "tradable": False, "index": REFERENCE_INDEX, **_summary(index, None)}
    pool = tuple(sorted({normalize_symbol(s) for s in symbols}))
    frames = {s: store.load(s) for s in pool}
    book = _Book(cash=cfg.initial_cash, shares={})
    events = sorted((e for s in pool for e in store.corporate_actions(s)), key=lambda e: e["ex_date"])
    rows: list[tuple[pd.Timestamp, float]] = []
    pending: dict[str, float] | None = None
    previous = pd.Timestamp.min
    initial_attempts = 0
    for position, date in enumerate(sessions):
        for event in events:
            if previous < pd.Timestamp(event["ex_date"]) <= date and book.shares.get(event["symbol"], 0) > 0:
                held = book.shares[event["symbol"]]
                book.cash += held * float(event.get("cash_per_share", 0.0))
                book.shares[event["symbol"]] = math.floor(held * (1.0 + float(event.get("share_ratio", 0.0))))
        previous = date
        if pending is not None:
            complete = _rebalance(book, pending, frames, date, cfg, buy_only=kind == "REF0a")
            # Buy-and-hold retries its initial buys for a few sessions, never sells.
            initial_attempts += kind == "REF0a"
            pending = pending if kind == "REF0a" and not complete and initial_attempts < 5 else None
        rows.append((date, _equity(book, frames, date)))
        listed = [s for s, f in frames.items() if date in f.index]
        month_end = position + 1 == len(sessions) or sessions[position + 1].month != date.month
        if ((kind == "REF0a" and position == 0) or (kind == "REF0b" and month_end)) and listed:
            pending = dict.fromkeys(listed, 1.0 / len(listed))
        elif kind == "REF3" and month_end:
            momentum = {
                s: float(series.iloc[-1] / series.iloc[-1 - REF3_LOOKBACK] - 1.0)
                for s in listed
                for series in (signal_close(frames[s].loc[:date]).dropna(),)
                if len(series) > REF3_LOOKBACK
            }
            leaders = sorted(momentum, key=lambda s: (-momentum[s], s))[:REF3_TOP_K]
            pending = {s: 1.0 / REF3_TOP_K for s in leaders}
        elif kind not in {"REF0a", "REF0b", "REF3"}:
            raise ValueError(f"unknown reference {kind}")
    equity = pd.Series(dict(rows), dtype=float)
    return {"reference": kind, "tradable": True, "pool_size": len(pool), **_summary(equity, book)}


def run_stress(
    scenario: str, *, data_dir: Path, symbols: Sequence[str], start: str, end: str,
) -> dict[str, Any]:
    """Replay the production strategy under one frozen execution scenario."""
    from uquant.engine import ProductionEngine

    cfg = DEFAULT_CONFIG.override(**STRESS_SCENARIOS[scenario])
    metrics = ProductionEngine(data_dir, cfg).backtest(symbols=symbols, start=start, end=end)
    submitted = max(1, int(metrics["submitted_account_orders"]))
    return {
        "scenario": scenario,
        "overrides": dict(STRESS_SCENARIOS[scenario]),
        "final_wealth_multiple": metrics["final_wealth_multiple"],
        "max_drawdown": metrics["max_drawdown"],
        "fees": metrics["fees"],
        "slippage_cost": metrics["slippage_cost"],
        "unfilled_submission_ratio": metrics["unfilled_account_submissions"] / submitted,
    }


def start_date_diagnostic(
    *, data_dir: Path, symbols: Sequence[str], starts: Iterable[str], months: int,
) -> list[dict[str, Any]]:
    """Fixed-length windows from several starts; overlapping, not independent samples."""
    from uquant.engine import ProductionEngine

    engine = ProductionEngine(data_dir, DEFAULT_CONFIG)
    rows = []
    for start in starts:
        end = str((pd.Timestamp(start) + pd.DateOffset(months=months) - pd.Timedelta(days=1)).date())
        metrics = engine.backtest(symbols=symbols, start=start, end=end)
        rows.append({"start": start, "end": end, "final_wealth_multiple": metrics["final_wealth_multiple"],
                     "max_drawdown": metrics["max_drawdown"]})
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "frozen")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--references", nargs="*", default=["REF0a", "REF0b", "REF1", "REF3"])
    parser.add_argument("--stress", nargs="*", default=[], choices=sorted(STRESS_SCENARIOS))
    parser.add_argument("--start-dates", nargs="*", default=[])
    parser.add_argument("--window-months", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    common = {"data_dir": args.data_dir, "symbols": args.symbols}
    report = {
        **EVIDENCE_SCOPE,
        "window": {"start": args.start, "end": args.end},
        "references": [run_reference(kind, start=args.start, end=args.end, **common) for kind in args.references],
        "stress": [run_stress(name, start=args.start, end=args.end, **common) for name in args.stress],
        "start_dates": start_date_diagnostic(starts=args.start_dates, months=args.window_months, **common),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
