"""Focused observer checks; fixtures are tests, never evidence of a live scan."""
from __future__ import annotations

import copy
import importlib.util
import shutil
import subprocess
from datetime import datetime

import pytest

from scripts import daily_scan
from scripts.daily_scan import OBSERVER_ID, prior_result
from scripts.daily_scan_market import SHANGHAI, SYMBOLS, session_context
from scripts.daily_scan_report import comparison, project_signals, render_report
from scripts.daily_scan_store import GitStore, put_json, safe_path, verify_manifest

DATES = ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-28"]


def test_calendar_requires_finished_close_and_covers_holiday():
    assert session_context(DATES, datetime(2026, 9, 23, 10, tzinfo=SHANGHAI))["status"] == "MARKET_NOT_CLOSED"
    assert session_context(DATES, datetime(2026, 9, 23, 17, 1, tzinfo=SHANGHAI))["status"] == "READY"
    context = session_context(DATES, datetime(2026, 9, 25, 17, 1, tzinfo=SHANGHAI))
    assert context["status"] == "MARKET_CLOSED"
    assert context["previous_session"] == "2026-09-24"


@pytest.mark.parametrize("dates", [[], DATES[:2], [*DATES, "2026-09-26"]])
def test_invalid_or_stale_calendar_is_not_assumed_open(dates):
    with pytest.raises(ValueError):
        session_context(dates, datetime(2026, 9, 23, 17, 1, tzinfo=SHANGHAI))


def sample_result():
    decision = {"risk_summary": {}, "opportunity": "NORMAL", "risk": "CAUTION",
                "targets": [], "pending_orders": []}
    return {"observer_id": OBSERVER_ID, "status": "COMPLETE", "config_sha256": "config",
            "economic_code_hash": "economic", "source_sha": "a" * 40,
            "target_date": "2026-09-23", "previous_session": "2026-09-22",
            "started_at": "2026-09-23T17:01:00+08:00", "computed_at": "2026-09-23T17:02:00+08:00",
            "run_url": "https://github.com/test/test/actions/runs/1", "observer_start": "2026-09-23",
            "initial_cash": 2000000, "signals": project_signals(decision, {})}


def test_projection_covers_exact_order_without_inventing_buy_permission():
    result = sample_result()
    assert tuple(row["symbol"] for row in result["signals"]["stocks"]) == SYMBOLS
    assert all(row["qualification"] is None for row in result["signals"]["stocks"])
    result["comparison"] = comparison(result, None)
    report = render_report(result, "test production report")
    assert "不可比较" in report and "未提供" in report and "不等于持有或允许买入" in report
    positions = [report.index(symbol[2:]) for symbol in SYMBOLS]
    assert positions == sorted(positions)


def test_comparison_preserves_unknowns_and_detects_changed_intent_weight():
    current = sample_result()
    previous = copy.deepcopy(current)
    previous["target_date"] = current["previous_session"]
    previous["signals"]["stocks"][0]["orders"] = [{"side": "BUY", "target_weight": 0.1}]
    current["signals"]["stocks"][0]["orders"] = [{"side": "BUY", "target_weight": 0.2}]
    result = comparison(current, previous)
    assert result["unavailable"]
    assert any(change["field"] == "action" for change in result["changes"])
    previous["economic_code_hash"] = "different"
    assert comparison(current, previous)["status"] == "INCOMPARABLE"


def stores(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    return remote, GitStore(tmp_path / "writer", str(remote))


def test_native_readback_and_stale_writer_claim_race(tmp_path):
    remote, first = stores(tmp_path)
    put_json(first.root, "status/initial.json", {"initialized": True})
    first.publish(["status/initial.json"], "Initialize test report store")
    second = GitStore(tmp_path / "second", str(remote))
    claim = "claims/2026-09-23.json"
    put_json(first.root, claim, {"status": "STARTED", "run": "first"})
    first.publish([claim], "Claim first")
    put_json(second.root, claim, {"status": "STARTED", "run": "second"})
    with pytest.raises(RuntimeError, match="conflict"):
        second.publish([claim], "Competing claim")
    assert b'"first"' in first.git("show", "FETCH_HEAD:" + claim).stdout


def test_missing_receipt_and_unfinished_claim_do_not_reset_account(tmp_path):
    put_json(tmp_path, "account.json", {})
    with pytest.raises(RuntimeError, match="reset"):
        prior_result(tmp_path, {"target_date": "2026-09-23", "previous_session": "2026-09-22"})
    put_json(tmp_path, "claims/2026-09-22.json", {"status": "STARTED"})
    with pytest.raises(RuntimeError, match="unreconciled"):
        prior_result(tmp_path, {})


def test_paths_and_corrupt_manifest_rejected(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../outside")
    with pytest.raises(ValueError):
        safe_path(tmp_path, ".git/config")
    put_json(tmp_path, "record.json", {})
    with pytest.raises(ValueError, match="bytes differ"):
        verify_manifest(tmp_path, {"record.json": {"size": 0, "sha256": "wrong"}})


def test_data_failure_keeps_diagnostics_but_publishes_no_account(tmp_path, monkeypatch):
    _, store = stores(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    def fail_refresh(root, _day, _previous):
        put_json(root, "input_audit.json", {"failures": {"symbol": "missing close"}})
        raise ValueError("missing close")
    monkeypatch.setattr(daily_scan, "refresh_inputs", fail_refresh)
    context = session_context(DATES, datetime(2026, 9, 23, 17, 1, tzinfo=SHANGHAI))
    with pytest.raises(RuntimeError, match="SCAN_FAILED"):
        daily_scan.run_once(store, work, context, "a" * 40, "test-run")
    assert not (store.root / "account.json").exists()
    assert not (store.root / "latest.json").exists()
    assert list((store.root / "failures").glob("*/inputs/input_audit.json"))


@pytest.mark.skipif(importlib.util.find_spec("uquant") is None, reason="production source not in local partial checkout")
def test_real_production_engine_two_sessions_and_duplicate_guard(tmp_path, monkeypatch, data_dir):
    from uquant.data import DataStore
    from uquant.engine import INDEX_SYMBOLS, REFERENCE_UNIVERSE

    _, store = stores(tmp_path)
    symbols = set(SYMBOLS) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
    data = DataStore(data_dir)
    sessions = data.common_sessions(symbols, "2026-01-01", "2026-08-05")
    dates = [str(date.date()) for date in sessions[-3:]]
    def fixture_refresh(root, day, _previous):
        root.mkdir()
        for symbol in symbols:
            shutil.copyfile(data.path_for(symbol), root / f"{symbol}.csv")
        # These are explicitly fixture prices, not live unadjusted quotes.
        audit = {"test_fixture_only": True, "quotes": {s: {"date": day, "close": float(data.load(s, as_of=day).iloc[-1]["close"])} for s in SYMBOLS}}
        put_json(root, "input_audit.json", audit)
        return audit
    monkeypatch.setattr(daily_scan, "refresh_inputs", fixture_refresh)
    for previous, day in zip(dates[:-1], dates[1:], strict=True):
        work = tmp_path / day
        work.mkdir()
        context = {"target_date": day, "previous_session": previous, "status": "READY", "checked_at": day + "T17:01:00+08:00"}
        result = daily_scan.run_once(store, work, context, "a" * 40, "fixture-run")
        assert result["status"] in ("COMPLETE", "PARTIAL")
        assert prior_result(store.root, context)["target_date"] == day
    monkeypatch.setattr(daily_scan, "refresh_inputs", lambda *_: pytest.fail("duplicate refresh/decision"))
    assert daily_scan.run_once(store, work, context, "b" * 40, "duplicate-run")["status"] == "REUSED"
