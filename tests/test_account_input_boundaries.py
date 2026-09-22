"""Authoritative input failures must leave the existing account untouched."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from test_broker_sync import _buy_account, _buy_fill, _position_snapshot
from test_cli_and_report import _FakeEngine, _state

import uquant.cli as cli
from uquant.account import load_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.types import AccountState


def test_missing_cash_is_rejected_without_mutating_account() -> None:
    account = AccountState.empty(2000.0)
    before = copy.deepcopy(account)
    with pytest.raises(ValueError, match="explicit cash"):
        sync_broker_snapshot(account, {"as_of": "2026-09-21", "positions": []})
    assert account == before


def test_explicit_zero_cash_remains_valid() -> None:
    account = AccountState.empty(2000.0)
    sync_broker_snapshot(account, {"as_of": "2026-09-21", "cash": 0, "positions": []})
    assert account.cash == 0.0


@pytest.mark.parametrize("field", ["broker_as_of", "last_successful_run"])
@pytest.mark.parametrize("date", ["20260921", "2026-W39-1", "2026-09-21"])
def test_snapshot_chronology_uses_dates_not_spelling(field: str, date: str) -> None:
    account = AccountState.empty(2000.0)
    setattr(account, field, "2026-09-22")
    before = copy.deepcopy(account)
    with pytest.raises(ValueError, match="predates"):
        sync_broker_snapshot(account, {"as_of": date, "cash": 1.0, "positions": []})
    assert account == before


def test_new_snapshot_and_fill_dates_are_canonical_and_idempotent() -> None:
    account = _buy_account()
    fill = _buy_fill("F1", shares=100, remaining_shares=0, final=True, execution_sequence=1)
    fill["fill_date"] = "20260106"
    snapshot = _position_snapshot([fill])
    snapshot["as_of"] = "2026-W02-2"
    sync_broker_snapshot(account, snapshot)
    assert account.broker_as_of == "2026-01-06"
    assert account.fills[0].fill_date == "2026-01-06"
    assert account.order_ledger[0].last_update_date == "2026-01-06"
    before = copy.deepcopy(account)
    fill["fill_date"] = "2026-01-06"
    snapshot["as_of"] = "20260106"
    sync_broker_snapshot(account, snapshot)
    assert account == before


def test_noncanonical_persisted_date_is_rejected_without_rewriting_file(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    _state(path)
    raw = json.loads(path.read_text())
    raw["last_successful_run"] = "20260921"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="canonical YYYY-MM-DD"):
        load_account(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["daily", "account-sync"])
@pytest.mark.parametrize(
    "payload",
    [
        '{"as_of":"2026-06-30","cash":1,"cash":2000000,"positions":[]}',
        '{"as_of":"2026-06-30","cash":2000000,"positions":[{"symbol":"300308","symbol":"300502"}]}',
        "[]",
        '{"as_of":"2026-06-30","cash":NaN,"positions":[]}',
    ],
)
def test_cli_rejects_ambiguous_snapshot_before_publication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
    payload: str,
) -> None:
    monkeypatch.setattr(cli, "ProductionEngine", _FakeEngine)
    account = tmp_path / "account.json"
    _state(account)
    before = account.read_bytes()
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(payload)
    if command == "daily":
        args = [
            command,
            "--account",
            str(account),
            "--broker-snapshot",
            str(snapshot),
            "--symbols",
            "sz300308",
            "--date",
            "2026-06-30",
            "--data-dir",
            str(tmp_path / "data"),
        ]
    else:
        args = [command, "--account", str(account), "--snapshot", str(snapshot)]
    with pytest.raises(ValueError):
        cli.main(args)
    assert account.read_bytes() == before


def test_account_loader_rejects_duplicate_cash(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    _state(path)
    path.write_text(path.read_text().replace('"cash":', '"cash": 1, "cash":', 1))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="missing or corrupt"):
        load_account(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("command", ["daily", "account-sync"])
def test_bound_configuration_reaches_broker_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
) -> None:
    cfg = DEFAULT_CONFIG.override(max_positions=1)
    path = tmp_path / "account.json"
    account = _state(path)
    account.account_migrations.append(
        {"migration_type": "configuration_binding", "effective_config_sha256": config_fingerprint(cfg)}
    )
    save_account(account, path)
    config = tmp_path / "config.json"
    config.write_text('{"max_positions":1}')
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"as_of":"2026-06-30","cash":2000000,"positions":[]}')
    observed = []

    def sync(account: AccountState, payload: dict[str, Any], *, cfg=DEFAULT_CONFIG):
        observed.append(cfg)
        return sync_broker_snapshot(account, payload, cfg=cfg)

    monkeypatch.setattr(cli, "sync_broker_snapshot", sync)
    monkeypatch.setattr(cli, "ProductionEngine", _FakeEngine)
    if command == "daily":
        args = [
            command,
            "--account",
            str(path),
            "--broker-snapshot",
            str(snapshot),
            "--symbols",
            "sz300308",
            "--date",
            "2026-06-30",
            "--data-dir",
            str(tmp_path / "data"),
        ]
    else:
        args = [command, "--account", str(path), "--snapshot", str(snapshot)]
    assert cli.main([*args, "--config", str(config)]) == 0
    assert observed == [cfg]


def test_account_sync_cannot_change_configuration_binding(tmp_path: Path) -> None:
    path = tmp_path / "account.json"
    _state(path)
    before = path.read_bytes()
    config = tmp_path / "config.json"
    config.write_text('{"max_positions":1}')
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text('{"as_of":"2026-06-30","cash":2000000,"positions":[]}')
    with pytest.raises(ValueError, match="configuration identity"):
        cli.main(
            ["account-sync", "--account", str(path), "--snapshot", str(snapshot), "--config", str(config)]
        )
    assert path.read_bytes() == before
