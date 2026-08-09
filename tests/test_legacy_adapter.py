from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_legacy_common_adapter.py"
SPEC = importlib.util.spec_from_file_location("legacy_adapter_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)


def test_observable_sessions_skip_market_dates_without_basket_rows():
    panel = {
        "a": pd.DataFrame(
            {"close": [1.0, 2.0]},
            index=pd.to_datetime(["2026-01-05", "2026-01-07"]),
        ),
        "b": pd.DataFrame(
            {"close": [3.0]},
            index=pd.to_datetime(["2026-01-08"]),
        ),
    }
    market = pd.date_range("2026-01-05", "2026-01-09", freq="D")

    assert adapter._observable_sessions(panel, market).equals(
        pd.to_datetime(["2026-01-05", "2026-01-07", "2026-01-08"])
    )


def test_broker_order_ledger_nets_internal_fills_by_execution_key():
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": "2026-01-05",
            "symbol": "300308",
            "side": "buy",
            "reason": "fast",
            "price": 10.0,
            "shares": 100,
        },
        {
            "fill_date": "2026-01-06",
            "signal_date": "2026-01-05",
            "symbol": "300308",
            "side": "BUY",
            "reason": "slow",
            "price": 10.0,
            "shares": 200,
        },
        {
            "fill_date": "2026-01-07",
            "signal_date": "2026-01-06",
            "symbol": "300308",
            "side": "SELL",
            "reason": "exit",
            "price": 11.0,
            "shares": 300,
        },
    ]

    ledger, linked = adapter._broker_order_ledger("trade", fills)

    assert len(ledger) == 2
    assert ledger[0]["filled_shares"] == 300
    assert ledger[0]["internal_fills"] == 2
    assert {row["order_id"] for row in linked} == {
        "TRADE-000001",
        "TRADE-000002",
    }
    assert linked[0]["order_id"] == linked[1]["order_id"]


def test_intent_diagnostics_keep_unfilled_churn_out_of_account_orders():
    submissions = [
        {
            "signal_date": "2026-01-05",
            "attempt_date": "2026-01-06",
            "symbol": "300308",
            "side": "BUY",
        },
        {
            "signal_date": "2026-01-06",
            "attempt_date": "2026-01-07",
            "symbol": "300308",
            "side": "BUY",
        },
    ]
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": "2026-01-05",
            "symbol": "300308",
            "side": "BUY",
            "reason": "entry",
            "price": 10.0,
            "shares": 100,
        }
    ]

    diagnostics = adapter._intent_diagnostics(submissions, fills)
    ledger, _ = adapter._broker_order_ledger("qwenquant", fills)

    assert diagnostics["unique_signal_intents"] == 2
    assert diagnostics["unfilled_signal_intents"] == 1
    assert len(ledger) == 1


def test_missing_fill_signal_date_links_to_unique_next_open_attempt():
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": None,
            "symbol": "sz300308",
            "side": "BUY",
            "reason": "entry",
            "price": 10.0,
            "shares": 100,
        }
    ]
    submissions = [
        {
            "signal_date": "2026-01-05",
            "attempt_date": "2026-01-06",
            "symbol": "sz300308",
            "side": "BUY",
        }
    ]

    linked = adapter._link_missing_signal_dates(fills, submissions)

    assert linked[0]["signal_date"] == "2026-01-05"


def test_missing_fill_signal_date_fails_closed_when_mapping_is_ambiguous():
    fills = [
        {
            "fill_date": "2026-01-06",
            "signal_date": None,
            "symbol": "sz300308",
            "side": "BUY",
        }
    ]
    submissions = [
        {
            "signal_date": signal_date,
            "attempt_date": "2026-01-06",
            "symbol": "sz300308",
            "side": "BUY",
        }
        for signal_date in ("2026-01-04", "2026-01-05")
    ]

    with pytest.raises(RuntimeError, match="exactly one close submission"):
        adapter._link_missing_signal_dates(fills, submissions)
