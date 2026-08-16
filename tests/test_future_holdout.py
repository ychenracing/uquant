from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    HoldoutBinding,
    build_future_holdout_manifest,
    holdout_source_sha256,
    load_future_holdout_contract,
    maximum_observed_market_date,
    validate_future_holdout_manifest,
    validate_holdout_layout,
)


def _csv(path: Path, *dates: str) -> None:
    rows = ["date,open,high,low,close,volume"]
    rows.extend(f"{date},10,11,9,10,100" for date in dates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _binding() -> HoldoutBinding:
    return HoldoutBinding(
        production_commit="1" * 40,
        production_source_sha256="2" * 64,
        effective_config_sha256="3" * 64,
        universe_sha256="4" * 64,
        industry_sha256="5" * 64,
        python_full_version="3.12.13",
        numpy_version="2.5.1",
        pandas_version="3.0.5",
        uv_version="0.11.33",
        uv_lock_sha256="6" * 64,
    )


def _account() -> dict[str, object]:
    return {
        "last_successful_run": LAST_IN_SAMPLE_DATE,
        "data_hash_as_of": LAST_IN_SAMPLE_DATE,
        "cash": 1_000_000.0,
        "positions": {
            "sz300308": {
                "shares": 100,
                "tranches": [{"shares": 100, "sellable_date": HOLDOUT_START}],
            }
        },
        "pending_orders": [
            {"signal_date": LAST_IN_SAMPLE_DATE, "symbol": "sz300308", "side": "SELL"}
        ],
        "risk": "NORMAL",
        "risk_streaks": {"caution": 0},
    }


def test_tracked_contract_freezes_date_path_policy_and_null_scores() -> None:
    contract = load_future_holdout_contract()

    assert contract.last_in_sample_date == "2026-08-05"
    assert contract.first_holdout_date == "2026-08-06"
    assert contract.data_directory == "data/holdout/phase2-future-v1"
    assert contract.review_milestones == (40, 60)
    assert contract.parameter_changes_from_observation is False
    assert contract.score_fields == (
        "final_wealth",
        "max_drawdown",
        "account_orders",
        "gross_turnover",
        "top1_concentration",
        "top3_concentration",
        "pnl_hhi",
    )


def test_tracked_contract_rejects_a_locally_resealed_edit(tmp_path: Path) -> None:
    source = Path("benchmarks/future_holdout_contract.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["dates"]["first_holdout"] = "2026-08-07"
    unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}
    payload["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            unsealed,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    edited = tmp_path / "future_holdout_contract.json"
    edited.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="reviewed contract"):
        load_future_holdout_contract(edited)


def test_maximum_observed_market_date_finds_frozen_boundary(tmp_path: Path) -> None:
    _csv(tmp_path / "a.csv", "2026-08-04", "2026-08-05")
    _csv(tmp_path / "b.csv", "2026-08-01", "2026-08-03")

    assert maximum_observed_market_date(tmp_path) == "2026-08-05"


def test_holdout_source_identity_covers_nested_source_contracts_and_lock(tmp_path: Path) -> None:
    paths = (
        "uquant/a.py",
        "uquant/validation/nested.py",
        "uquant/validation/resources/universe.json",
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
        "benchmarks/future_holdout_contract.json",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
    )
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative, encoding="utf-8")

    before = holdout_source_sha256(tmp_path)
    (tmp_path / "uquant/validation/nested.py").write_text("changed", encoding="utf-8")
    after_source = holdout_source_sha256(tmp_path)
    (tmp_path / "uv.lock").write_text("changed lock", encoding="utf-8")
    after_lock = holdout_source_sha256(tmp_path)

    assert len({before, after_source, after_lock}) == 3


def test_layout_isolates_future_rows_and_rejects_expanded_phase1_windows(
    tmp_path: Path,
) -> None:
    contract = load_future_holdout_contract()
    _csv(tmp_path / "data/frozen/a.csv", LAST_IN_SAMPLE_DATE)
    _csv(tmp_path / HOLDOUT_DATA_DIRECTORY / "a.csv", HOLDOUT_START)
    windows = {"continuous_ai_era": ("2023-01-03", LAST_IN_SAMPLE_DATE)}

    validate_holdout_layout(tmp_path, contract=contract, phase1_windows=windows)

    _csv(tmp_path / "data/frozen/leak.csv", "2026-08-07")
    with pytest.raises(RuntimeError, match="holdout data entered data/frozen"):
        validate_holdout_layout(tmp_path, contract=contract, phase1_windows=windows)
    (tmp_path / "data/frozen/leak.csv").unlink()

    _csv(tmp_path / "data/holdout/wrong-version/a.csv", HOLDOUT_START)
    with pytest.raises(RuntimeError, match="outside the isolated holdout"):
        validate_holdout_layout(tmp_path, contract=contract, phase1_windows=windows)
    (tmp_path / "data/holdout/wrong-version/a.csv").unlink()

    with pytest.raises(RuntimeError, match="Phase 1 window expanded"):
        validate_holdout_layout(
            tmp_path,
            contract=contract,
            phase1_windows={"continuous_ai_era": ("2023-01-03", "2026-08-06")},
        )


def test_layout_requires_the_observed_frozen_market_boundary(tmp_path: Path) -> None:
    contract = load_future_holdout_contract()
    _csv(tmp_path / "data/frozen/a.csv", "2026-08-04")

    with pytest.raises(RuntimeError, match="maximum observed economic market date"):
        validate_holdout_layout(
            tmp_path,
            contract=contract,
            phase1_windows={"continuous_ai_era": ("2023-01-03", LAST_IN_SAMPLE_DATE)},
        )


def test_null_manifest_carries_prior_close_state_and_rejects_metrics() -> None:
    contract = load_future_holdout_contract()
    binding = _binding()
    account = _account()

    manifest = build_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
    )

    assert manifest["observation"] == {
        "session_count": 0,
        "first_session": None,
        "last_session": None,
        "parameter_changes_from_observation": False,
    }
    assert all(value is None for value in manifest["scores"].values())
    assert set(manifest["prior_close_state"]) == {
        "as_of",
        "account_sha256",
        "positions_sha256",
        "tranches_sha256",
        "pending_orders_sha256",
        "strategy_state_sha256",
    }

    with pytest.raises(ValueError, match="scores must be null when no holdout sessions exist"):
        build_future_holdout_manifest(
            contract=contract,
            binding=binding,
            account_payload=account,
            holdout_sessions=(),
            scores={"final_wealth": 1.0},
        )


def test_manifest_fails_closed_when_state_binding_or_policy_is_stale() -> None:
    contract = load_future_holdout_contract()
    binding = _binding()
    account = _account()
    manifest = build_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
    )

    validate_future_holdout_manifest(
        manifest,
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
    )

    changed_account = json.loads(json.dumps(account))
    changed_account["cash"] = 999_999.0
    with pytest.raises(ValueError, match="stale"):
        validate_future_holdout_manifest(
            manifest,
            contract=contract,
            binding=binding,
            account_payload=changed_account,
            holdout_sessions=(),
        )

    changed_manifest = json.loads(json.dumps(manifest))
    changed_manifest["observation"]["parameter_changes_from_observation"] = True
    with pytest.raises(ValueError, match="parameter changes"):
        validate_future_holdout_manifest(
            changed_manifest,
            contract=contract,
            binding=binding,
            account_payload=account,
            holdout_sessions=(),
        )
