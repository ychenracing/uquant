from __future__ import annotations

import csv
import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_tencent_history.py"
SPEC = importlib.util.spec_from_file_location("history_migration_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
history = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = history
SPEC.loader.exec_module(history)


def test_existing_tech_migration_is_causal_and_return_invariant(monkeypatch):
    rows = (
        "date,open,high,low,close,volume,amount\n"
        "2021-08-13,49.000000,51.000000,48.000000,50.000000,10.0,\n"
        "2021-08-16,98.000000,102.000000,96.000000,100.000000,20.0,\n"
        "2021-08-17,107.800000,112.200000,105.600000,110.000000,30.0,\n"
    ).encode()
    result = history.BackfillResult(
        symbol=history.TECH_INDEX,
        first_date="2021-08-13",
        last_date="2021-08-17",
        historical_rows_added=1,
        total_rows=3,
        anchor_max_relative_difference=0.0,
        sha256="legacy",
        pre_inception_proxy="sz399006 close-scaled at 2021-08-16",
    )

    def fake_fetch(symbol: str, start: str, end: str, *, count: int = 640):
        assert symbol == history.TECH_PROXY
        assert start == end == history.TECH_TARGET_FIRST_DATE
        assert count == 30
        return [(start, "196", "204", "192", "200", "100")]

    monkeypatch.setattr(history, "_fetch", fake_fetch)
    updated, migrated = history._causal_rebase_existing_tech(result, rows)
    parsed = list(csv.DictReader(migrated.decode().splitlines()))

    assert Decimal(parsed[0]["close"]) == Decimal("100")
    assert Decimal(parsed[1]["close"]) == Decimal("200")
    assert Decimal(parsed[2]["close"]) == Decimal("220")
    assert history._max_abs_ohlc_return_delta(rows, migrated) == 0
    assert "raw before 2021-08-16" in str(updated.pre_inception_proxy)
