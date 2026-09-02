from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from uquant.account import economic_state_sha256, load_account, save_account
from uquant.attribution import build_economic_attribution
from uquant.cli import main
from uquant.report import render_daily_report, render_economic_attribution_report
from uquant.types import (
    AccountState,
    AttributionMechanism,
    Decision,
    Fill,
    Lifecycle,
    Opportunity,
    OriginSubsystem,
    PendingOrder,
    Position,
    Risk,
    Target,
    Tranche,
)


class _FakeData:
    def manifest(self, symbols: set[str], *, as_of: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            end="2026-06-30",
            digest=f"digest:{as_of or 'latest'}",
            symbols=tuple(sorted(symbols)),
        )


class _FakeEngine:
    def __init__(self, data_dir: str | Path) -> None:
        assert str(data_dir)
        self.data = _FakeData()

    def decide(self, **kwargs: Any) -> Decision:
        account = kwargs["account"]
        account.last_successful_run = str(kwargs["as_of"])
        return Decision(
            date=str(kwargs["as_of"]),
            opportunity=Opportunity.CHOPPY,
            risk=Risk.NORMAL,
            target_gross=0.0,
            target_k=0,
            targets=(),
            pending_orders=(),
            risk_summary={},
            decision_digest="fake-decision",
        )

    def backtest(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "start": kwargs["start"],
            "end": kwargs["end"],
            "final_wealth": 1.0,
        }


def _state(path: Path) -> AccountState:
    state = AccountState.empty(2_000_000.0)
    state.data_hash = "data"
    state.code_hash = "code"
    save_account(state, path)
    return state


def test_cli_account_init_daily_sync_and_backtest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: Any,
) -> None:
    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    account_path = tmp_path / "account.json"
    assert (
        main(
            [
                "account-init",
                "--data-dir",
                "fixture",
                "--symbols",
                "sz300308",
                "--cash",
                "2000000",
                "--output",
                str(account_path),
            ]
        )
        == 0
    )
    initialized = load_account(account_path)
    assert initialized.initial_cash == 2_000_000.0
    assert initialized.data_hash == "digest:2026-06-30"

    report_path = tmp_path / "daily.md"
    assert (
        main(
            [
                "daily",
                "--data-dir",
                "fixture",
                "--symbols",
                "sz300308",
                "--date",
                "2026-06-30",
                "--account",
                str(account_path),
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    assert "fake-decision" in report_path.read_text(encoding="utf-8")

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "as_of": "2026-06-30",
                "cash": 2_000_000.0,
                "positions": [],
                "fills": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "account-sync",
                "--account",
                str(account_path),
                "--snapshot",
                str(snapshot),
            ]
        )
        == 0
    )

    result_path = tmp_path / "backtest.json"
    assert (
        main(
            [
                "backtest",
                "--data-dir",
                "fixture",
                "--symbols",
                "sz300308",
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-31",
                "--output",
                str(result_path),
            ]
        )
        == 0
    )
    assert json.loads(result_path.read_text(encoding="utf-8"))["final_wealth"] == 1.0
    assert "positions_reconciled" in capsys.readouterr().out


def test_daily_report_preflights_and_consumes_a_broker_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercises the successful broker-input/report-output boundary."""

    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    account_path = tmp_path / "account.json"
    _state(account_path)
    snapshot = tmp_path / "broker.json"
    snapshot.write_text(
        json.dumps(
            {
                "as_of": "2026-06-30",
                "cash": 2_000_000.0,
                "positions": [],
                "fills": [],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "daily.md"

    assert (
        main(
            [
                "daily",
                "--data-dir",
                str(tmp_path / "frozen"),
                "--symbols",
                "sz300308",
                "--date",
                "2026-06-30",
                "--account",
                str(account_path),
                "--broker-snapshot",
                str(snapshot),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert "fake-decision" in output.read_text(encoding="utf-8")
    assert (
        main(
            [
                "backtest",
                "--data-dir",
                str(tmp_path / "frozen"),
                "--symbols",
                "sz300308",
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-31",
            ]
        )
        == 0
    )


def test_cli_explicit_code_identity_only_migration(
    tmp_path: Path,
    capsys: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_path = tmp_path / "account.json"
    before = _state(account_path)
    before_sha = economic_state_sha256(before)
    monkeypatch.setattr("uquant.cli.code_fingerprint", lambda: "phase-4-code")

    assert (
        main(
            [
                "account-code-migrate",
                "--account",
                str(account_path),
                "--acknowledge-code-change",
            ]
        )
        == 0
    )

    migrated = load_account(account_path)
    assert migrated.code_hash == "phase-4-code"
    assert economic_state_sha256(migrated) == before_sha
    assert migrated.account_migrations[-1]["migration_type"] == "code_identity_only"
    assert "economic_state_sha256" in capsys.readouterr().out


def test_daily_report_output_cannot_overwrite_the_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a report path destroying the durable account it reports on."""

    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    account_path = tmp_path / "account.json"
    _state(account_path)
    original = account_path.read_bytes()

    with pytest.raises(ValueError, match="protected path"):
        main(
            [
                "daily",
                "--data-dir",
                "fixture",
                "--symbols",
                "sz300308",
                "--date",
                "2026-06-30",
                "--account",
                str(account_path),
                "--output",
                str(account_path),
            ]
        )

    assert account_path.read_bytes() == original


def test_daily_report_preflights_a_hardlink_to_the_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches account replacement hiding an invocation-time output alias."""

    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    account_path = tmp_path / "account.json"
    output_path = tmp_path / "daily.md"
    _state(account_path)
    original = account_path.read_bytes()
    os.link(account_path, output_path)

    with pytest.raises(ValueError, match="protected path"):
        main(
            [
                "daily",
                "--data-dir",
                "fixture",
                "--symbols",
                "sz300308",
                "--date",
                "2026-06-30",
                "--account",
                str(account_path),
                "--output",
                str(output_path),
            ]
        )

    assert account_path.read_bytes() == original
    assert output_path.read_bytes() == original


@pytest.mark.parametrize("command", ["daily", "backtest"])
def test_cli_report_preflights_the_consumed_market_data_tree(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a report overwriting replay input after it has been consumed."""

    data_dir = tmp_path / "frozen"
    data_dir.mkdir()
    market_data = data_dir / "sz300308.csv"
    original = b"date,open,high,low,close,volume\n2026-01-02,1,1,1,1,1\n"
    market_data.write_bytes(original)
    account_path = tmp_path / "account.json"
    _state(account_path)

    def fail_replay(_: str | Path) -> _FakeEngine:
        raise AssertionError("market replay started before output preflight")

    monkeypatch.setattr("uquant.cli.ProductionEngine", fail_replay)
    if command == "daily":
        args = [
            "daily",
            "--data-dir",
            str(data_dir),
            "--symbols",
            "sz300308",
            "--date",
            "2026-01-02",
            "--account",
            str(account_path),
            "--output",
            str(market_data),
        ]
    else:
        args = [
            "backtest",
            "--data-dir",
            str(data_dir),
            "--symbols",
            "sz300308",
            "--start",
            "2026-01-02",
            "--end",
            "2026-01-02",
            "--output",
            str(market_data),
        ]

    with pytest.raises(ValueError, match="protected input tree"):
        main(args)

    assert market_data.read_bytes() == original


def test_daily_report_renders_every_action_and_pending_order() -> None:
    account = AccountState.empty(100_000.0)
    account.positions = {
        "held_adjust": Position("held_adjust", shares=100, avg_cost=10.0),
        "held_sell": Position("held_sell", shares=100, avg_cost=10.0),
    }
    targets = (
        Target("held_adjust", 0.4, "CORE", 0.9, 1.0, "adjust"),
        Target("held_sell", 0.0, "CORE", 0.2, 1.0, "exit"),
        Target("new_buy", 0.3, "CORE", 0.8, 1.0, "entry"),
        Target("blocked", 0.0, "CORE", 0.1, 1.0, "risk"),
    )
    pending = PendingOrder(
        signal_date="2026-06-30",
        symbol="new_buy",
        side="BUY",
        target_weight=0.3,
        reason="entry",
        lifecycle="CORE",
    )
    decision = Decision(
        date="2026-06-30",
        opportunity=Opportunity.TREND,
        risk=Risk.CAUTION,
        target_gross=0.7,
        target_k=2,
        targets=targets,
        pending_orders=(pending,),
        risk_summary={
            "shock_state": "WATCH",
            "declining_ratio": 0.5,
            "below_ma20_ratio": 0.4,
            "median_correlation": 0.7,
            "operating_drawdown": 0.08,
            "capital_drawdown": 0.05,
        },
        decision_digest="digest",
    )

    report = render_daily_report(decision, account)
    for action in ("HOLD/ADJUST", "SELL", "BUY", "BLOCKED"):
        assert f"| {action} |" in report
    assert "1. BUY new_buy" in report
    assert "Deployed-sector guard: INACTIVE" in report
    assert "Deployed-sector daily return: N/A" in report


def test_economic_attribution_report_labels_accounting_and_diagnostics() -> None:
    """Catches omitted reconciliation or a diagnostic presented as realized PnL."""
    identity = {
        "event_id": "evt_" + "1" * 64,
        "origin_subsystem": OriginSubsystem.LEADER.value,
        "mechanism": AttributionMechanism.LEADER_SELECTION.value,
        "origin_lifecycle": Lifecycle.CORE.value,
        "replaces_symbol": None,
        "industry_at_entry": "optical",
        "industry_manifest_sha256": "a" * 64,
    }
    account = AccountState.empty(100.0)
    account.cash = 90.0
    account.fills = [
        Fill(
            signal_date="2025-01-02",
            fill_date="2025-01-02",
            symbol="a",
            side="BUY",
            shares=1,
            price=10.0,
            gross_value=10.0,
            commission=0.0,
            stamp_duty=0.0,
            transfer_fee=0.0,
            slippage_cost=0.0,
            reason="display only",
            lifecycle=Lifecycle.CORE.value,
            **identity,
        )
    ]
    account.positions = {
        "a": Position(
            symbol="a",
            shares=1,
            avg_cost=10.0,
            entry_date="2025-01-02",
            highest_close=25.0,
            lifecycle=Lifecycle.CORE.value,
            tranches=[
                Tranche(
                    tranche_id="2025-01-02:a:1",
                    lifecycle=Lifecycle.CORE.value,
                    shares=1,
                    avg_cost=10.0,
                    entry_date="2025-01-02",
                    sellable_date="2025-01-03",
                    highest_close=25.0,
                    **identity,
                )
            ],
        )
    }
    ledger = [
        {
            "date": date,
            "cash": 90.0,
            "equity": equity,
            "gross_exposure": (equity - 90.0) / equity,
            "net_exposure": (equity - 90.0) / equity,
            "cash_weight": 90.0 / equity,
            "position_weights": {"a": (equity - 90.0) / equity},
            "daily_pnl": pnl,
            "target_weights": {"a": 0.2},
            "target_gross": 0.2,
            "caps": {"risk_gross": 0.9, "system_gross": 0.9},
            "binding_owner": "STRATEGY",
            "risk_state": "NORMAL",
            "opportunity": "TREND",
        }
        for date, equity, pnl in (
            ("2025-01-02", 100.0, 0.0),
            ("2025-01-06", 115.0, 15.0),
        )
    ]
    attribution = build_economic_attribution(
        account=account,
        final_prices={"a": 25.0},
        sessions=("2025-01-02", "2025-01-06"),
        economic_start="2025-01-02",
        economic_end="2025-01-06",
        final_equity=115.0,
        daily_ledger=ledger,
        benchmark_close={"2025-01-02": 100.0, "2025-01-06": 105.0},
    )

    report = render_economic_attribution_report(attribution)

    assert "Economic Attribution — 2025-01-02 to 2025-01-06" in report
    assert "Reconciled: **YES** (error 0.000000; tolerance 0.000001)" in report
    assert "Realized PnL: 0.000000" in report
    assert "Open PnL: 15.000000" in report
    assert "Top-1 positive contribution: 100.00%" in report
    assert "Industry-at-entry Contribution" in report
    assert "optical | 15.000000" in report
    assert "Origin Mechanism Contribution" in report
    assert "LEADER_SELECTION | 15.000000" in report
    assert "Gross turnover: 10.000000%" in report
    assert "Replacement-linked lot count: 0" in report
    assert "Cash drag (diagnostic, not accounting PnL): -4.500000" in report
    assert "Risk avoidance: N/A — requires an exact paired counterfactual" in report

    attribution["accounting"]["total_pnl"] = 999.0
    with pytest.raises(ValueError, match="reconcile"):
        render_economic_attribution_report(attribution)
