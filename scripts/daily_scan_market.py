"""Live inputs for the scheduled observer; never refresh frozen research files."""
from __future__ import annotations

# Chinese punctuation is intentional in user-facing reports.
# ruff: noqa: RUF001

import importlib
import json
import math
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

SHANGHAI = ZoneInfo("Asia/Shanghai")
WATCHLIST = (
    ("sz300308", "中际旭创"), ("sz300502", "新易盛"), ("sz300394", "天孚通信"),
    ("sh688256", "寒武纪"), ("sh603986", "兆易创新"), ("sh688072", "拓荆科技"),
    ("sh688300", "联瑞新材"), ("sz300054", "鼎龙股份"), ("sh688361", "中科飞测"),
    ("sz002409", "雅克科技"), ("sh688498", "源杰科技"), ("sh688120", "华海清科"),
    ("sz002384", "东山精密"),
)
SYMBOLS = tuple(symbol for symbol, _ in WATCHLIST)


def session_context(dates: list[str], now: datetime) -> dict[str, Any]:
    """A calendar must cover the target on both sides; absence is not a holiday."""
    if now.tzinfo is None:
        raise ValueError("timezone-aware clock required")
    local = now.astimezone(SHANGHAI)
    day = local.date().isoformat()
    sessions = sorted(set(dates))
    if not sessions or sessions[0] >= day or sessions[-1] < day:
        raise ValueError("trading calendar does not cover the target date")
    # Provider dates must be real ISO dates, and makeup Saturdays are never sessions.
    for value in sessions:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
        if parsed.isoformat() != value or parsed.weekday() > 4:
            raise ValueError("invalid trading calendar session")
    previous = max(value for value in sessions if value < day)
    is_session = day in sessions and local.weekday() < 5
    status = "READY" if is_session else "MARKET_CLOSED"
    if is_session and local.time() < time(15, 0):
        status = "MARKET_NOT_CLOSED"
    return {"target_date": day, "previous_session": previous, "status": status,
            "calendar_source": "akshare.tool_trade_date_hist_sina", "checked_at": local.isoformat()}


def calendar_now(now: datetime) -> tuple[dict[str, Any], list[str]]:
    ak = importlib.import_module("akshare")
    frame = ak.tool_trade_date_hist_sina()
    dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.strftime("%Y-%m-%d").tolist()
    return session_context(dates, now), dates


def refresh_inputs(root: Path, day: str, previous: str) -> dict[str, Any]:
    """Fetch QFQ research prices and separate unadjusted displayed quotes."""
    from uquant.data import DataStore
    from uquant.engine import INDEX_SYMBOLS, REFERENCE_UNIVERSE

    ak = importlib.import_module("akshare")
    root.mkdir(parents=True, exist_ok=False)
    store = DataStore(root)
    stocks = sorted(set(SYMBOLS) | set(REFERENCE_UNIVERSE))
    coverage: dict[str, Any] = {}
    failures: dict[str, str] = {}
    # Serial calls avoid unnecessary provider load. The workflow supervises total runtime.
    for symbol in stocks:
        try:
            store.refresh_akshare([symbol], end=day)
            frame = DataStore(root).load(symbol)
            coverage[symbol] = {"date": str(frame.index[-1].date()), "rows": len(frame), "adjustment": "qfq"}
            if coverage[symbol]["date"] != day or len(frame) < 2:
                raise ValueError("missing target-date close or history")
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {exc}"
    mapping = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
               "收盘": "close", "成交量": "volume", "成交额": "amount"}
    for symbol in INDEX_SYMBOLS:
        try:
            raw = ak.index_zh_a_hist(symbol=symbol[2:], period="daily", start_date="20000101", end_date=day.replace("-", ""))
            frame = raw.rename(columns=mapping)[list(mapping.values())].copy()
            frame["volume"] = pd.to_numeric(frame["volume"], errors="raise") * 100
            validated = DataStore._validate(frame, symbol)
            validated = validated.loc[:pd.Timestamp(day)]
            coverage[symbol] = {"date": str(validated.index[-1].date()), "rows": len(validated), "adjustment": "raw"}
            if coverage[symbol]["date"] != day or len(validated) < 2:
                raise ValueError("missing target-date raw index or history")
            validated.reset_index().to_csv(root / f"{symbol}.csv", index=False)
        except Exception as exc:
            failures[symbol] = f"{type(exc).__name__}: {exc}"
    quotes: dict[str, Any] = {}
    for symbol in SYMBOLS:
        try:
            raw = ak.stock_zh_a_hist(symbol=symbol[2:], period="daily", start_date=previous.replace("-", ""), end_date=day.replace("-", ""), adjust="")
            # Keep the provider's raw records; do not use QFQ prices as exchange close.
            raw.to_csv(root / f"{symbol}.raw.csv", index=False)
            dates = pd.to_datetime(raw["日期"], errors="raise").dt.strftime("%Y-%m-%d")
            selected = raw.loc[dates == day]
            if len(selected) != 1:
                raise ValueError("unadjusted quote is missing or duplicated")
            row = selected.iloc[0]
            close = float(row["收盘"])
            if not math.isfinite(close) or close <= 0:
                raise ValueError("invalid unadjusted close")
            change = row.get("涨跌幅")
            if pd.notna(change) and not math.isfinite(float(change)):
                raise ValueError("invalid daily change")
            quotes[symbol] = {"date": day, "close": close, "change_pct": float(change) if pd.notna(change) else None, "adjustment": "raw"}
        except Exception as exc:
            failures[symbol + ":raw"] = f"{type(exc).__name__}: {exc}"
    audit = {"provider": "AkShare/Eastmoney", "fetched_at": datetime.now(SHANGHAI).isoformat(),
             "coverage": coverage, "quotes": quotes, "failures": failures}
    (root / "input_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if failures:
        raise ValueError("live input validation failed: " + ", ".join(sorted(failures)))
    return audit
