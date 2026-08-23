from __future__ import annotations

import copy
import json
from functools import cache
from pathlib import Path
from typing import Any, cast

import pytest

from uquant.contracts.strict_json import canonical_json_sha256

from ._task9_inventory import (
    build_task9_inventory,
    current_reflection_contract,
)

ROOT = Path(__file__).resolve().parents[2]
_TASK9_START = "719288f6067686b3199d305899ddc09adf098a0d"
_TASK9_START_TREE = "459d592cb24c6cfed2082bfd2f7519a9badee67d"
_INVENTORY = ROOT / "artifacts/architecture_refactor/task9_cleanup_inventory.json"
_LEGACY_IMPLEMENTATIONS = (
    "uquant/validation/generalization.py",
    "uquant/validation/generalization_reference.py",
    "uquant/validation/holdout.py",
    "uquant/validation/holdout_runtime.py",
    "uquant/validation/holdout_lanes.py",
    "uquant/risk_sentinel/cli.py",
    "uquant/risk_sentinel/validation.py",
)
_FIXED_REFERENCE_COUNTS = (5, 5, 11, 10, 7, 10, 8)
_IMPORT_CONSUMER_COUNTS = (16, 3, 8, 4, 4, 4, 1)


@cache
def _immutable_inventory() -> dict[str, Any]:
    value = json.loads(_INVENTORY.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


@cache
def _rebuilt_inventory() -> dict[str, Any]:
    return build_task9_inventory(ROOT)


def _assert_inventory_seal(payload: dict[str, Any]) -> None:
    claimed = payload["canonical_sha256"]
    unsigned = dict(payload)
    del unsigned["canonical_sha256"]
    assert claimed == canonical_json_sha256(unsigned)


def _assert_immutable_inventory(payload: dict[str, Any]) -> None:
    _assert_inventory_seal(payload)
    assert payload == _immutable_inventory()


def test_task9_cleanup_inventory_precedes_every_legacy_replacement() -> None:
    payload = _immutable_inventory()
    assert payload["baseline_commit"] == _TASK9_START
    assert payload["baseline_tree"] == _TASK9_START_TREE
    assert payload["captured_while_all_implementations_intact"] == list(
        _LEGACY_IMPLEMENTATIONS
    )
    assert tuple(entry["path"] for entry in payload["entries"]) == (
        _LEGACY_IMPLEMENTATIONS
    )


def test_task9_cleanup_inventory_is_exactly_rebuilt_from_immutable_git() -> None:
    payload = _immutable_inventory()
    _assert_inventory_seal(payload)
    assert _rebuilt_inventory() == payload


def test_task9_cleanup_inventory_has_bidirectional_reference_partitions() -> None:
    payload = _immutable_inventory()
    entries = payload["entries"]
    assert isinstance(entries, list)
    assert tuple(
        len(entry["live_references"]["immutable_fixed_path_consumers"])
        for entry in entries
    ) == _FIXED_REFERENCE_COUNTS
    assert tuple(
        len(entry["live_references"]["ast_import_consumers"])
        for entry in entries
    ) == _IMPORT_CONSUMER_COUNTS
    classifications = (
        "current_executable_consumers",
        "historical_machine_evidence_to_preserve",
        "documentation_references",
        "other_current_or_contract_consumers",
    )
    for entry in entries:
        references = entry["live_references"]
        groups = [set(references[name]) for name in classifications]
        assert set().union(*groups) == set(
            references["immutable_fixed_path_consumers"]
        )
        assert sum(len(group) for group in groups) == len(set().union(*groups))


def test_task9_frozen_public_reflection_survives_all_import_modes() -> None:
    payload = _immutable_inventory()
    current = current_reflection_contract(ROOT)
    assert {
        "modules": current["normal"],
        "import_mode_sha256": current["mode_sha256"],
    } == payload["public_runtime_contract"]


@pytest.mark.parametrize(
    ("entry_index", "field"),
    (
        (0, "ast_import_consumers"),
        (2, "immutable_fixed_path_consumers"),
        (5, "dotted_runtime_identity_consumers"),
    ),
)
def test_task9_resigned_inventory_omission_is_rejected(
    entry_index: int, field: str
) -> None:
    payload = copy.deepcopy(_immutable_inventory())
    entries = payload["entries"]
    assert isinstance(entries, list)
    values = entries[entry_index]["live_references"][field]
    assert values
    values.pop()
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    _assert_inventory_seal(payload)
    with pytest.raises(AssertionError):
        _assert_immutable_inventory(payload)


def test_task9_resigned_immutable_blob_tamper_is_rejected() -> None:
    payload = copy.deepcopy(_immutable_inventory())
    entries = payload["entries"]
    assert isinstance(entries, list)
    entries[0]["content_sha256"] = "0" * 64
    del payload["canonical_sha256"]
    payload["canonical_sha256"] = canonical_json_sha256(payload)
    _assert_inventory_seal(payload)
    with pytest.raises(AssertionError):
        _assert_immutable_inventory(payload)
