"""Bounded native Absolute evidence integration for the tradeoff gate."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import generalization_tradeoff_native as native
from uquant.validation.absolute_generalization import load_absolute_generalization_contract


@dataclass(frozen=True)
class _Metrics:
    final_wealth: float
    max_drawdown: float
    distinct_owner_count: int = 0
    cash_drag: float = 0.0


@dataclass(frozen=True)
class _Cell:
    removed_symbol: str
    status: str
    metrics: _Metrics | None


def _policy() -> dict[str, object]:
    return {
        "minimum_positive_fraction": 0.9,
        "minimum_p10_wealth": 1.0,
        "maximum_p90_drawdown": 0.31,
        "maximum_worst_drawdown": 0.35,
    }


def test_native_thresholds_use_linear_percentiles_and_disclose_diagnostics() -> None:
    cells = [_Cell(str(index), "COMPLETE", _Metrics(1.01 + index / 100, index / 200)) for index in range(34)]
    result = native._evaluate_cells(cells, tuple(str(index) for index in range(34)), _policy())
    assert result["status"] == "PASS"
    assert result["metrics"]["positive_fraction"] == 1.0
    assert result["metrics"]["p10_wealth"] == pytest.approx(1.043)
    assert result["diagnostics"]["epoch_owner_counts"] == [0] * 34


def test_native_thresholds_fail_without_relaxing_to_old_policy() -> None:
    cells = [
        _Cell(str(index), "COMPLETE", _Metrics(0.5 if index == 0 else 2.0, 0.36 if index == 33 else 0.1))
        for index in range(34)
    ]
    result = native._evaluate_cells(cells, tuple(str(index) for index in range(34)), _policy())
    assert result["status"] == "FAIL"
    assert {failure["metric"] for failure in result["failures"]} == {"worst_drawdown"}


def test_native_cells_reject_missing_duplicate_and_replay_error() -> None:
    universe = tuple(str(index) for index in range(34))
    complete = [_Cell(symbol, "COMPLETE", _Metrics(2.0, 0.1)) for symbol in universe]
    for cells in (
        complete[:-1],
        [*complete[:-1], complete[0]],
        [*complete[:-1], _Cell(universe[-1], "REPLAY_ERROR", None)],
    ):
        result = native._evaluate_cells(cells, universe, _policy())
        assert result["status"] == "INCOMPLETE"
        assert result["passed"] is False


def test_public_evaluator_fails_closed_on_missing_evidence(tmp_path: Path) -> None:
    missing = native.evaluate_native(
        tmp_path, tmp_path, "d" * 40, tmp_path / "none", {"native_absolute": _policy()}
    )
    assert missing["status"] == "INCOMPLETE"


def test_producer_source_is_recomputed_from_original_head(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).parents[1]
    absolute = load_absolute_generalization_contract()
    monkeypatch.setattr(native, "load_absolute_generalization_contract", lambda _: absolute)
    monkeypatch.setattr(native, "source_surface_fingerprint", lambda *_: "a" * 64)
    monkeypatch.setattr(
        native,
        "git_source_surface_fingerprint",
        lambda _root, revision, _surface: "b" * 64 if revision == "producer" else "a" * 64,
    )
    monkeypatch.setattr(native, "_git", lambda *_: "candidate")
    with pytest.raises(ValueError, match="producer Git economic source"):
        native._trusted_contract(root, root, "producer", "a" * 64)


def test_tradeoff_scope_must_equal_absolute_contract() -> None:
    absolute = load_absolute_generalization_contract()
    tradeoff: dict[str, Any] = {
        "universe": list(absolute.canonical_universe),
        "window": {"start": absolute.window_start.isoformat(), "end": absolute.window_end.isoformat()},
    }
    native._validate_contract_scope(tradeoff, absolute)
    tradeoff["universe"] = tradeoff["universe"][:-1]
    with pytest.raises(ValueError, match="universe"):
        native._validate_contract_scope(tradeoff, absolute)


def test_producer_full_uquant_tree_must_equal_evaluated_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native, "_git", lambda *_: "producer-uquant-tree")
    with pytest.raises(ValueError, match="producer uquant tree"):
        native._verify_producer_tree(Path("."), "a" * 40, "candidate-uquant-tree")


def test_manifest_uses_public_validator_in_exact_temporary_worktree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    git_calls: list[tuple[str, ...]] = []
    process_calls: list[dict[str, object]] = []

    def fake_git(_root: Path, *arguments: str) -> str:
        git_calls.append(arguments)
        if arguments[:3] == ("worktree", "add", "--detach"):
            Path(arguments[3]).mkdir(parents=True)
        return ""

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        process_calls.append({"args": args, **kwargs})
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(native, "_git", fake_git)
    monkeypatch.setattr(subprocess, "run", fake_run)
    raw = {"head": "a" * 40, "cells": []}
    manifest = tmp_path / "manifest.json"
    encoded = json.dumps(raw).encode()
    manifest.write_bytes(encoded)
    native._validate_manifest_at_producer(
        tmp_path, "a" * 40, "b" * 64, manifest, hashlib.sha256(encoded).hexdigest()
    )
    assert git_calls[0][:3] == ("worktree", "add", "--detach")
    assert git_calls[-1][:3] == ("worktree", "remove", "--force")
    arguments = process_calls[0]["args"]
    assert isinstance(arguments, tuple)
    command = arguments[0]
    assert isinstance(command, list)
    assert "validate_shard_manifest" in command[2]
    assert str(manifest) in command
