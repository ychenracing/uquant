from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from uquant.account import load_account, save_account
from uquant.cli import main
from uquant.report import render_daily_report
from uquant.types import (
    AccountState,
    Decision,
    Opportunity,
    PendingOrder,
    Position,
    Risk,
    Target,
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


def test_cli_explicit_account_migration(tmp_path: Path, capsys: Any) -> None:
    account_path = tmp_path / "legacy.json"
    state = _state(account_path)
    payload = state.to_dict()
    payload.pop("schema_version")
    payload.pop("account_migrations")
    account_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="acknowledge"):
        main(["account-migrate", "--account", str(account_path)])
    assert (
        main(
            [
                "account-migrate",
                "--account",
                str(account_path),
                "--acknowledge-code-change",
            ]
        )
        == 0
    )
    assert load_account(account_path).account_migrations
    assert "schema_version" in capsys.readouterr().out


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
