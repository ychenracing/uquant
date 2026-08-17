from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import uquant.atomic_io as atomic_io_module
import uquant.validation as validation_package
from uquant.atomic_io import (
    _aliases,
    _fsync_directory,
    atomic_write_bytes,
    atomic_write_text,
    validate_atomic_output_boundary,
)
from uquant.data import DataContractError
from uquant.validation import equivalence as equivalence_module
from uquant.validation import holdout as holdout_module
from uquant.validation import universe as universe_module
from uquant.validation.equivalence import Phase1Case, Phase1DecisionTrace
from uquant.validation.holdout import HoldoutBinding, current_holdout_binding
from uquant.validation.manifest import _checksum_entries, verify_data_manifest
from uquant.validation.replay_evidence import VerifiedMarketData

ROOT = Path(__file__).resolve().parents[1]


def _holdout_binding() -> HoldoutBinding:
    contract = holdout_module.load_future_holdout_contract()
    return HoldoutBinding(
        production_commit="1" * 40,
        production_source_sha256="2" * 64,
        strategy_source_sha256=contract.strategy_source_sha256,
        strategy_cli_sha256=contract.strategy_cli_sha256,
        effective_config_sha256=contract.strategy_config_sha256,
        universe_sha256="6" * 64,
        industry_sha256="7" * 64,
        python_full_version="3.12.13",
        numpy_version="2.5.1",
        pandas_version="3.0.5",
        uv_version="0.11.33",
        uv_lock_sha256="8" * 64,
    )


def _write_snapshot(root: Path, *, symbol: str = "sh000300") -> tuple[Path, str]:
    content = b"date,open,high,low,close,volume\n2026-08-05,10,11,9,10,100\n"
    digest = hashlib.sha256(content).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    csv_path = root / f"{symbol}.csv"
    csv_path.write_bytes(content)
    (root / "SHA256SUMS").write_text(f"{digest}  {symbol}.csv\n", encoding="utf-8")
    (root / "DATA_MANIFEST.json").write_text(
        json.dumps(
            {
                "snapshot_id": "gate-edge-fixture",
                "results": [{"symbol": symbol, "sha256": digest}],
            }
        ),
        encoding="utf-8",
    )
    return csv_path, digest


def test_lazy_validation_exports_route_every_public_gate() -> None:
    for name in validation_package.__all__:
        assert callable(getattr(validation_package, name))
    with pytest.raises(AttributeError, match="not_a_gate"):
        validation_package.__getattr__("not_a_gate")


def test_atomic_output_covers_success_alias_and_platform_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "nested/result.txt"
    atomic_write_text(destination, "complete\n")
    assert destination.read_text(encoding="utf-8") == "complete\n"

    with pytest.raises(ValueError, match="aliases a protected path"):
        atomic_write_text(destination, "forbidden", protected_paths=(destination,))
    hardlink = tmp_path / "hardlink.txt"
    os.link(destination, hardlink)
    assert _aliases(destination, hardlink) is True
    with pytest.raises(ValueError, match="aliases a protected path"):
        atomic_write_text(hardlink, "forbidden", protected_paths=(destination,))

    monkeypatch.setattr(os.path, "samefile", lambda *_: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ValueError, match="cannot verify protected path identity"):
        _aliases(destination, hardlink)
    with pytest.raises(ValueError, match="cannot verify protected path identity"):
        atomic_write_text(hardlink, "forbidden", protected_paths=(destination,))
    assert destination.read_text(encoding="utf-8") == "complete\n"
    monkeypatch.setattr(os, "name", "nt")
    _fsync_directory(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_atomic_output_preserves_existing_modes_and_new_files_honor_umask(
    tmp_path: Path,
) -> None:
    """Catches replacement publishing mkstemp's private mode as file policy."""

    existing_text = tmp_path / "existing.txt"
    existing_text.write_text("prior", encoding="utf-8")
    existing_text.chmod(0o640)
    atomic_write_text(existing_text, "updated")
    assert stat.S_IMODE(existing_text.stat().st_mode) == 0o640

    existing_bytes = tmp_path / "existing.bin"
    existing_bytes.write_bytes(b"prior")
    existing_bytes.chmod(0o644)
    atomic_write_bytes(existing_bytes, b"updated")
    assert stat.S_IMODE(existing_bytes.stat().st_mode) == 0o644

    prior_umask = os.umask(0o027)
    try:
        new_text = tmp_path / "new.txt"
        new_bytes = tmp_path / "new.bin"
        atomic_write_text(new_text, "new")
        atomic_write_bytes(new_bytes, b"new")
    finally:
        os.umask(prior_umask)
    assert stat.S_IMODE(new_text.stat().st_mode) == 0o640
    assert stat.S_IMODE(new_bytes.stat().st_mode) == 0o640


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_atomic_output_tightens_an_existing_private_mode_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a private replacement being staged temporarily as world-readable."""

    destination = tmp_path / "private.txt"
    destination.write_text("prior", encoding="utf-8")
    destination.chmod(0o600)
    observed_payloads: list[bytes] = []
    real_fchmod = os.fchmod

    def inspect_mode(descriptor: int, mode: int) -> None:
        staged = tuple(tmp_path.glob(".private.txt.*"))
        assert len(staged) == 1
        observed_payloads.append(staged[0].read_bytes())
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", inspect_mode)
    prior_umask = os.umask(0o022)
    try:
        atomic_write_text(destination, "private payload")
    finally:
        os.umask(prior_umask)

    assert observed_payloads == [b""]
    assert destination.read_text(encoding="utf-8") == "private payload"


def test_atomic_output_boundary_failures_remain_attributable_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercises resolution, inventory, mode, collision, and cleanup failures."""

    destination = tmp_path / "output.json"
    protected_root = tmp_path / "inputs"
    protected_root.mkdir()
    real_resolve = Path.resolve

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "resolve",
            lambda self, *args, **kwargs: (
                (_ for _ in ()).throw(OSError("target resolution failed"))
                if self == destination
                else real_resolve(self, *args, **kwargs)
            ),
        )
        with pytest.raises(ValueError, match="cannot resolve atomic output"):
            validate_atomic_output_boundary(destination)

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "resolve",
            lambda self, *args, **kwargs: (
                (_ for _ in ()).throw(RuntimeError("symlink loop"))
                if self == destination
                else real_resolve(self, *args, **kwargs)
            ),
        )
        with pytest.raises(ValueError, match="cannot resolve atomic output"):
            validate_atomic_output_boundary(destination)

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "resolve",
            lambda self, *args, **kwargs: (
                (_ for _ in ()).throw(OSError("root resolution failed"))
                if self == protected_root
                else real_resolve(self, *args, **kwargs)
            ),
        )
        with pytest.raises(ValueError, match="cannot resolve protected input tree"):
            validate_atomic_output_boundary(
                destination,
                protected_roots=(protected_root,),
            )

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "rglob",
            lambda self, pattern: (_ for _ in ()).throw(OSError("inventory failed")),
        )
        with pytest.raises(ValueError, match="cannot inventory protected input tree"):
            validate_atomic_output_boundary(
                destination,
                protected_roots=(protected_root,),
            )

    with monkeypatch.context() as patch:
        patch.setattr(atomic_io_module.os, "name", "nt")
        assert atomic_io_module._existing_destination_mode(destination) is None

    destination.write_text("prior", encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "stat",
            lambda self: (_ for _ in ()).throw(OSError("mode inspection failed")),
        )
        with pytest.raises(ValueError, match="cannot inspect atomic output mode"):
            atomic_io_module._existing_destination_mode(destination)

    real_open = atomic_io_module.os.open
    collisions = 0

    def collide_once(path: Path, flags: int, mode: int) -> int:
        nonlocal collisions
        collisions += 1
        if collisions == 1:
            raise FileExistsError("collision")
        return real_open(path, flags, mode)

    with monkeypatch.context() as patch:
        patch.setattr(atomic_io_module.os, "open", collide_once)
        descriptor, temporary = atomic_io_module._open_temporary(
            destination,
            existing_mode=None,
        )
        os.close(descriptor)
        temporary.unlink()
    assert collisions == 2

    with monkeypatch.context() as patch:
        patch.setattr(
            atomic_io_module.os,
            "fchmod",
            lambda *_: (_ for _ in ()).throw(OSError("chmod failed")),
        )
        with pytest.raises(OSError, match="chmod failed"):
            atomic_io_module._open_temporary(destination, existing_mode=0o600)
    assert not tuple(tmp_path.glob(".output.json.*"))

    with monkeypatch.context() as patch:
        patch.setattr(
            atomic_io_module.os,
            "open",
            lambda *_: (_ for _ in ()).throw(FileExistsError("exhausted")),
        )
        with pytest.raises(FileExistsError, match="cannot allocate atomic temporary"):
            atomic_io_module._open_temporary(destination, existing_mode=None)


def test_holdout_git_strategy_inventory_filters_operational_and_generated_paths(
    tmp_path: Path,
) -> None:
    for relative in (
        "uquant/engine.py",
        "uquant/cli.py",
        "uquant/__pycache__/engine.py",
        "uquant/compiled.pyc",
        "benchmarks/reference_registry.json",
        "benchmarks/config_parameter_governance.json",
        "README.md",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=UQuant Tests",
            "-c",
            "user.email=tests@uquant.invalid",
            "commit",
            "--quiet",
            "-m",
            "Create strategy inventory fixture",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert holdout_module._git_strategy_relatives(tmp_path, commit=head) == (
        "benchmarks/config_parameter_governance.json",
        "benchmarks/reference_registry.json",
        "uquant/engine.py",
    )


def test_frozen_manifest_rejects_each_inventory_and_checksum_boundary(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    csv_path, digest = _write_snapshot(valid)
    assert verify_data_manifest(valid)["files_verified"] == 1

    with pytest.raises(DataContractError, match="cannot read frozen checksum"):
        _checksum_entries(tmp_path / "absent")
    for name, content, message in (
        ("bad-line", "not-a-checksum\n", "invalid SHA256SUMS"),
        ("unsafe", f"{digest}  ../sh000300.csv\n", "unsafe frozen-data filename"),
        (
            "duplicate",
            f"{digest}  sh000300.csv\n{digest}  sh000300.csv\n",
            "duplicate frozen-data checksum",
        ),
        ("empty", "", "checksum file is empty"),
    ):
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        with pytest.raises(DataContractError, match=message):
            _checksum_entries(path)

    with pytest.raises(DataContractError, match="data directory does not exist"):
        verify_data_manifest(tmp_path / "missing")

    corrupt = tmp_path / "corrupt"
    _write_snapshot(corrupt)
    (corrupt / "DATA_MANIFEST.json").write_text("{", encoding="utf-8")
    with pytest.raises(DataContractError, match="missing or corrupt"):
        verify_data_manifest(corrupt)

    invalid_inventory = tmp_path / "invalid-inventory"
    _write_snapshot(invalid_inventory)
    (invalid_inventory / "DATA_MANIFEST.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DataContractError, match="invalid results inventory"):
        verify_data_manifest(invalid_inventory)

    non_object = tmp_path / "non-object"
    _write_snapshot(non_object)
    (non_object / "DATA_MANIFEST.json").write_text(
        json.dumps({"results": ["bad"]}), encoding="utf-8"
    )
    with pytest.raises(DataContractError, match="result must be an object"):
        verify_data_manifest(non_object)

    invalid_result = tmp_path / "invalid-result"
    _write_snapshot(invalid_result)
    (invalid_result / "DATA_MANIFEST.json").write_text(
        json.dumps({"results": [{"symbol": "bad", "sha256": digest}]}),
        encoding="utf-8",
    )
    with pytest.raises(DataContractError, match="invalid result"):
        verify_data_manifest(invalid_result)

    repeated = tmp_path / "repeated"
    _write_snapshot(repeated)
    item = {"symbol": "sh000300", "sha256": digest}
    (repeated / "DATA_MANIFEST.json").write_text(
        json.dumps({"results": [item, item]}), encoding="utf-8"
    )
    with pytest.raises(DataContractError, match="repeats"):
        verify_data_manifest(repeated)

    mismatched_inventory = tmp_path / "mismatched-inventory"
    _write_snapshot(mismatched_inventory)
    (mismatched_inventory / "extra.csv").write_bytes(csv_path.read_bytes())
    with pytest.raises(DataContractError, match="inventories differ"):
        verify_data_manifest(mismatched_inventory)

    manifest_mismatch = tmp_path / "manifest-mismatch"
    _write_snapshot(manifest_mismatch)
    (manifest_mismatch / "DATA_MANIFEST.json").write_text(
        json.dumps({"results": [{"symbol": "sh000300", "sha256": "0" * 64}]}),
        encoding="utf-8",
    )
    with pytest.raises(DataContractError, match="manifest checksum differs"):
        verify_data_manifest(manifest_mismatch)

    byte_mismatch = tmp_path / "byte-mismatch"
    changed, _ = _write_snapshot(byte_mismatch)
    changed.write_bytes(changed.read_bytes() + b"changed")
    with pytest.raises(DataContractError, match="checksum mismatch"):
        verify_data_manifest(byte_mismatch)


def test_verified_market_data_rejects_noncausal_and_invalid_lookups() -> None:
    provenance = verify_data_manifest(ROOT / "data/frozen")
    market = VerifiedMarketData(ROOT / "data/frozen", expected_manifest=provenance)
    sessions = market.sessions("2026-08-03", "2026-08-05")
    assert sessions[-1] == "2026-08-05"
    assert market.close("sh000300", sessions[-1]) > 0.0

    with pytest.raises(DataContractError, match="artifact provenance"):
        VerifiedMarketData(ROOT / "data/frozen", expected_manifest={})
    with pytest.raises(DataContractError, match="interval is invalid"):
        market.sessions("bad", "2026-08-05")
    with pytest.raises(DataContractError, match="interval is inverted"):
        market.sessions("2026-08-05", "2026-08-04")
    with pytest.raises(DataContractError, match="no common sessions"):
        market.sessions("2099-01-01", "2099-01-02")
    with pytest.raises(DataContractError, match="is missing"):
        market.close("sz000001", sessions[-1])
    with pytest.raises(DataContractError, match="session is invalid"):
        market.close("sh000300", "bad")
    with pytest.raises(DataContractError, match="no causal close"):
        market.close("sh000300", "1900-01-01")
    panel = market._panels["sh000300"]
    last = panel.index[-1]
    original = panel.loc[last, "close"]
    panel.loc[last, "close"] = pd.NA
    try:
        with pytest.raises(DataContractError, match="close is invalid"):
            market.close("sh000300", str(last.date()))
    finally:
        panel.loc[last, "close"] = original


def test_current_holdout_binding_matches_exact_head_and_reviewed_strategy_anchors() -> None:
    binding = current_holdout_binding(ROOT)
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert binding.production_commit == head
    assert binding.strategy_source_sha256 == holdout_module.STRATEGY_SOURCE_SHA256


def test_holdout_binding_and_defensive_value_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _holdout_binding()

    values = binding.__dict__ if hasattr(binding, "__dict__") else {
        field: getattr(binding, field) for field in binding.__dataclass_fields__
    }
    for field in (
        "production_source_sha256",
        "strategy_source_sha256",
        "strategy_cli_sha256",
        "effective_config_sha256",
        "universe_sha256",
        "industry_sha256",
        "uv_lock_sha256",
    ):
        with pytest.raises(ValueError, match="must be SHA-256"):
            HoldoutBinding(**{**values, field: "bad"})
    for field in ("python_full_version", "numpy_version", "pandas_version", "uv_version"):
        with pytest.raises(ValueError, match="must be non-empty"):
            HoldoutBinding(**{**values, field: ""})
    with pytest.raises(ValueError, match="full Git SHA"):
        HoldoutBinding(**{**values, "production_commit": "short"})

    with pytest.raises(ValueError, match="duplicate key"):
        holdout_module._reject_duplicate_keys([("a", 1), ("a", 2)])
    with pytest.raises(ValueError, match="non-standard number"):
        holdout_module._reject_nonstandard_constant("NaN")
    with pytest.raises(TypeError, match="must be a mapping"):
        holdout_module._canonical_bytes([], omit_seal=True)
    with pytest.raises(ValueError, match="missing or not a regular file"):
        holdout_module._read_json_snapshot(tmp_path / "missing.json", label="fixture")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="is corrupt"):
        holdout_module._read_json_snapshot(corrupt, label="fixture")
    non_object = tmp_path / "array.json"
    non_object.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        holdout_module._read_json_snapshot(non_object, label="fixture")

    first_member = SimpleNamespace(
        symbol="sh000300",
        industry="broad-index",
        effective_from=pd.Timestamp("2020-01-01").date(),
        effective_to=None,
    )
    second_member = SimpleNamespace(
        symbol="sz300308",
        industry="semiconductors",
        effective_from=pd.Timestamp("2021-01-01").date(),
        effective_to=pd.Timestamp("2026-01-01").date(),
    )
    expected_industry_payload = [
        {
            "symbol": "sh000300",
            "industry": "broad-index",
            "effective_from": "2020-01-01",
            "effective_to": None,
        },
        {
            "symbol": "sz300308",
            "industry": "semiconductors",
            "effective_from": "2021-01-01",
            "effective_to": "2026-01-01",
        },
    ]
    assert holdout_module._industry_sha256(
        SimpleNamespace(members=(first_member, second_member))
    ) == holdout_module._canonical_sha256(expected_industry_payload)

    monkeypatch.setattr(holdout_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="cannot resolve git"):
        holdout_module._git_executable()


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "identity",
        "dates",
        "windows",
        "policy",
        "strategy",
        "boundary",
    ),
)
def test_signed_holdout_contract_rejects_semantic_boundary_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(
        json.loads((ROOT / "benchmarks/future_holdout_contract.json").read_text(encoding="utf-8"))
    )
    if mutation == "schema":
        payload.pop("dates")
    elif mutation == "identity":
        payload["schema_version"] = 1
    elif mutation == "dates":
        payload["dates"] = {}
    elif mutation == "windows":
        payload["phase1_windows"] = {}
    elif mutation == "policy":
        payload["observation_policy"] = {}
    elif mutation == "strategy":
        payload["strategy_anchor"] = {}
    else:
        payload["data_directory"] = "data/holdout/weakened"
    payload["canonical_sha256"] = holdout_module._canonical_sha256(
        payload, omit_seal=True
    )
    monkeypatch.setattr(
        holdout_module,
        "REQUIRED_FUTURE_HOLDOUT_SHA256",
        payload["canonical_sha256"],
    )
    path = tmp_path / f"{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        holdout_module.load_future_holdout_contract(path)


def test_holdout_sessions_scores_and_manifest_validation_fail_closed(tmp_path: Path) -> None:
    contract = holdout_module.load_future_holdout_contract()
    binding = _holdout_binding()
    account = {
        "last_successful_run": contract.last_in_sample_date,
        "data_hash_as_of": contract.last_in_sample_date,
        "positions": {},
        "pending_orders": [],
    }
    with pytest.raises(ValueError, match="unique and increasing"):
        holdout_module._session_dates(
            (contract.first_holdout_date, contract.first_holdout_date), contract=contract
        )
    with pytest.raises(ValueError, match="ISO date"):
        holdout_module._session_dates(("bad",), contract=contract)
    with pytest.raises(ValueError, match="predates"):
        holdout_module._session_dates((contract.last_in_sample_date,), contract=contract)
    with pytest.raises(ValueError, match="contracted exchange session prefix"):
        holdout_module._session_dates(
            (contract.review_sessions[1],), contract=contract
        )
    assert holdout_module._session_dates(
        (contract.first_holdout_date,), contract=contract
    ) == (contract.first_holdout_date,)
    with pytest.raises(ValueError, match="unknown holdout scores"):
        holdout_module._normalized_scores(
            {"unknown": 1.0}, sessions=(), contract=contract
        )
    with pytest.raises(ValueError, match="require every score"):
        holdout_module._normalized_scores(
            {}, sessions=(contract.first_holdout_date,), contract=contract
        )
    with pytest.raises(ValueError, match="must be null"):
        holdout_module._normalized_scores(
            {"final_wealth": 1.0}, sessions=(), contract=contract
        )
    invalid_scores: dict[str, float | int | None] = {
        "final_wealth": 1.0,
        "max_drawdown": 0.0,
        "account_orders": 0,
        "gross_turnover": 0.0,
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    with pytest.raises(ValueError, match="must be finite"):
        holdout_module._normalized_scores(
            {**invalid_scores, "final_wealth": True},
            sessions=(contract.first_holdout_date,),
            contract=contract,
        )
    with pytest.raises(ValueError, match="account_orders must be an integer"):
        holdout_module._normalized_scores(
            {**invalid_scores, "account_orders": 1.0},
            sessions=(contract.first_holdout_date,),
            contract=contract,
        )
    for mutation, message in (
        ({"final_wealth": 0.0}, "must be positive"),
        ({"account_orders": -1}, "must be nonnegative"),
        ({"gross_turnover": -1.0}, "must be nonnegative"),
        ({"max_drawdown": 2.0}, "must be between zero and one"),
        (
            {"top1_concentration": 0.2, "top3_concentration": 0.1},
            "must not be below",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            holdout_module._normalized_scores(
                {**invalid_scores, **mutation},
                sessions=(contract.first_holdout_date,),
                contract=contract,
            )
    assert holdout_module._normalized_scores(
        invalid_scores,
        sessions=(contract.first_holdout_date,),
        contract=contract,
    ) == invalid_scores
    for field, value, message in (
        ("holdout_data_sha256", "bad", "data identity"),
        ("metrics_sha256", "bad", "metrics identity"),
        ("metrics_sha256", None, "must agree"),
    ):
        arguments = {
            "contract": contract,
            "binding": binding,
            "account_payload": account,
            "holdout_sessions": (contract.first_holdout_date,),
            "scores": invalid_scores,
            "holdout_data_sha256": "a" * 64,
            "metrics_sha256": "b" * 64,
            field: value,
        }
        with pytest.raises(ValueError, match=message):
            holdout_module._assemble_future_holdout_manifest(**arguments)  # type: ignore[arg-type]

    valid = holdout_module._assemble_future_holdout_manifest(
        contract=contract,
        binding=binding,
        account_payload=account,
        holdout_sessions=(),
        holdout_data_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="schema"):
        holdout_module._validate_future_holdout_manifest_payload(
            {key: value for key, value in valid.items() if key != "scores"}, expected=valid
        )
    changed = copy.deepcopy(valid)
    changed["canonical_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash is invalid"):
        holdout_module._validate_future_holdout_manifest_payload(changed, expected=valid)
    changed = copy.deepcopy(valid)
    changed["observation"]["parameter_changes_from_observation"] = True
    changed["canonical_sha256"] = holdout_module._canonical_sha256(
        changed,
        omit_seal=True,
    )
    with pytest.raises(ValueError, match="parameter changes"):
        holdout_module._validate_future_holdout_manifest_payload(changed, expected=valid)
    with pytest.raises(ValueError, match="is stale"):
        holdout_module._validate_future_holdout_manifest_payload(
            valid,
            expected={**valid, "manifest_id": "different"},
        )

    assert holdout_module.holdout_data_identity(tmp_path) == (
        (),
        hashlib.sha256(b"uquant.empty-future-holdout.v1").hexdigest(),
    )
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="cannot inspect market data"):
        holdout_module.holdout_data_identity(tmp_path)


def test_holdout_ast_and_git_anchor_helpers_reject_unsafe_or_drifted_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert holdout_module._safe_parser_value(ast.Constant(value=1)) is True
    assert holdout_module._safe_parser_value(
        ast.List(elts=[ast.Constant(value="x")], ctx=ast.Load())
    ) is True
    assert holdout_module._safe_parser_value(ast.Name(id="float")) is True
    assert holdout_module._safe_parser_value(ast.Name(id="forbidden")) is False
    safe_statement = ast.parse(
        "sub.add_parser('holdout-manifest', help='reviewed')"
    ).body[0]
    assert holdout_module._safe_operational_parser_statement(
        safe_statement,
        operational_names=set(),
    ) is True

    assignment = ast.parse("a = b = 1").body[0]
    expression = ast.parse("unsafe()") .body[0]
    nested_call = ast.parse("sub.add_parser(build_name())").body[0]
    wrong_receiver = ast.parse("other.add_parser('holdout-manifest')").body[0]
    unsafe_keyword = ast.parse("sub.add_parser('holdout-manifest', help=unsafe())").body[0]
    for statement in (assignment, expression, nested_call, wrong_receiver, unsafe_keyword):
        assert (
            holdout_module._safe_operational_parser_statement(
                statement, operational_names=set()
            )
            is False
        )
    assert holdout_module._command_guard(ast.parse("if other: pass").body[0]) is None
    assert (
        holdout_module._command_guard(
            ast.parse("if args.other == 'holdout-manifest': pass").body[0]
        )
        is None
    )
    with pytest.raises(RuntimeError, match="cannot compile"):
        holdout_module._cli_strategy_ast(b"def invalid(")
    with pytest.raises(RuntimeError, match="exact holdout production source"):
        holdout_module._source_paths(tmp_path)
    with pytest.raises(RuntimeError, match="complete anchored strategy source"):
        holdout_module._strategy_source_paths(tmp_path)

    source = ROOT / "uquant/config.py"
    monkeypatch.setattr(holdout_module, "_strategy_source_paths", lambda _: (source,))
    monkeypatch.setattr(holdout_module, "_git_strategy_relatives", lambda *_args, **_kwargs: ())
    with pytest.raises(RuntimeError, match="inventory drifted"):
        holdout_module._validated_strategy_source_sha256(ROOT)

    monkeypatch.setattr(
        holdout_module,
        "_git_strategy_relatives",
        lambda *_args, **_kwargs: ("uquant/config.py",),
    )
    monkeypatch.setattr(holdout_module, "_source_sha256", lambda *_args, **_kwargs: "0" * 64)
    with pytest.raises(RuntimeError, match="bytes drifted"):
        holdout_module._validated_strategy_source_sha256(ROOT)

    monkeypatch.setattr(holdout_module, "_strategy_cli_sha256", lambda *_args, **_kwargs: "0" * 64)
    with pytest.raises(RuntimeError, match="CLI decision path drifted"):
        holdout_module._validated_strategy_cli_sha256(ROOT)

    with monkeypatch.context() as patch:
        patch.setattr(holdout_module, "STRATEGY_ACCOUNT_CODE_SHA256", "0" * 64)
        with pytest.raises(RuntimeError, match="account code anchor differs"):
            holdout_module._strategy_account_code_sha256(ROOT)

    monkeypatch.setattr(
        holdout_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=""),
    )
    with pytest.raises(RuntimeError, match="account code inventory"):
        holdout_module._strategy_account_code_sha256(ROOT)


def test_reviewed_holdout_strategy_anchor_rejects_one_byte_mutation(tmp_path: Path) -> None:
    """Catches accepting later strategy drift merely because its path was reviewed."""

    checkout = tmp_path / "reviewed-strategy"
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(checkout)],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "checkout",
            "--quiet",
            "--detach",
            holdout_module.STRATEGY_ANCHOR_COMMIT,
        ],
        check=True,
    )
    assert (
        holdout_module._validated_strategy_source_sha256(checkout)
        == holdout_module.STRATEGY_SOURCE_SHA256
    )
    strategy = checkout / "uquant" / "portfolio_strategic.py"
    strategy.write_text(strategy.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="bytes drifted"):
        holdout_module._validated_strategy_source_sha256(checkout)


def test_holdout_observation_metrics_reject_every_detached_input(tmp_path: Path) -> None:
    contract = holdout_module.load_future_holdout_contract()
    data_sha256 = "a" * 64
    with pytest.raises(ValueError, match="must be omitted"):
        holdout_module._observation_metrics(
            tmp_path / "metrics.json",
            sessions=(),
            holdout_data_sha256=data_sha256,
            contract=contract,
        )
    with pytest.raises(RuntimeError, match="deterministic holdout replay"):
        holdout_module._observation_metrics(
            None,
            sessions=(contract.first_holdout_date,),
            holdout_data_sha256=data_sha256,
            contract=contract,
        )
    base: dict[str, object] = {
        "schema_version": 1,
        "holdout_data_sha256": data_sha256,
        "sessions": [contract.first_holdout_date],
        "scores": {
            "final_wealth": 1.0,
            "max_drawdown": 0.0,
            "account_orders": 0,
            "gross_turnover": 0.0,
            "top1_concentration": 0.0,
            "top3_concentration": 0.0,
            "pnl_hhi": 0.0,
        },
    }
    for name, mutation in (
        ("schema", {"schema_version": 2}),
        ("data", {"holdout_data_sha256": "b" * 64}),
        ("sessions-type", {"sessions": "bad"}),
        ("sessions-value", {"sessions": ["2026-08-07"]}),
        ("scores", {"scores": []}),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**base, **mutation}), encoding="utf-8")
        with pytest.raises(RuntimeError, match="deterministic holdout replay"):
            holdout_module._observation_metrics(
                path,
                sessions=(contract.first_holdout_date,),
                holdout_data_sha256=data_sha256,
                contract=contract,
            )


def test_holdout_file_layout_rejects_missing_dates_links_and_stale_state(
    tmp_path: Path,
) -> None:
    contract = holdout_module.load_future_holdout_contract()
    with pytest.raises(RuntimeError, match="lacks date column"):
        holdout_module._csv_dates_from_text("close\n1\n", path=tmp_path / "bad.csv")
    with pytest.raises(RuntimeError, match="invalid date"):
        holdout_module._csv_dates_from_text("date\nbad\n", path=tmp_path / "bad.csv")
    with pytest.raises(RuntimeError, match="no observed market sessions"):
        holdout_module.maximum_observed_market_date(tmp_path)
    assert holdout_module._closed_csv_files(
        tmp_path / "absent", label="fixture", missing_ok=True
    ) == ()
    with pytest.raises(RuntimeError, match="is missing"):
        holdout_module._closed_csv_files(
            tmp_path / "absent", label="fixture", missing_ok=False
        )
    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must be a directory"):
        holdout_module._closed_csv_files(regular, label="fixture", missing_ok=False)
    unsupported = tmp_path / "unsupported"
    unsupported.mkdir()
    (unsupported / "note.txt").write_text("bad", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported file"):
        holdout_module._closed_csv_files(unsupported, label="fixture", missing_ok=False)

    with pytest.raises(ValueError, match="exact prior-close state"):
        holdout_module._state_hashes({}, as_of=contract.last_in_sample_date)
    with pytest.raises(ValueError, match="positions or pending orders"):
        holdout_module._state_hashes(
            {
                "last_successful_run": contract.last_in_sample_date,
                "data_hash_as_of": contract.last_in_sample_date,
                "positions": [],
                "pending_orders": {},
            },
            as_of=contract.last_in_sample_date,
        )
    with pytest.raises(ValueError, match="owning repository root"):
        holdout_module._manifest_repository_root(tmp_path)

    repository_link = tmp_path / "repository-link"
    repository_link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="repository root must not be a symlink"):
        holdout_module.validate_holdout_layout(repository_link)

    root = tmp_path / "isolated"
    frozen = root / "data/frozen"
    frozen.mkdir(parents=True)
    (frozen / "a.csv").write_text(
        "date,open,high,low,close,volume\n2026-08-05,1,1,1,1,1\n",
        encoding="utf-8",
    )
    linked = frozen / "linked.csv"
    linked.symlink_to(frozen / "a.csv")
    with pytest.raises(RuntimeError, match="data/frozen contains a symlink"):
        holdout_module.validate_holdout_layout(root)
    linked.unlink()

    holdout_root = root / "data/holdout"
    holdout_root.symlink_to(frozen, target_is_directory=True)
    with pytest.raises(RuntimeError, match="data/holdout must be a physical directory"):
        holdout_module.validate_holdout_layout(root)
    holdout_root.unlink()
    future = root / contract.data_directory
    future.mkdir(parents=True)
    (future / "a.csv").write_text(
        "date,open,high,low,close,volume\n2026-08-05,1,1,1,1,1\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="in-sample market row"):
        holdout_module.validate_holdout_layout(root)


def test_phase1_equivalence_rejects_incomplete_matrix_and_trace_contracts(
    tmp_path: Path,
) -> None:
    baseline = json.loads((ROOT / "benchmarks/promotion_baseline.json").read_text(encoding="utf-8"))
    for name, mutation, message in (
        ("root", {"pools": []}, "baseline is malformed"),
        ("pools", {"pools": {}}, "complete replay matrix"),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({**baseline, **mutation}), encoding="utf-8")
        with pytest.raises(RuntimeError, match=message):
            equivalence_module.phase1_cases(path)
    malformed_pool = copy.deepcopy(baseline)
    malformed_pool["pools"]["a"] = [1]
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps(malformed_pool), encoding="utf-8")
    with pytest.raises(RuntimeError, match="pool is malformed"):
        equivalence_module.phase1_cases(pool_path)
    malformed_interval = copy.deepcopy(baseline)
    malformed_interval["contract"]["windows"]["h1_2023"] = []
    interval_path = tmp_path / "interval.json"
    interval_path.write_text(json.dumps(malformed_interval), encoding="utf-8")
    with pytest.raises(RuntimeError, match="interval is malformed"):
        equivalence_module.phase1_cases(interval_path)

    required = {
        "decision_payload_sha256": "a" * 64,
        "economic_account_sha256": "b" * 64,
    }
    candidate = Phase1DecisionTrace(production_commit="f" * 40, cases={"case": required})
    with pytest.raises(RuntimeError, match="not bound"):
        equivalence_module.assert_equivalent_phase1_traces(
            Phase1DecisionTrace(production_commit="0" * 40, cases={"case": required}),
            candidate,
        )
    frozen = Phase1DecisionTrace(
        production_commit=equivalence_module.FROZEN_CHAMPION_COMMIT,
        cases={"case": required},
    )
    with pytest.raises(RuntimeError, match="cases differ"):
        equivalence_module.assert_equivalent_phase1_traces(
            frozen, Phase1DecisionTrace(production_commit="f" * 40, cases={})
        )
    with pytest.raises(RuntimeError, match="payload is malformed"):
        equivalence_module.assert_equivalent_phase1_traces(
            frozen,
            Phase1DecisionTrace(
                production_commit="f" * 40,
                cases={"case": {"decision_payload_sha256": "a" * 64}},
            ),
        )


def test_phase1_equivalence_subprocess_boundaries_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(equivalence_module.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="cannot resolve git"):
        equivalence_module._git_executable()
    monkeypatch.setattr(equivalence_module.shutil, "which", lambda _: "git")
    monkeypatch.setattr(
        equivalence_module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["git"])
        ),
    )
    with pytest.raises(RuntimeError, match="cannot resolve commit"):
        equivalence_module._git_commit(tmp_path)

    case = Phase1Case("a/h1_2023", ("sh000300",), "2023-01-03", "2023-01-04")
    outputs = (
        ("not-json", "cannot capture"),
        ("[]", "trace is malformed"),
        (json.dumps({"decision_payload_sha256": "a" * 64}), "trace is malformed"),
        (
            json.dumps(
                {
                    "decision_payload_sha256": "short",
                    "economic_account_sha256": "b" * 64,
                }
            ),
            "digest is malformed",
        ),
    )
    for stdout, message in outputs:
        monkeypatch.setattr(
            equivalence_module.subprocess,
            "run",
            lambda *_args, _stdout=stdout, **_kwargs: SimpleNamespace(stdout=_stdout),
        )
        with pytest.raises(RuntimeError, match=message):
            equivalence_module.trace_phase1_case(root=tmp_path, data_dir=tmp_path, case=case)

    monkeypatch.setattr(equivalence_module, "_git_commit", lambda _: "0" * 40)
    with pytest.raises(RuntimeError, match="does not match"):
        equivalence_module.compare_phase1_commits(
            frozen_root=tmp_path,
            candidate_root=tmp_path,
            data_dir=tmp_path,
            cases=(case,),
        )


def test_universe_json_and_scalar_helpers_reject_ambiguous_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        universe_module._reject_duplicate_keys([("a", 1), ("a", 2)])
    with pytest.raises(ValueError, match="non-standard number"):
        universe_module._reject_nonstandard_constant("NaN")
    with pytest.raises(ValueError, match="must be an ISO date"):
        universe_module._parse_date(1, label="date")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="must be an ISO date"):
        universe_module._parse_date("bad", label="date")
    with pytest.raises(ValueError, match="is corrupt"):
        universe_module._read_json_bytes(b"\xff", label="fixture")
    with pytest.raises(ValueError, match="must be a JSON object"):
        universe_module._read_json_bytes(b"[]", label="fixture")
    with pytest.raises(ValueError, match="missing or not a regular file"):
        universe_module._read_json(tmp_path / "missing.json", label="fixture")
    with pytest.raises(ValueError, match="must be SHA-256"):
        universe_module._sha256("bad", label="fixture")


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "production_shape",
        "production_identity",
        "data_shape",
        "environment_shape",
        "snapshot",
        "file_count",
        "environment_value",
        "artifact",
        "source_sha",
    ),
)
def test_signed_frozen_champion_rejects_semantic_provenance_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(json.loads(universe_module.frozen_champion_bytes()))
    if mutation == "schema":
        payload["schema_version"] = 2
    elif mutation == "production_shape":
        payload["production"] = {}
    elif mutation == "production_identity":
        payload["production"]["repository"] = "other/repository"
    elif mutation == "data_shape":
        payload["data"] = {}
    elif mutation == "environment_shape":
        payload["environment"] = {}
    elif mutation == "snapshot":
        payload["data"]["snapshot_id"] = ""
    elif mutation == "file_count":
        payload["data"]["files_verified"] = True
    elif mutation == "environment_value":
        payload["environment"]["python_full_version"] = ""
    elif mutation == "artifact":
        payload["github_phase1_artifact_sha256"] = "0" * 64
    else:
        payload["production"]["source_sha256"] = "bad"
    monkeypatch.setattr(
        universe_module,
        "REQUIRED_FROZEN_CHAMPION_SHA256",
        universe_module.canonical_sha256(payload),
    )
    path = tmp_path / f"champion-{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        universe_module.load_phase1_frozen_champion(path)


@pytest.mark.parametrize(
    "mutation",
    (
        "schema",
        "identity",
        "members",
        "member_shape",
        "symbol",
        "industry",
        "domain",
        "tradable",
        "evidence",
        "interval",
        "count",
        "order",
    ),
)
def test_signed_ai_universe_rejects_semantic_membership_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = copy.deepcopy(json.loads(universe_module.ai_universe_manifest_bytes()))
    if mutation == "schema":
        payload.pop("members")
    elif mutation == "identity":
        payload["manifest_id"] = "changed"
    elif mutation == "members":
        payload["members"] = []
    elif mutation == "member_shape":
        payload["members"][0].pop("evidence")
    elif mutation == "symbol":
        payload["members"][0]["symbol"] = "bad"
    elif mutation == "industry":
        payload["members"][0]["industry"] = "unknown"
    elif mutation == "domain":
        payload["members"][0]["ai_domain"] = ""
    elif mutation == "tradable":
        payload["members"][0]["tradable"] = False
    elif mutation == "evidence":
        payload["members"][0]["evidence"] = ""
    elif mutation == "interval":
        payload["members"][0]["effective_to"] = payload["members"][0]["effective_from"]
    elif mutation == "count":
        payload["members"].pop()
    else:
        payload["members"][0], payload["members"][1] = (
            payload["members"][1],
            payload["members"][0],
        )
    payload["canonical_sha256"] = universe_module.canonical_sha256(payload)
    monkeypatch.setattr(
        universe_module,
        "REQUIRED_AI_UNIVERSE_SHA256",
        payload["canonical_sha256"],
    )
    path = tmp_path / f"universe-{mutation}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        universe_module.load_ai_universe(path)
