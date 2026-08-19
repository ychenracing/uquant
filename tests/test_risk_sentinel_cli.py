from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from uquant.account import save_account
from uquant.data import DataStore
from uquant.engine import code_fingerprint
from uquant.risk_sentinel.cli import run_shadow
from uquant.types import AccountState
from uquant.validation.universe import load_ai_universe

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "frozen"
AS_OF = "2026-08-05"


def _account(path: Path) -> Path:
    universe = load_ai_universe()
    symbols = universe.symbols_as_of(AS_OF)
    account = AccountState.empty(1_000_000.0)
    account.last_successful_run = AS_OF
    account.data_hash_as_of = AS_OF
    account.data_hash_symbols = list(symbols)
    account.data_hash = DataStore(DATA).manifest(symbols, as_of=AS_OF).digest
    account.code_hash = code_fingerprint()
    save_account(account, path)
    return path


def test_shadow_cli_is_read_only_deterministic_and_fully_provenanced(tmp_path: Path) -> None:
    account = _account(tmp_path / "account.json")
    output = tmp_path / "sentinel.json"
    before = account.read_bytes()

    first = run_shadow(
        data_dir=DATA,
        as_of=AS_OF,
        account_path=account,
        output_path=output,
        repository_root=ROOT,
    )
    first_json = output.read_bytes()
    first_markdown = output.with_suffix(".md").read_bytes()
    second = run_shadow(
        data_dir=DATA,
        as_of=AS_OF,
        account_path=account,
        output_path=output,
        repository_root=ROOT,
    )

    assert account.read_bytes() == before
    assert output.read_bytes() == first_json
    assert output.with_suffix(".md").read_bytes() == first_markdown
    assert first == second
    assert first["assessment"]["date"] == AS_OF
    assert first["provenance"]["account_sha256"] == hashlib.sha256(before).hexdigest()
    assert set(first["provenance"]) == {
        "account_sha256",
        "config_sha256",
        "data",
        "repository_commit",
        "runtime",
        "sentinel_source_sha256",
        "universe_sha256",
    }
    latest = json.loads((tmp_path / "latest_success.json").read_text(encoding="utf-8"))
    assert latest == {
        "artifact": "sentinel.json",
        "canonical_sha256": first["canonical_sha256"],
        "date": AS_OF,
    }


def test_shadow_cli_rejects_input_aliases_and_preserves_latest_on_failure(
    tmp_path: Path,
) -> None:
    account = _account(tmp_path / "account.json")
    output = tmp_path / "sentinel.json"
    run_shadow(
        data_dir=DATA,
        as_of=AS_OF,
        account_path=account,
        output_path=output,
        repository_root=ROOT,
    )
    latest = (tmp_path / "latest_success.json").read_bytes()

    with pytest.raises(ValueError, match="protected"):
        run_shadow(
            data_dir=DATA,
            as_of=AS_OF,
            account_path=account,
            output_path=account,
            repository_root=ROOT,
        )
    with pytest.raises(ValueError, match="protected input tree"):
        run_shadow(
            data_dir=DATA,
            as_of=AS_OF,
            account_path=account,
            output_path=DATA / "sentinel.json",
            repository_root=ROOT,
        )
    with pytest.raises(RuntimeError, match="contracted frozen-data session"):
        run_shadow(
            data_dir=DATA,
            as_of="2026-08-19",
            account_path=account,
            output_path=output,
            repository_root=ROOT,
        )

    assert (tmp_path / "latest_success.json").read_bytes() == latest
