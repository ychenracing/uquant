"""F01-F04: required broker facts, parsed chronology and bound configuration."""
replace('uquant/broker_contract.py', 'from typing import Any\n', 'from pathlib import Path\nfrom typing import Any, cast\n\nfrom .contracts.strict_json import strict_json_loads\n')
replace('uquant/broker_contract.py', '@dataclass(frozen=True, slots=True)\nclass BrokerFillValues:', '''def load_broker_snapshot(path: str | Path) -> dict[str, Any]:
    """Read an unambiguous broker object before any account mutation."""
    payload = strict_json_loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("broker snapshot must be a JSON object")
    return cast(dict[str, Any], payload)


@dataclass(frozen=True, slots=True)
class BrokerFillValues:''')
replace('uquant/broker.py', '''    as_of_value = payload.get("as_of", "")
    snapshot_date = _broker_date(as_of_value, field="snapshot as_of")
    as_of = str(as_of_value)
    if account.broker_as_of and as_of < account.broker_as_of:
        raise ValueError("broker snapshot predates the latest broker snapshot")
    if account.last_successful_run and as_of < account.last_successful_run:
        raise ValueError("broker snapshot predates the last successful decision")

    cash = _nonnegative(payload, "cash")''', '''    if not isinstance(payload, dict):
        raise ValueError("broker snapshot must be an object")
    as_of_value = payload.get("as_of", "")
    snapshot_date = _broker_date(as_of_value, field="snapshot as_of")
    as_of = snapshot_date.isoformat()
    if account.broker_as_of and snapshot_date < _broker_date(
        account.broker_as_of, field="stored broker_as_of"
    ):
        raise ValueError("broker snapshot predates the latest broker snapshot")
    if account.last_successful_run and snapshot_date < _broker_date(
        account.last_successful_run, field="last successful decision"
    ):
        raise ValueError("broker snapshot predates the last successful decision")

    if "cash" not in payload:
        raise ValueError("broker snapshot requires explicit cash")
    cash = _nonnegative(payload, "cash")''')
replace('uquant/broker.py', '    fill_date = str(fill_date_value)\n', '    fill_date = parsed_fill_date.isoformat()\n')
replace('uquant/broker.py', '        and existing.fill_date == fill_date\n', '''        and _broker_date(existing.fill_date, field="stored fill_date")
        == _broker_date(fill_date, field="fill_date")
''')
replace('uquant/broker.py', '        if remaining <= 0 or tranche.sellable_date > fill_date:\n', '''        if remaining <= 0 or _broker_date(tranche.sellable_date, field="lot sellable_date") > _broker_date(
            fill_date, field="sale fill_date"
        ):
''')
replace('uquant/cli.py', 'from .broker import sync_broker_snapshot\n', 'from .broker import sync_broker_snapshot\nfrom .broker_contract import load_broker_snapshot\n')
replace('uquant/cli.py', 'from .config import DEFAULT_CONFIG, config_fingerprint\n', 'from .config import DEFAULT_CONFIG, SystemConfig, config_fingerprint\n')
replace('uquant/cli.py', '    for command in (init, daily, backtest):\n', '    for command in (init, daily, backtest, sync):\n')
block = '''    bindings = [event["effective_config_sha256"] for event in account.account_migrations
                if event.get("migration_type") == "configuration_binding"]
    expected = bindings[-1] if bindings else config_fingerprint(DEFAULT_CONFIG)
    if expected != config_fingerprint(cfg):
        raise ValueError("account configuration identity differs; no automatic configuration migration is available")
    if any(epoch.config_identity != "config:" + expected for epoch in account.strategic_epochs):
        raise ValueError("account strategic configuration identity differs")'''
replace('uquant/cli.py', block, '    _validate_account_config(account, cfg)')
replace('uquant/cli.py', 'def _run_daily(args: argparse.Namespace) -> int:\n', '''def _validate_account_config(account: AccountState, cfg: SystemConfig) -> None:
    """Require the same effective configuration for sync and daily decisions."""
''' + block + '\n\n\ndef _run_daily(args: argparse.Namespace) -> int:\n')
replace('uquant/cli.py', '        snapshot = json.loads(Path(args.broker_snapshot).read_text(encoding="utf-8"))\n        sync_broker_snapshot(account, snapshot)\n', '        snapshot = load_broker_snapshot(args.broker_snapshot)\n        sync_broker_snapshot(account, snapshot, cfg=cfg)\n')
replace('uquant/cli.py', '''        account = load_account(args.account)
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
        summary = sync_broker_snapshot(account, snapshot)''', '''        account = load_account(args.account)
        cfg = load_public_config(args.config)
        _validate_account_config(account, cfg)
        snapshot = load_broker_snapshot(args.snapshot)
        summary = sync_broker_snapshot(account, snapshot, cfg=cfg)''')
