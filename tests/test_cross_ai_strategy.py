from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest


def test_principal_removal_cases_remove_all_reference_roles() -> None:
    from research.cross_ai_strategy import case_symbols
    from uquant.contracts.universe import default_ai_universe

    universe = default_ai_universe()
    core = {"sz300308", "sz300502", "sz300394"}
    optical = {member.symbol for member in universe.members if member.industry == "optical"}
    for case, removed in (("remove_all_three", core), ("no_optical", optical)):
        roles = case_symbols(case, "2023-01-03")
        expected = set(universe.symbols_as_of("2023-01-03")) - removed
        assert set(roles["tradable"]) == expected
        assert set(roles["qualification"]) == expected
        assert set(roles["risk"]) == expected
        assert roles["indexes"] == ("sh000300", "sh000682")


def test_small_champion_replay_uses_real_next_open_and_sealed_evidence(tmp_path: Path) -> None:
    from research.cross_ai_strategy import run_production_case
    from uquant.account import load_account

    result = run_production_case(
        case_id="champion", start="2023-01-03", end="2023-01-10", output_dir=tmp_path / "champion"
    )
    assert result["status"] == "COMPLETE", result.get("error")
    assert result["sessions"] == 6
    assert result["accounting"]["reconciled"]
    raw = tmp_path / "champion" / "observations.jsonl.gz"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == result["raw_sha256"]
    with gzip.open(raw, "rt", encoding="utf-8") as stream:
        observations = [json.loads(line) for line in stream]
    assert len(observations) == 6
    assert observations[0]["date"] == "2023-01-03"
    account_path = tmp_path / "champion" / "final_account.json"
    assert hashlib.sha256(account_path.read_bytes()).hexdigest() == result["final_account_sha256"]
    account = load_account(account_path)
    assert account.fills
    assert account.fills[0].fill_date == "2023-01-05"
    assert any(fill.fill_date == "2023-01-10" for fill in account.fills)
    assert len(account.order_ledger) == 1
    assert {fill.order_id for fill in account.fills} == {account.order_ledger[0].order_id}
    assert account.order_ledger[0].requested_shares == (
        account.order_ledger[0].filled_shares + account.order_ledger[0].remaining_shares
    )
    assert all(fill.signal_date < fill.fill_date for fill in account.fills)
    assert result["identity"]["runtime"]["numpy_version"] == "2.5.1"
    with pytest.raises(FileExistsError):
        run_production_case(
            case_id="champion", start="2023-01-03", end="2023-01-10", output_dir=tmp_path / "champion"
        )


def test_diagnostic_replay_rejects_protected_period_before_creating_output(tmp_path: Path) -> None:
    from research.cross_ai_strategy import run_production_case

    output = tmp_path / "forbidden"
    with pytest.raises(ValueError, match="historical"):
        run_production_case(
            case_id="full", start="2026-08-06", end="2026-08-07", output_dir=output
        )
    assert not output.exists()


def test_failed_replay_retains_literal_case_and_account(tmp_path: Path) -> None:
    from research.cross_ai_strategy import run_production_case
    from uquant.account import load_account
    from uquant.contracts.strict_json import canonical_json_bytes

    output = tmp_path / "incomplete"
    result = run_production_case(
        case_id="no_optical", start="2023-01-03", end="2023-01-03", output_dir=output,
    )
    assert result["status"] == "REPLAY_ERROR"
    assert "fewer than two sessions" in result["error"]
    assert result["sessions"] == 0
    assert result["expected_sessions"] == 1
    assert not result["accounting"]["reconciled"]
    assert not result["authoritative_acceptance"]
    restored = json.loads((output / "result.json").read_text())
    seal = restored.pop("canonical_sha256")
    assert seal == hashlib.sha256(canonical_json_bytes(restored)).hexdigest()
    assert json.loads((output / "final_account.json").read_text())["cash"] == 2_000_000.0
    with pytest.raises(RuntimeError, match="validation hashes"):
        load_account(output / "final_account.json")
    assert "validation hashes" in result["error"]
    assert (output / "error.txt").exists()
