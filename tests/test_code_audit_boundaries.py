"""Regression coverage for audited broker and decision input boundaries."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from uquant.account import load_account, save_account
from uquant.broker import sync_broker_snapshot
from uquant.broker_contract import load_broker_snapshot
from uquant.cli import main
from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.data import DataStore
from uquant.engine import ProductionEngine
from uquant.types import AccountState, Opportunity


def _snapshot(**changes):
    return {"as_of": "2026-01-06", "cash": 1000.0, "positions": [], "fills": [], **changes}


def test_broker_requires_cash_without_mutating_account():
    account = AccountState.empty(1000.0)
    before = copy.deepcopy(account.to_dict())
    payload = _snapshot()
    del payload["cash"]
    with pytest.raises(ValueError, match="explicit cash"):
        sync_broker_snapshot(account, payload)
    assert account.to_dict() == before


def test_broker_accepts_explicit_zero_cash():
    account = AccountState.empty(1000.0)
    sync_broker_snapshot(account, _snapshot(cash=0.0))
    assert account.cash == 0.0


@pytest.mark.parametrize("stored", ["2026-01-07", "20260107", "2026-W02-3"])
@pytest.mark.parametrize("incoming", ["2026-01-06", "20260106", "2026-W02-2"])
@pytest.mark.parametrize("field", ["broker_as_of", "last_successful_run"])
def test_broker_compares_parsed_dates_and_preserves_rejected_account(stored, incoming, field):
    account = AccountState.empty(1000.0)
    setattr(account, field, stored)
    before = copy.deepcopy(account.to_dict())
    with pytest.raises(ValueError, match="predates"):
        sync_broker_snapshot(account, _snapshot(as_of=incoming))
    assert account.to_dict() == before


@pytest.mark.parametrize("incoming", ["20260106", "2026-W02-2"])
def test_broker_persists_canonical_snapshot_date(incoming):
    account = AccountState.empty(1000.0)
    sync_broker_snapshot(account, _snapshot(as_of=incoming))
    assert account.broker_as_of == "2026-01-06"


@pytest.mark.parametrize(
    "text",
    [
        '{"cash":1000,"cash":2000000}',
        "[1,2]",
        "null",
        '{"cash":NaN}',
        '{"positions":[{"shares":1,"shares":2}]}',
    ],
)
def test_broker_snapshot_reader_rejects_ambiguous_or_nonobject_json(tmp_path, text):
    path = tmp_path / "snapshot.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_broker_snapshot(path)


def _bound_account(path: Path, *, max_positions: int = 6) -> None:
    cfg = DEFAULT_CONFIG.override(max_positions=max_positions)
    account = AccountState.empty(cfg.initial_cash)
    account.data_hash = "fixture-data"
    account.code_hash = "fixture-code"
    account.account_migrations.append(
        {"migration_type": "configuration_binding", "effective_config_sha256": config_fingerprint(cfg)}
    )
    save_account(account, path)


@pytest.mark.parametrize("command", ["account-sync", "daily"])
def test_cli_broker_inputs_use_strict_decoding(tmp_path, monkeypatch, command):
    from test_cli_and_report import _FakeEngine

    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    account = tmp_path / "account.json"
    snapshot = tmp_path / "broker.json"
    _bound_account(account)
    snapshot.write_text('{"as_of":"2026-01-06","cash":1,"cash":2,"positions":[]}')
    original = account.read_bytes()
    args = [command, "--account", str(account)]
    if command == "daily":
        args += [
            "--broker-snapshot",
            str(snapshot),
            "--data-dir",
            str(tmp_path / "data"),
            "--symbols",
            "sz300308",
            "--date",
            "2026-01-06",
        ]
    else:
        args += ["--snapshot", str(snapshot)]
    with pytest.raises(ValueError, match="duplicate JSON key"):
        main(args)
    assert account.read_bytes() == original


@pytest.mark.parametrize("command", ["account-sync", "daily"])
def test_sync_receives_the_bound_custom_configuration(tmp_path, monkeypatch, command):
    from test_cli_and_report import _FakeEngine

    monkeypatch.setattr("uquant.cli.ProductionEngine", _FakeEngine)
    captured = []

    def sync(account, payload, *, cfg):
        captured.append(cfg)
        return sync_broker_snapshot(account, payload, cfg=cfg)

    monkeypatch.setattr("uquant.cli.sync_broker_snapshot", sync)
    account = tmp_path / "account.json"
    config = tmp_path / "config.json"
    snapshot = tmp_path / "broker.json"
    _bound_account(account, max_positions=3)
    config.write_text('{"max_positions":3}')
    snapshot.write_text(json.dumps(_snapshot(cash=DEFAULT_CONFIG.initial_cash)))
    args = [command, "--account", str(account), "--config", str(config)]
    if command == "daily":
        args += [
            "--broker-snapshot",
            str(snapshot),
            "--data-dir",
            str(tmp_path / "data"),
            "--symbols",
            "sz300308",
            "--date",
            "2026-01-06",
        ]
    else:
        args += ["--snapshot", str(snapshot)]
    assert main(args) == 0
    assert len(captured) == 1 and captured[0].max_positions == 3
    assert load_account(account).cash == DEFAULT_CONFIG.initial_cash


def test_account_sync_rejects_unprovided_custom_config(tmp_path):
    account = tmp_path / "account.json"
    snapshot = tmp_path / "broker.json"
    _bound_account(account, max_positions=3)
    snapshot.write_text(json.dumps(_snapshot()))
    original = account.read_bytes()
    with pytest.raises(ValueError, match="configuration identity differs"):
        main(["account-sync", "--account", str(account), "--snapshot", str(snapshot)])
    assert account.read_bytes() == original


def test_data_authority_replacement_clears_all_engine_derived_caches(tmp_path):
    engine = ProductionEngine(tmp_path)
    engine._leader_score_cache[("cached",)] = {}
    engine._reversal_observation_cache[("cached",)] = []
    engine._risk_timeline_cache_key = ("cached",)
    engine._risk_timeline_cache = object()
    raw, features = engine._raw, engine._features
    old = engine.data
    engine.data = old
    assert engine._leader_score_cache
    (tmp_path / "other").mkdir()
    engine.data = DataStore(tmp_path / "other")
    assert not engine._leader_score_cache
    assert not engine._reversal_observation_cache
    assert engine._risk_timeline_cache is None
    assert engine._risk_timeline_cache_key is None
    assert engine.workspace.data is engine.data
    assert engine._raw is raw and engine._features is features


@pytest.mark.parametrize(
    "prior,new,profile",
    [
        (Opportunity.TREND, Opportunity.RECOVERY, "TREND"),
        (Opportunity.RECOVERY, Opportunity.TREND, "RECOVERY"),
    ],
)
def test_profile_label_tracks_scoring_state_not_new_classification(
    data_dir, monkeypatch, prior, new, profile
):
    import uquant.application.decision as decision_module

    def classify(**kwargs):
        kwargs["account"].opportunity = new.value
        return new

    monkeypatch.setattr(decision_module, "classify_opportunity", classify)
    account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
    account.opportunity = prior.value
    decision = ProductionEngine(data_dir).decide(
        symbols=("sz300308", "sz300502", "sz300394"),
        as_of="2026-06-30",
        account=account,
    )
    assert decision.opportunity is new
    assert decision.risk_summary["factor_profile"] == profile
