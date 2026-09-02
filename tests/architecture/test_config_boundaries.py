from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
import pickle
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_type_hints

import pytest

from ._analysis import (
    _CONFIG_RELOCATED_PRIVATE_IMPORTS,
    ROOT,
    architecture_snapshot,
)
from ._config_transport import config_private_relocation_projection

_CONFIG_REFERENCE_COMMIT = "3af754edf83b5ca67e06b1c3733eb5161dd7fd3c"
_INVENTORY = ROOT / "artifacts" / "architecture_refactor" / "task5_cleanup_inventory.json"
_V1_PLANNED_BYTES = (
    b'{"actual_price":null,"actual_shares":null,"actual_time":null,'
    b'"manual_skip":null,"next_open":null,"plan_id":"frozen-plan-1",'
    b'"planned_price":947.74,"planned_shares":100,'
    b'"previous_sha256":"0000000000000000000000000000000000000000000000000000000000000000",'
    b'"record_sha256":"625f4800c03588a453b1c137a49bf6f8ecc1f9480eb1e094049e1135ae8a5b40",'
    b'"recorded_at":"2026-08-05T15:01:00+08:00","schema_version":1,'
    b'"sequence":1,"side":"BUY","slippage_bps":null,'
    b'"slippage_per_share":null,"slippage_value":null,"status":"PLANNED",'
    b'"symbol":"sz300308"}\n'
)

_SOURCE_SURFACE_REGISTRY = "benchmarks/source_surface_registry.json"


def test_config_cleanup_inventory_is_bound_to_immutable_start_blobs() -> None:
    assert hashlib.sha256(_INVENTORY.read_bytes()).hexdigest() == (
        "98a5b9a3648d5356cc58893a0acf153ac43df228221133fc053e04c53f8d4a47"
    )
    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == _CONFIG_REFERENCE_COMMIT
    assert payload["requirements_txt_disposition"] == "KEEP_AUTHORITATIVE"
    assert {entry["path"] for entry in payload["entries"]} == {
        "uquant/account.py",
        "uquant/attribution.py",
        "uquant/execution_journal.py",
        "uquant/validation/execution_journal.py",
    }
    for entry in payload["entries"]:
        source = subprocess.run(
            ["git", "cat-file", "blob", entry["git_blob_sha1"]],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        assert len(source) == entry["size_bytes"]
        assert hashlib.sha256(source).hexdigest() == entry["content_sha256"]


def test_config_cleanup_inventory_covers_current_authority_references() -> None:
    """Keep the frozen inventory distinct from the current source-surface contract."""

    payload = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert payload["live_reference_derivation"]["immutable_commit"] == _CONFIG_REFERENCE_COMMIT
    entries = {entry["path"]: entry for entry in payload["entries"]}
    registry = json.loads((ROOT / _SOURCE_SURFACE_REGISTRY).read_text(encoding="utf-8"))
    unsigned = {key: value for key, value in registry.items() if key != "canonical_sha256"}
    from uquant.contracts.strict_json import canonical_json_sha256

    assert registry["canonical_sha256"] == canonical_json_sha256(unsigned)
    registry_paths = {
        surface["id"]: set(surface["source_paths"])
        for surface in registry["surfaces"]
    }
    for replaced_path, entry in entries.items():
        recorded = set(entry["live_references"]["immutable_path_references"])
        assert recorded
        assert set(entry["live_references"]["historical_machine_evidence_to_preserve"]) <= recorded
        recorded_surface_ids = set(
            entry["live_references"]["current_source_surface_registry"]
        )
        assert recorded_surface_ids <= set(registry_paths)
        remains_current = any(
            replaced_path in paths for paths in registry_paths.values()
        )
        assert remains_current is (ROOT / replaced_path).is_file()


def test_config_private_edges_are_exactly_bound_to_the_mechanical_split() -> None:
    from uquant.attribution import concentration, replay_evidence

    graph = architecture_snapshot()["import_graph"]
    assert isinstance(graph, dict)
    relocated = graph["task5_relocated_private_imports"]
    ordinary = graph["cross_module_private_imports"]
    private_module_calls = graph["cross_module_private_module_calls"]
    assert isinstance(relocated, list)
    assert isinstance(ordinary, list)
    assert isinstance(private_module_calls, list)
    observed_relocated = {str(row["id"]) for row in relocated}
    assert config_private_relocation_projection(
        root=ROOT,
        expected=_CONFIG_RELOCATED_PRIVATE_IMPORTS,
        observed=observed_relocated,
    ) == _CONFIG_RELOCATED_PRIVATE_IMPORTS
    assert concentration.group_lot_pnl is concentration._group_lot_pnl
    assert concentration.holding_summary is concentration._holding_summary
    assert replay_evidence.LEDGER_FIELDS is replay_evidence._LEDGER_FIELDS
    assert (
        replay_evidence.require_exact_attribution_fields
        is replay_evidence._require_exact_fields
    )
    config_prefixes = (
        "uquant.account",
        "uquant.attribution",
        "uquant.execution_journal",
        "uquant.observation.execution_journal",
        "uquant.validation.execution_journal",
    )
    baseline = json.loads(
        (ROOT / "artifacts/architecture_refactor/baseline_inventory.json").read_text(encoding="utf-8")
    )
    allowed = set(baseline["architecture_debt"]["temporary_allowlist"]["cross_module_private_imports"])
    assert (
        not {
            str(row["id"])
            for row in ordinary
            if str(row["importer"]).startswith(config_prefixes)
            or str(row["imported_from"]).startswith(config_prefixes)
        }
        - allowed
    )
    assert not {
        str(row["id"])
        for row in private_module_calls
        if str(row["importer"]).startswith(config_prefixes)
        or str(row["imported_from"]).startswith(config_prefixes)
    }


def test_config_public_objects_keep_legacy_module_and_pickle_identities() -> None:
    from uquant.account import account_from_dict, save_account
    from uquant.attribution import ExitRecord, build_economic_attribution
    from uquant.execution_journal import (
        JournalCheckpoint as LegacyCheckpoint,
    )
    from uquant.execution_journal import (
        JournalRecord as LegacyRecord,
    )
    from uquant.execution_journal import (
        JournalStatus as LegacyStatus,
    )
    from uquant.validation.execution_journal import (
        JournalCheckpoint,
        JournalRecord,
        JournalStatus,
    )

    assert account_from_dict.__module__ == save_account.__module__ == "uquant.account"
    assert ExitRecord.__module__ == build_economic_attribution.__module__ == "uquant.attribution"
    legacy = LegacyRecord(
        1,
        1,
        LegacyStatus.PLANNED,
        "frozen-plan-1",
        "2026-08-05T15:01:00+08:00",
        "sz300308",
        "BUY",
        947.74,
        100,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "0" * 64,
        "1" * 64,
    )
    current = JournalRecord(
        2,
        1,
        JournalStatus.PLANNED,
        "canonical-plan-1",
        "2026-08-05T15:01:00+08:00",
        "2026-08-05",
        "sz300308",
        "BUY",
        0.08,
        947.74,
        100,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "0" * 64,
        "1" * 64,
    )
    objects = (
        ExitRecord("sz300308", "2026-08-05", 947.74, "leader", "exit"),
        LegacyStatus.PLANNED,
        LegacyCheckpoint(1, 0, "0" * 64),
        legacy,
        JournalStatus.PLANNED,
        JournalCheckpoint(1, 0, "0" * 64),
        current,
    )
    for value in objects:
        restored = pickle.loads(pickle.dumps(value))
        assert restored == value
        assert type(restored).__module__ == type(value).__module__
        assert type(restored).__qualname__ == type(value).__qualname__

    assert LegacyRecord.__annotations__["status"] == "JournalStatus"
    assert get_type_hints(LegacyRecord)["status"] is LegacyStatus
    assert LegacyRecord.__init__.__module__ == "uquant.execution_journal"
    assert LegacyRecord.__init__.__qualname__ == "JournalRecord.__init__"
    assert LegacyCheckpoint.__init__.__module__ == "uquant.execution_journal"
    assert LegacyCheckpoint.__init__.__qualname__ == "JournalCheckpoint.__init__"
    for legacy_type, frozen_name in (
        (LegacyRecord, "JournalRecord"),
        (LegacyCheckpoint, "JournalCheckpoint"),
    ):
        for method_name in ("__repr__", "__eq__", "__hash__"):
            method = getattr(legacy_type, method_name)
            assert method.__module__ == "uquant.execution_journal"
            assert method.__qualname__ == f"{frozen_name}.{method_name}"
    assert LegacyCheckpoint.__post_init__.__module__ == "uquant.execution_journal"
    assert LegacyCheckpoint.__post_init__.__qualname__ == "JournalCheckpoint.__post_init__"


@pytest.mark.parametrize(
    ("module_name", "owned_names"),
    (
        ("uquant.account.validation_common", ("_finite_number", "_required_iso_date")),
        (
            "uquant.account.validation_orders",
            ("_validate_order_state", "validate_pending_order_for_account_write"),
        ),
        ("uquant.account.validation_positions", ("_position", "_validate_position_state")),
        ("uquant.account.validation_strategy", ("_validate_audit_events",)),
        ("uquant.account.codec", ("account_from_dict", "load_account")),
        ("uquant.account.code_identity", ("migrate_code_identity",)),
        ("uquant.account.economic_identity", ("economic_state_sha256",)),
        ("uquant.account.store", ("save_account",)),
        ("uquant.attribution.concentration", ("contribution_concentration",)),
        (
            "uquant.attribution.validation",
            ("validate_economic_attribution", "validate_attribution_against_engine_result"),
        ),
        ("uquant.attribution.ledger", ("build_daily_ledger_row",)),
        ("uquant.attribution.replay_evidence", ("build_daily_replay_evidence_row",)),
        ("uquant.attribution.diagnostics", ("attribution_diagnostics", "post_exit_diagnostics")),
        ("uquant.attribution.builder", ("build_economic_attribution",)),
        ("uquant.observation.execution_journal.codec_v1", ("decode_v1_record",)),
        ("uquant.observation.execution_journal.codec_v2", ("encode_v2_record",)),
        ("uquant.observation.execution_journal.lifecycle", ("validate_lifecycle",)),
        ("uquant.observation.execution_journal.checkpoint", ("execution_journal_checkpoint",)),
        (
            "uquant.observation.execution_journal.store",
            ("append_planned", "read_execution_journal"),
        ),
        ("uquant.observation.execution_journal.rendering", ("render_execution_journal",)),
    ),
)
def test_config_responsibilities_have_real_module_owners(
    module_name: str,
    owned_names: tuple[str, ...],
) -> None:
    module = importlib.import_module(module_name)
    expected_path = Path(*module_name.split(".")).with_suffix(".py")
    for name in owned_names:
        value = getattr(module, name)
        source = inspect.getsourcefile(value)
        assert source is not None
        assert Path(source).resolve() == (ROOT / expected_path).resolve()


@pytest.mark.parametrize(
    ("path", "required_imports", "forbidden_imports"),
    (
        (
            "uquant/account/store.py",
            {"infrastructure.atomic_files"},
            {"tempfile"},
        ),
        (
            "uquant/observation/execution_journal/store.py",
            {
                "infrastructure.atomic_files",
                "infrastructure.file_lock",
            },
            {"fcntl"},
        ),
    ),
)
def test_config_persistence_uses_only_shared_platform_primitives(
    path: str,
    required_imports: set[str],
    forbidden_imports: set[str],
) -> None:
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert all(any(name.endswith(required) for name in imported) for required in required_imports)
    assert not forbidden_imports & imported


def test_historical_v1_golden_bytes_remain_readable(tmp_path: Path) -> None:
    from uquant.execution_journal import read_execution_journal, record_to_dict

    path = tmp_path / "historical-v1.jsonl"
    path.write_bytes(_V1_PLANNED_BYTES)
    records = read_execution_journal(path)
    assert len(records) == 1
    assert record_to_dict(records[0]) == json.loads(_V1_PLANNED_BYTES)
    assert path.read_bytes() == _V1_PLANNED_BYTES


def test_legacy_v1_facade_preserves_frozen_decode_error_priority(tmp_path: Path) -> None:
    from uquant.execution_journal import read_execution_journal as read_legacy
    from uquant.observation.execution_journal import append_planned
    from uquant.validation.execution_journal import read_execution_journal as read_current

    v2_path = tmp_path / "canonical-v2.jsonl"
    append_planned(
        v2_path,
        plan_id="canonical-plan-1",
        recorded_at="2026-08-05T15:01:00+08:00",
        decision_date="2026-08-05",
        symbol="sz300308",
        side="BUY",
        planned_price=947.74,
        planned_shares=100,
    )
    with pytest.raises(ValueError, match=r"^execution journal record schema is malformed$"):
        read_legacy(v2_path)

    malformed = json.loads(_V1_PLANNED_BYTES)
    malformed["plan_id"] = " bad"
    malformed["recorded_at"] = "not-a-time"
    unsigned = {key: value for key, value in malformed.items() if key != "record_sha256"}
    malformed["record_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    malformed_path = tmp_path / "malformed-v1.jsonl"
    malformed_path.write_text(
        json.dumps(malformed, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"^execution journal plan_id is malformed$"):
        read_legacy(malformed_path)
    with pytest.raises(ValueError, match=r"^journal recorded_at must be an ISO timestamp$"):
        read_current(malformed_path)

    version_mismatch = json.loads(_V1_PLANNED_BYTES)
    version_mismatch["schema_version"] = 2
    unsigned = {
        key: value for key, value in version_mismatch.items() if key != "record_sha256"
    }
    version_mismatch["record_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    mismatch_path = tmp_path / "v1-fields-v2-version.jsonl"
    mismatch_path.write_text(
        json.dumps(
            version_mismatch,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"^execution journal sequence is malformed$"):
        read_legacy(mismatch_path)
    with pytest.raises(ValueError, match=r"^execution journal record schema is malformed$"):
        read_current(mismatch_path)


def test_legacy_v1_records_keep_frozen_compact_report_columns(tmp_path: Path) -> None:
    from uquant.execution_journal import read_execution_journal
    from uquant.report import render_execution_journal

    path = tmp_path / "historical-v1.jsonl"
    path.write_bytes(_V1_PLANNED_BYTES)
    assert render_execution_journal(read_execution_journal(path)) == (
        "# Manual Execution Journal\n\n"
        "| Seq | Plan | Status | Symbol | Side | Planned | Next open | Actual | Shares | Slippage | Note |\n"
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---|\n"
        "| 1 | frozen-plan-1 | PLANNED | sz300308 | BUY | 947.7400 |  |  |  |  |  |\n"
    )


def test_historical_v1_migration_is_explicit_and_writes_only_v2(tmp_path: Path) -> None:
    from uquant.observation.execution_journal import (
        migrate_v1_journal,
        read_execution_journal,
    )

    source = tmp_path / "historical-v1.jsonl"
    destination = tmp_path / "migrated-v2.jsonl"
    source.write_bytes(_V1_PLANNED_BYTES)
    migrated = migrate_v1_journal(source, destination)
    encoded = json.loads(destination.read_bytes())
    assert source.read_bytes() == _V1_PLANNED_BYTES
    assert encoded["schema_version"] == 2
    assert "record_hash" in encoded and "record_sha256" not in encoded
    assert migrated == read_execution_journal(destination)
    assert migrated[0].plan_id == "frozen-plan-1"


def test_legacy_v1_facade_append_entrypoints_fail_closed(tmp_path: Path) -> None:
    from uquant import execution_journal as legacy

    path = tmp_path / "legacy.jsonl"
    calls: tuple[Callable[[], Any], ...] = (
        lambda: legacy.append_planned(
            path,
            plan_id="frozen-plan-1",
            recorded_at="2026-08-05T15:01:00+08:00",
            symbol="sz300308",
            side="BUY",
            planned_price=947.74,
            planned_shares=100,
        ),
        lambda: legacy.append_filled(
            path,
            plan_id="frozen-plan-1",
            recorded_at="2026-08-06T09:32:00+08:00",
            next_open=950.0,
            actual_time="2026-08-06T09:31:05+08:00",
            actual_price=951.0,
            actual_shares=100,
        ),
        lambda: legacy.append_skipped(
            path,
            plan_id="frozen-plan-1",
            recorded_at="2026-08-06T09:32:00+08:00",
            next_open=950.0,
            manual_skip="operator declined",
        ),
    )
    for call in calls:
        with pytest.raises(RuntimeError, match="v1 execution journal is read-only"):
            call()
        assert not path.exists()


def test_canonical_writer_emits_v2_and_never_v1(tmp_path: Path) -> None:
    from uquant.observation.execution_journal import append_planned

    path = tmp_path / "canonical.jsonl"
    record = append_planned(
        path,
        plan_id="canonical-plan-1",
        decision_date="2026-08-05",
        recorded_at="2026-08-05T15:01:00+08:00",
        symbol="sz300308",
        side="BUY",
        planned_weight=0.08,
        planned_price=947.74,
        planned_shares=100,
    )
    encoded = json.loads(path.read_bytes())
    assert record.schema_version == encoded["schema_version"] == 2
    assert "record_hash" in encoded
    assert "record_sha256" not in encoded


@pytest.mark.skipif(os.name == "nt", reason="POSIX account file mode contract")
def test_account_store_keeps_private_new_file_permissions(tmp_path: Path) -> None:
    from uquant.account import save_account
    from uquant.types import AccountState

    path = tmp_path / "account.json"
    save_account(AccountState.empty(2_000_000.0), path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
