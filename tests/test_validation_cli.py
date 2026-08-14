from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from uquant.validation import cli


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_generalization_cli_passes_explicit_causal_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _write_json(tmp_path / "baseline.json", {})
    industries = _write_json(tmp_path / "industries.json", {"a": "memory", "b": "optical"})
    output = tmp_path / "report.json"
    observed: dict[str, Any] = {}

    def fake_run_generalization(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"passed": True, "gate": "generalization"}

    monkeypatch.setattr(cli, "run_generalization", fake_run_generalization)
    status = cli.main(
        [
            "generalization",
            "--data-dir",
            str(tmp_path),
            "--universe",
            "a",
            "b",
            "--industries",
            str(industries),
            "--prior-symbols",
            "a",
            "--start",
            "2025-01-02",
            "--end",
            "2026-07-20",
            "--baseline",
            str(baseline),
            "--random-seed-count",
            "6",
            "--output",
            str(output),
        ]
    )

    assert status == 0
    assert observed == {
        "data_dir": str(tmp_path),
        "universe": ["a", "b"],
        "industries": {"a": "memory", "b": "optical"},
        "prior_symbols": ["a"],
        "start": "2025-01-02",
        "end": "2026-07-20",
        "baseline_path": baseline,
        "lookback_sessions": 120,
        "random_seeds": range(6),
    }
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True


def test_generalization_cli_defaults_to_report_minimum_replay_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _write_json(tmp_path / "baseline.json", {})
    industries = _write_json(tmp_path / "industries.json", {"a": "memory"})
    observed: dict[str, Any] = {}

    def fake_run_generalization(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"passed": True}

    monkeypatch.setattr(cli, "run_generalization", fake_run_generalization)
    status = cli.main(
        [
            "generalization",
            "--data-dir",
            str(tmp_path),
            "--universe",
            "a",
            "--industries",
            str(industries),
            "--prior-symbols",
            "a",
            "--start",
            "2025-01-02",
            "--end",
            "2026-07-20",
            "--baseline",
            str(baseline),
        ]
    )

    assert status == 0
    assert observed["random_seeds"] == range(300)


def test_generalization_cli_returns_one_when_economic_gate_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _write_json(tmp_path / "baseline.json", {})
    industries = _write_json(tmp_path / "industries.json", {"a": "memory", "b": "optical"})
    monkeypatch.setattr(
        cli,
        "run_generalization",
        lambda **_: {
            "passed": False,
            "failures": ["pareto: no material improvement without material regression"],
        },
    )

    status = cli.main(
        [
            "generalization",
            "--data-dir",
            str(tmp_path),
            "--universe",
            "a",
            "b",
            "--industries",
            str(industries),
            "--prior-symbols",
            "a",
            "--start",
            "2025-01-02",
            "--end",
            "2026-07-20",
            "--baseline",
            str(baseline),
        ]
    )

    assert status == 1


def test_generalization_cli_rejects_duplicate_industry_symbols(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _write_json(tmp_path / "baseline.json", {})
    industries = tmp_path / "industries.json"
    industries.write_text('{"a":"memory","a":"optical"}', encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "run_generalization",
        lambda **_: pytest.fail("runner must not start for malformed provenance"),
    )

    with pytest.raises(RuntimeError, match="cannot load generalization industry map"):
        cli.main(
            [
                "generalization",
                "--data-dir",
                str(tmp_path),
                "--universe",
                "a",
                "--industries",
                str(industries),
                "--prior-symbols",
                "a",
                "--start",
                "2025-01-02",
                "--end",
                "2026-07-20",
                "--baseline",
                str(baseline),
            ]
        )


def test_generalization_cli_missing_reference_is_fail_closed(tmp_path: Path) -> None:
    industries = _write_json(tmp_path / "industries.json", {"a": "memory"})
    missing = tmp_path / "missing.json"

    with pytest.raises(RuntimeError, match="generalization gate is fail-closed"):
        cli.main(
            [
                "generalization",
                "--data-dir",
                str(tmp_path),
                "--universe",
                "a",
                "--industries",
                str(industries),
                "--prior-symbols",
                "a",
                "--start",
                "2025-01-02",
                "--end",
                "2026-07-20",
                "--baseline",
                str(missing),
            ]
        )
    assert not missing.exists()


def test_generalization_matrix_cli_runs_full_fixed_contract_and_writes_canonical_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a CLI that requires duplicate universe inputs or emits unstable JSON."""
    observed: dict[str, Any] = {}

    def fake_matrix(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"z": 1, "passed": True, "a": [2, 3]}

    monkeypatch.setattr(cli, "run_generalization_matrix", fake_matrix)
    output = tmp_path / "nested" / "matrix.json"
    status = cli.main(
        [
            "generalization-matrix",
            "--data-dir",
            str(tmp_path),
            "--output",
            str(output),
        ]
    )

    assert status == 0
    assert observed == {
        "data_dir": str(tmp_path),
        "window_names": None,
        "lookback_sessions": 120,
    }
    assert output.read_text(encoding="utf-8") == '{"a":[2,3],"passed":true,"z":1}\n'


def test_generalization_matrix_cli_passes_exact_named_window_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches interval overrides or a hidden configurable seed count in blocking mode."""
    observed: dict[str, Any] = {}

    def fake_matrix(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"passed": False, "failures": ["fixture"]}

    monkeypatch.setattr(cli, "run_generalization_matrix", fake_matrix)
    status = cli.main(
        [
            "generalization-matrix",
            "--data-dir",
            str(tmp_path),
            "--window",
            "h2_2024",
            "--window",
            "h1_2023",
        ]
    )

    assert status == 1
    assert observed == {
        "data_dir": str(tmp_path),
        "window_names": ("h2_2024", "h1_2023"),
        "lookback_sessions": 120,
    }
    with pytest.raises(SystemExit):
        cli.main(
            [
                "generalization-matrix",
                "--data-dir",
                str(tmp_path),
                "--random-seed-count",
                "6",
            ]
        )


def test_competitor_cli_uses_reviewed_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = _write_json(tmp_path / "competitors.json", {})
    observed: dict[str, Any] = {}

    def fake_run_competitor_gate(**kwargs: Any) -> dict[str, Any]:
        observed.update(kwargs)
        return {"passed": False, "failures": ["wealth"]}

    monkeypatch.setattr(cli, "run_competitor_gate", fake_run_competitor_gate)
    status = cli.main(
        [
            "competitor",
            "--data-dir",
            str(tmp_path),
            "--reference",
            str(reference),
        ]
    )

    assert status == 1
    assert observed == {"data_dir": str(tmp_path), "reference_path": reference}


def test_competitor_cli_missing_reference_is_fail_closed_and_never_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "competitor_matrix_reference.json"
    monkeypatch.setattr(
        cli,
        "run_competitor_gate",
        lambda **_: pytest.fail("runner must not start without reviewed evidence"),
    )

    with pytest.raises(
        RuntimeError,
        match=r"competitor gate is fail-closed.*refusing to create a placeholder",
    ):
        cli.main(
            [
                "competitor",
                "--data-dir",
                str(tmp_path),
                "--reference",
                str(missing),
            ]
        )
    assert not missing.exists()
