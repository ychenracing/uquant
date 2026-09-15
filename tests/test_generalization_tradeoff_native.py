"""Bounded native Absolute evidence integration for the tradeoff gate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import generalization_tradeoff_native as native


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
    cells = [_Cell(str(index), "COMPLETE", _Metrics(0.5 if index == 0 else 2.0, 0.36 if index == 33 else 0.1)) for index in range(34)]
    result = native._evaluate_cells(cells, tuple(str(index) for index in range(34)), _policy())
    assert result["status"] == "FAIL"
    assert {failure["metric"] for failure in result["failures"]} == {"worst_drawdown"}


def test_native_cells_reject_missing_duplicate_and_replay_error() -> None:
    universe = tuple(str(index) for index in range(34))
    complete = [_Cell(symbol, "COMPLETE", _Metrics(2.0, 0.1)) for symbol in universe]
    for cells in (complete[:-1], [*complete[:-1], complete[0]], [*complete[:-1], _Cell(universe[-1], "REPLAY_ERROR", None)]):
        result = native._evaluate_cells(cells, universe, _policy())
        assert result["status"] == "INCOMPLETE"
        assert result["passed"] is False


def test_public_evaluator_fails_closed_on_missing_evidence(tmp_path: Path) -> None:
    missing = native.evaluate_native(tmp_path, tmp_path, "d" * 40, tmp_path / "none", {"native_absolute": _policy()})
    assert missing["status"] == "INCOMPLETE"


def test_producer_source_is_recomputed_from_original_head(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).parents[1]
    absolute = native.load_absolute_generalization_contract()
    monkeypatch.setattr(native, "load_absolute_generalization_contract", lambda _: absolute)
    monkeypatch.setattr(native, "source_surface_fingerprint", lambda *_: "a" * 64)
    monkeypatch.setattr(native, "git_source_surface_fingerprint", lambda _root, revision, _surface: "b" * 64 if revision == "producer" else "a" * 64)
    monkeypatch.setattr(native, "_git", lambda *_: "candidate")
    with pytest.raises(ValueError, match="producer Git economic source"):
        native._trusted_contract(root, root, "producer", "a" * 64)


def test_tradeoff_scope_must_equal_absolute_contract() -> None:
    absolute = native.load_absolute_generalization_contract()
    tradeoff = {
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
