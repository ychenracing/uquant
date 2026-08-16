from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from uquant.account import save_account
from uquant.data import DataStore
from uquant.engine import code_fingerprint
from uquant.types import AccountState
from uquant.validation import ai_era as ai_era_module
from uquant.validation import holdout as holdout_module
from uquant.validation.ai_era import AI_ERA_WINDOWS
from uquant.validation.holdout import (
    HOLDOUT_DATA_DIRECTORY,
    HOLDOUT_START,
    LAST_IN_SAMPLE_DATE,
    HoldoutBinding,
    _assemble_future_holdout_manifest,
    _strategy_account_code_sha256,
    _strategy_source_sha256,
    _validate_future_holdout_manifest_payload,
    _validated_strategy_cli_sha256,
    build_future_holdout_manifest,
    current_holdout_binding,
    holdout_data_identity,
    holdout_source_sha256,
    load_future_holdout_contract,
    maximum_observed_market_date,
    validate_future_holdout_manifest,
    validate_holdout_layout,
    validate_prior_close_account,
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
        strategy_source_sha256=(
            "c5f819e3f164bd96089f746ec8c850bfed42c13e1668bb11e7b63647c5677ed6"
        ),
        strategy_cli_sha256=(
            "db34c26631b9b64c6d359149b927f0bee86c89dba74360efddc922342b6f24ad"
        ),
        effective_config_sha256=(
            "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
        ),
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


def _single_holdout_file_sha256(relative: str, content: bytes) -> str:
    digest = hashlib.sha256()
    encoded_relative = relative.encode()
    digest.update(len(encoded_relative).to_bytes(4, "big"))
    digest.update(encoded_relative)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)
    return digest.hexdigest()


def test_tracked_contract_freezes_date_path_policy_and_null_scores() -> None:
    contract = load_future_holdout_contract()

    assert contract.last_in_sample_date == "2026-08-05"
    assert contract.first_holdout_date == "2026-08-06"
    assert contract.data_directory == "data/holdout/phase2-future-v1"
    assert contract.review_milestones == (40, 60)
    assert contract.parameter_changes_from_observation is False
    assert dict(contract.phase1_windows) == dict(AI_ERA_WINDOWS)
    assert contract.strategy_anchor_commit == "388125839a196560e0d4d67d55ea8ad794652289"
    assert (
        contract.strategy_source_sha256
        == "c5f819e3f164bd96089f746ec8c850bfed42c13e1668bb11e7b63647c5677ed6"
    )
    assert (
        contract.strategy_config_sha256
        == "ed52da44a359c1506e1d299f7bc341ad01b199d7f96997f7c01f2b8eca7cfc13"
    )
    assert (
        contract.strategy_cli_sha256
        == "db34c26631b9b64c6d359149b927f0bee86c89dba74360efddc922342b6f24ad"
    )
    assert (
        contract.strategy_account_code_sha256
        == "f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7"
    )
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
    windows = dict(AI_ERA_WINDOWS)

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
            phase1_windows={
                **AI_ERA_WINDOWS,
                "continuous_ai_era": ("2023-01-03", "2026-08-06"),
            },
        )


def test_layout_requires_the_observed_frozen_market_boundary(tmp_path: Path) -> None:
    contract = load_future_holdout_contract()
    _csv(tmp_path / "data/frozen/a.csv", "2026-08-04")

    with pytest.raises(RuntimeError, match="maximum observed economic market date"):
        validate_holdout_layout(
            tmp_path,
            contract=contract,
            phase1_windows=AI_ERA_WINDOWS,
        )


def test_layout_requires_frozen_data_and_exact_official_windows(tmp_path: Path) -> None:
    contract = load_future_holdout_contract()
    with pytest.raises(RuntimeError, match="data/frozen"):
        validate_holdout_layout(tmp_path, contract=contract)

    _csv(tmp_path / "data/frozen/a.csv", LAST_IN_SAMPLE_DATE)
    missing = dict(AI_ERA_WINDOWS)
    missing.pop("h1_2023")
    with pytest.raises(RuntimeError, match="official Phase 1 windows"):
        validate_holdout_layout(tmp_path, contract=contract, phase1_windows=missing)

    added = {**AI_ERA_WINDOWS, "new_window": ("2026-08-01", LAST_IN_SAMPLE_DATE)}
    with pytest.raises(RuntimeError, match="official Phase 1 windows"):
        validate_holdout_layout(tmp_path, contract=contract, phase1_windows=added)

    moved_start = {**AI_ERA_WINDOWS, "h1_2023": ("2023-01-04", "2023-06-30")}
    with pytest.raises(RuntimeError, match="official Phase 1 windows"):
        validate_holdout_layout(tmp_path, contract=contract, phase1_windows=moved_start)


@pytest.mark.parametrize(
    ("name", "bounds"),
    (
        ("continuous_ai_era", ("2023-01-03", "2026-08-06")),
        ("h1_2023", ("2023-01-02", "2023-06-30")),
    ),
)
def test_layout_rejects_mutated_live_phase1_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    bounds: tuple[str, str],
) -> None:
    _csv(tmp_path / "data/frozen/a.csv", LAST_IN_SAMPLE_DATE)
    mutated = {**AI_ERA_WINDOWS, name: bounds}
    monkeypatch.setattr(holdout_module, "AI_ERA_WINDOWS", mutated)
    monkeypatch.setattr(ai_era_module, "AI_ERA_WINDOWS", mutated)

    with pytest.raises(RuntimeError, match="sealed Phase 1 windows"):
        validate_holdout_layout(
            tmp_path,
            contract=load_future_holdout_contract(),
            phase1_windows=mutated,
        )


def test_layout_rejects_a_forged_contract_dataclass(tmp_path: Path) -> None:
    _csv(tmp_path / "data/frozen/a.csv", LAST_IN_SAMPLE_DATE)
    forged = replace(
        load_future_holdout_contract(),
        data_directory="data/holdout/unsealed-forgery",
    )

    with pytest.raises(ValueError, match="reviewed sealed contract"):
        validate_holdout_layout(tmp_path, contract=forged)


def test_holdout_data_rejects_symlinks_and_unknown_file_types(tmp_path: Path) -> None:
    contract = load_future_holdout_contract()
    _csv(tmp_path / "data/frozen/a.csv", LAST_IN_SAMPLE_DATE)
    outside = tmp_path / "outside"
    _csv(outside / "a.csv", HOLDOUT_START)
    holdout = tmp_path / HOLDOUT_DATA_DIRECTORY
    holdout.parent.mkdir(parents=True)
    holdout.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        validate_holdout_layout(tmp_path, contract=contract)
    holdout.unlink()

    holdout.mkdir()
    (holdout / "linked.csv").symlink_to(outside / "a.csv")
    with pytest.raises(RuntimeError, match="symlink"):
        validate_holdout_layout(tmp_path, contract=contract)
    (holdout / "linked.csv").unlink()

    (holdout / "future.parquet").write_bytes(b"not-csv")
    with pytest.raises(RuntimeError, match="unsupported"):
        validate_holdout_layout(tmp_path, contract=contract)
    with pytest.raises(ValueError, match="unsupported"):
        holdout_data_identity(holdout)


def test_current_binding_refuses_a_mixed_repository_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="owning repository"):
        current_holdout_binding(tmp_path)


def _minimal_strategy_tree(root: Path) -> None:
    files = {
        "uquant/decision.py": "decision = 1\n",
        "uquant/cli.py": (
            "def main(args):\n"
            "    if args.command == 'daily':\n"
            "        engine = ProductionEngine(args.data_dir)\n"
            "        account = load_account(args.account)\n"
            "        decision = engine.decide(account=account)\n"
            "        account.pending_orders = list(decision.pending_orders)\n"
            "        save_account(account, args.account)\n"
        ),
        "uquant/validation/ai_era.py": "windows = 1\n",
        "uquant/validation/resources/ai_universe_manifest.json": "{}\n",
        "benchmarks/reference_registry.json": "{}\n",
        "benchmarks/config_parameter_governance.json": "{}\n",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    (
        "uquant/validation/ai_era.py",
        "uquant/validation/resources/ai_universe_manifest.json",
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
    ),
)
def test_strategy_anchor_hash_covers_transitive_sources_and_resources(
    tmp_path: Path,
    relative: str,
) -> None:
    _minimal_strategy_tree(tmp_path)
    before = _strategy_source_sha256(tmp_path)
    (tmp_path / relative).write_text("mutated\n", encoding="utf-8")

    assert _strategy_source_sha256(tmp_path) != before


def test_strategy_anchor_hash_closes_the_recursive_path_inventory(tmp_path: Path) -> None:
    _minimal_strategy_tree(tmp_path)
    before = _strategy_source_sha256(tmp_path)
    added = tmp_path / "uquant/validation/new_decision_rule.py"
    added.write_text("new_rule = True\n", encoding="utf-8")

    assert _strategy_source_sha256(tmp_path) != before


@pytest.mark.parametrize(
    "relative",
    (
        "uquant/validation/ci_artifacts.py",
        "uquant/validation/equivalence.py",
    ),
)
def test_strategy_anchor_ignores_gate_only_operational_validation_tools(
    tmp_path: Path,
    relative: str,
) -> None:
    _minimal_strategy_tree(tmp_path)
    before = _strategy_source_sha256(tmp_path)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("gate_only = True\n", encoding="utf-8")

    assert _strategy_source_sha256(tmp_path) == before


def test_strategy_anchor_hash_covers_cli_decision_and_account_persistence(
    tmp_path: Path,
) -> None:
    _minimal_strategy_tree(tmp_path)
    cli_hash = getattr(holdout_module, "_strategy_cli_sha256", None)
    assert cli_hash is not None
    before = cli_hash(tmp_path)
    (tmp_path / "uquant/cli.py").write_text(
        "def main(args):\n"
        "    if args.command == 'daily':\n"
        "        engine = ProductionEngine(args.data_dir, cfg=forged_config)\n"
        "        account = load_account(args.account)\n"
        "        decision = engine.decide(account=account)\n"
        "        account.pending_orders = []\n"
        "        save_account(account, args.account)\n",
        encoding="utf-8",
    )

    assert cli_hash(tmp_path) != before
    repository_root = Path(holdout_module.__file__).resolve().parents[2]
    assert (
        _validated_strategy_cli_sha256(repository_root)
        == "db34c26631b9b64c6d359149b927f0bee86c89dba74360efddc922342b6f24ad"
    )


def test_cli_anchor_does_not_subtract_side_effecting_operational_parser_code(
    tmp_path: Path,
) -> None:
    cli = tmp_path / "uquant/cli.py"
    cli.parent.mkdir(parents=True)
    cli.write_text(
        "def _parser(sub):\n"
        "    return sub\n",
        encoding="utf-8",
    )
    before = holdout_module._strategy_cli_sha256(tmp_path)
    cli.write_text(
        "def _parser(sub):\n"
        "    holdout = sub.add_parser(\n"
        "        'holdout-manifest', help=mutate_default_config()\n"
        "    )\n"
        "    return sub\n",
        encoding="utf-8",
    )

    assert holdout_module._strategy_cli_sha256(tmp_path) != before

    cli.write_text(
        "def main(args):\n"
        "    return 2\n",
        encoding="utf-8",
    )
    before = holdout_module._strategy_cli_sha256(tmp_path)
    cli.write_text(
        "def main(args):\n"
        "    if args.command == 'holdout-manifest':\n"
        "        return 0\n"
        "    else:\n"
        "        mutate_default_config()\n"
        "    return 2\n",
        encoding="utf-8",
    )

    assert holdout_module._strategy_cli_sha256(tmp_path) != before


def test_null_manifest_carries_prior_close_state_and_rejects_metrics() -> None:
    contract = load_future_holdout_contract()
    binding = _binding()
    account = _account()

    manifest = _assemble_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
        holdout_data_sha256="a" * 64,
    )

    assert manifest["observation"] == {
        "session_count": 0,
        "first_session": None,
        "last_session": None,
        "metrics_sha256": None,
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
        _assemble_future_holdout_manifest(
            contract=contract,
            binding=binding,
            account_payload=account,
            holdout_sessions=(),
            scores={"final_wealth": 1.0},
            holdout_data_sha256="a" * 64,
        )


def test_prior_close_account_carries_the_exact_frozen_candidate_code_hash(
    tmp_path: Path,
) -> None:
    repository_root = Path(holdout_module.__file__).resolve().parents[2]
    assert (
        _strategy_account_code_sha256(repository_root)
        == "f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7"
    )
    frozen = tmp_path / "data/frozen"
    _csv(frozen / "sz300308.csv", "2026-08-04", LAST_IN_SAMPLE_DATE)
    account = _account()
    account.update(
        code_hash="f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7",
        data_hash_symbols=["sz300308"],
        data_hash=DataStore(frozen).manifest(
            ["sz300308"], as_of=LAST_IN_SAMPLE_DATE
        ).digest,
    )

    validate_prior_close_account(account, frozen_data_dir=frozen)

    assert code_fingerprint() == account["code_hash"]
    mutable_current_code = {**account, "code_hash": "0" * 64}
    with pytest.raises(ValueError, match="frozen candidate"):
        validate_prior_close_account(mutable_current_code, frozen_data_dir=frozen)

    stale_data = {**account, "data_hash": "1" * 64}
    with pytest.raises(ValueError, match="frozen prefix"):
        validate_prior_close_account(stale_data, frozen_data_dir=frozen)


def test_manifest_readback_rebuilds_observed_evidence_from_authoritative_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(holdout_module, "_repository_root", lambda: tmp_path)
    monkeypatch.setattr(
        holdout_module,
        "current_holdout_binding",
        lambda repository_root=None: _binding(),
    )
    contract_source = Path("benchmarks/future_holdout_contract.json")
    contract_path = tmp_path / "benchmarks/future_holdout_contract.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_bytes(contract_source.read_bytes())
    frozen = tmp_path / "data/frozen"
    _csv(frozen / "sz300308.csv", LAST_IN_SAMPLE_DATE)
    holdout_content = (
        "date,open,high,low,close,volume\n"
        f"{HOLDOUT_START},10,11,9,10,100\n"
    ).encode()
    holdout_path = tmp_path / HOLDOUT_DATA_DIRECTORY / "sz300308.csv"
    holdout_path.parent.mkdir(parents=True)
    holdout_path.write_bytes(holdout_content)
    account = AccountState.empty(1_000_000.0)
    account.last_successful_run = LAST_IN_SAMPLE_DATE
    account.data_hash_as_of = LAST_IN_SAMPLE_DATE
    account.data_hash_symbols = ["sz300308"]
    account.data_hash = DataStore(frozen).manifest(
        ["sz300308"], as_of=LAST_IN_SAMPLE_DATE
    ).digest
    account.code_hash = (
        "f43e1e93859169df056051ad1963b761e35143be31b321bf11883726218c5dc7"
    )
    account_path = tmp_path / "account.json"
    save_account(account, account_path)
    metrics = {
        "schema_version": 1,
        "holdout_data_sha256": _single_holdout_file_sha256(
            "sz300308.csv", holdout_content
        ),
        "sessions": [HOLDOUT_START],
        "scores": {
            "final_wealth": 1_010_000.0,
            "max_drawdown": 0.02,
            "account_orders": 1,
            "gross_turnover": 0.1,
            "top1_concentration": 0.5,
            "top3_concentration": 0.8,
            "pnl_hhi": 0.4,
        },
    }
    metrics_path = tmp_path / "holdout_metrics.json"
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    manifest = build_future_holdout_manifest(
        account_path=account_path,
        metrics_path=metrics_path,
        repository_root=tmp_path,
    )
    assert manifest["observation"]["session_count"] == 1
    assert manifest["observation"]["metrics_sha256"] == hashlib.sha256(
        metrics_path.read_bytes()
    ).hexdigest()
    forged = json.loads(json.dumps(manifest))
    forged["scores"]["final_wealth"] = 99_000_000.0
    forged["canonical_sha256"] = hashlib.sha256(
        json.dumps(
            {key: value for key, value in forged.items() if key != "canonical_sha256"},
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    manifest_path = tmp_path / "future_holdout_manifest.json"
    manifest_path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(ValueError, match="stale"):
        validate_future_holdout_manifest(
            manifest_path=manifest_path,
            account_path=account_path,
            metrics_path=metrics_path,
            repository_root=tmp_path,
        )


def test_holdout_identity_uses_one_immutable_byte_snapshot_per_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "future.csv"
    original = (
        "date,open,high,low,close,volume\n"
        f"{HOLDOUT_START},10,11,9,10,100\n"
    ).encode()
    replacement = original.replace(HOLDOUT_START.encode(), b"2026-08-07")
    path.write_bytes(original)
    original_read_bytes = Path.read_bytes
    snapshots = 0

    def replace_after_snapshot(candidate: Path) -> bytes:
        nonlocal snapshots
        content = original_read_bytes(candidate)
        if candidate == path:
            snapshots += 1
            candidate.write_bytes(replacement)
        return content

    monkeypatch.setattr(Path, "read_bytes", replace_after_snapshot)

    sessions, data_sha256 = holdout_data_identity(tmp_path)

    assert snapshots == 1
    assert sessions == (HOLDOUT_START,)
    assert data_sha256 == _single_holdout_file_sha256("future.csv", original)


def test_manifest_fails_closed_when_state_binding_or_policy_is_stale() -> None:
    contract = load_future_holdout_contract()
    binding = _binding()
    account = _account()
    manifest = _assemble_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
        holdout_data_sha256="a" * 64,
    )

    _validate_future_holdout_manifest_payload(manifest, expected=manifest)

    changed_account = json.loads(json.dumps(account))
    changed_account["cash"] = 999_999.0
    with pytest.raises(ValueError, match="stale"):
        _validate_future_holdout_manifest_payload(
            manifest,
            expected=_assemble_future_holdout_manifest(
                contract=contract,
                binding=binding,
                account_payload=changed_account,
                holdout_sessions=(),
                holdout_data_sha256="a" * 64,
            ),
        )

    with pytest.raises(ValueError, match="decision path or config drifted"):
        _assemble_future_holdout_manifest(
            contract=contract,
            binding=replace(binding, strategy_source_sha256="7" * 64),
            account_payload=account,
            holdout_sessions=(),
            holdout_data_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="decision path or config drifted"):
        _assemble_future_holdout_manifest(
            contract=contract,
            binding=replace(binding, effective_config_sha256="8" * 64),
            account_payload=account,
            holdout_sessions=(),
            holdout_data_sha256="a" * 64,
        )

    changed_manifest = json.loads(json.dumps(manifest))
    changed_manifest["observation"]["parameter_changes_from_observation"] = True
    with pytest.raises(ValueError, match="parameter changes"):
        _validate_future_holdout_manifest_payload(
            changed_manifest,
            expected=manifest,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("final_wealth", 0.0),
        ("max_drawdown", -0.01),
        ("max_drawdown", 1.01),
        ("account_orders", -1),
        ("gross_turnover", -0.01),
        ("top1_concentration", 1.01),
        ("top3_concentration", -0.01),
        ("top3_concentration", 0.3),
        ("pnl_hhi", 1.01),
    ),
)
def test_observed_scores_enforce_metric_specific_bounds(field: str, value: float) -> None:
    scores: dict[str, float | int | None] = {
        "final_wealth": 1_000_000.0,
        "max_drawdown": 0.1,
        "account_orders": 1,
        "gross_turnover": 0.5,
        "top1_concentration": 0.4,
        "top3_concentration": 0.8,
        "pnl_hhi": 0.3,
    }
    scores[field] = value
    with pytest.raises(ValueError, match=field):
        _assemble_future_holdout_manifest(
            contract=load_future_holdout_contract(),
            binding=_binding(),
            account_payload=_account(),
            holdout_sessions=(HOLDOUT_START,),
            scores=scores,
            holdout_data_sha256="a" * 64,
            metrics_sha256="b" * 64,
        )
