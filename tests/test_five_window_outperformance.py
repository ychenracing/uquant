from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_five_window_outperformance.py"
SPEC = importlib.util.spec_from_file_location("five_window_outperformance_under_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
outperformance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = outperformance
SPEC.loader.exec_module(outperformance)


def _row(system: str, pool: str, window: str, **overrides: object) -> dict[str, object]:
    start, end = outperformance.WINDOWS[window]
    row: dict[str, object] = {
        "system": system,
        "pool": pool,
        "window": window,
        "start": start,
        "end": end,
        "final_wealth": 2.0 if system == "uquant" else 1.5,
        "max_drawdown": 0.10 if system == "uquant" else 0.15,
        "account_orders": 5 if system == "uquant" else 6,
        "gross_turnover": 1.0,
        "acute_return": 0.01 if system == "uquant" else 0.0,
        "evidence_sha256": "a" * 64,
    }
    row.update(overrides)
    return row


def _complete_rows() -> list[dict[str, object]]:
    return [
        _row(system, pool, window)
        for system in outperformance.SYSTEMS
        for pool in outperformance.POOLS
        for window in outperformance.WINDOWS
    ]


def test_five_window_contract_and_acute_boundaries_are_exact() -> None:
    assert outperformance.WINDOWS == {
        "h1_2023": ("2023-01-03", "2023-06-30"),
        "h2_2023": ("2023-07-03", "2023-12-29"),
        "h1_2024": ("2024-01-02", "2024-07-01"),
        "h2_2024": ("2024-07-01", "2024-12-31"),
        "bull_crash_2025_2026": ("2025-01-02", "2026-07-31"),
    }
    assert outperformance.ACUTE_WINDOWS == {
        "h1_2023": ("2023-04-20", "2023-05-25"),
        "h2_2023": ("2023-07-26", "2023-08-25"),
        "h1_2024": ("2024-01-03", "2024-02-02"),
        "h2_2024": ("2024-08-01", "2024-09-02"),
        "bull_crash_2025_2026": ("2026-06-30", "2026-07-30"),
    }


def test_git_identity_fails_closed_when_git_cannot_be_resolved(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(outperformance.shutil, "which", lambda executable: None)

    with pytest.raises(RuntimeError, match="resolve git executable"):
        outperformance._git_identity(tmp_path)


def test_evaluate_requires_one_hundred_cells_and_seventy_five_comparisons() -> None:
    rows = _complete_rows()

    report = outperformance.evaluate(rows)

    assert report["summary"] == {"cells": 100, "pairwise_comparisons": 75, "passed": 75}
    assert report["passed"]
    with pytest.raises(RuntimeError, match="missing outperformance cells"):
        outperformance.evaluate(rows[:-1])
    with pytest.raises(RuntimeError, match="duplicate outperformance cell"):
        outperformance.evaluate([*rows, dict(rows[0])])


@pytest.mark.parametrize(
    ("field", "value", "predicate"),
    [
        ("final_wealth", 1.49, "final_wealth"),
        ("max_drawdown", 0.151, "max_drawdown"),
        ("account_orders", 7, "account_orders"),
        ("acute_return", -0.01, "acute_return"),
    ],
)
def test_every_metric_is_a_strict_no_regression_gate(
    field: str,
    value: float | int,
    predicate: str,
) -> None:
    rows = _complete_rows()
    candidate = next(
        row
        for row in rows
        if row["system"] == "uquant"
        and row["pool"] == "a"
        and row["window"] == "h1_2023"
    )
    candidate[field] = value

    report = outperformance.evaluate(rows)

    assert not report["passed"]
    comparison = report["comparisons"]["h1_2023/a/aquant"]
    assert not comparison["predicates"][predicate]


def test_equal_on_all_metrics_does_not_count_as_outperformance() -> None:
    rows = _complete_rows()
    candidate = next(
        row
        for row in rows
        if row["system"] == "uquant"
        and row["pool"] == "a"
        and row["window"] == "h1_2023"
    )
    competitor = next(
        row
        for row in rows
        if row["system"] == "aquant"
        and row["pool"] == "a"
        and row["window"] == "h1_2023"
    )
    for field in outperformance.METRICS:
        candidate[field] = competitor[field]

    report = outperformance.evaluate(rows)

    assert "h1_2023/a/aquant:strictly_better" in report["failures"]


def _complete_competitor_payload(repository_root: Path) -> dict[str, object]:
    pools = outperformance._promotion_pools(repository_root)
    data_dir = repository_root / "data" / "frozen"
    rows: list[dict[str, object]] = []
    for system in outperformance.COMPETITORS:
        for pool in outperformance.POOLS:
            for window, (start, end) in outperformance.WINDOWS.items():
                acute_start, acute_end = outperformance.ACUTE_WINDOWS[window]
                dates = sorted({start, acute_start, acute_end, end})
                rows.append(
                    {
                        "system": system,
                        "pool": pool,
                        "window": window,
                        "requested_symbols": list(pools[pool]),
                        "effective_symbols": list(
                            outperformance._effective_symbols(
                                pools[pool],
                                data_dir=data_dir,
                                as_of=start,
                            )
                        ),
                        "start": start,
                        "end": end,
                        "final_wealth": 1.0,
                        "max_drawdown": 0.0,
                        "account_orders": 0,
                        "turnover": 0.0,
                        "order_ledger": [],
                        "equity_curve": [
                            {"date": date, "equity": outperformance.INITIAL_CASH}
                            for date in dates
                        ],
                        "fills": [],
                    }
                )
    adapter = repository_root / "scripts" / "run_window_competitor_adapter.py"
    return {
        "schema_version": 2,
        "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "contract": copy.deepcopy(outperformance.COMPARISON_CONTRACT),
        "repositories": copy.deepcopy(outperformance.LOCKED_COMPETITOR_SOURCES),
        "source_hashes": {
            system: outperformance.LOCKED_COMPETITOR_SOURCES[system]["python_sha256"]
            for system in outperformance.COMPETITORS
        },
        "data_provenance": {
            "through": outperformance.TARGET_END,
            "sha256": outperformance._bounded_data_fingerprint(data_dir),
        },
        "systems": list(outperformance.COMPETITORS),
        "pools": list(outperformance.POOLS),
        "windows": list(outperformance.WINDOWS),
        "rows": rows,
    }


def test_competitor_artifact_provenance_and_raw_metrics_fail_closed() -> None:
    repository_root = SCRIPT.parents[1]
    payload = _complete_competitor_payload(repository_root)

    rows, evidence = outperformance._compact_competitor_rows(
        payload,
        repository_root=repository_root,
    )

    assert len(rows) == 75
    assert len(evidence) == 75

    mutations = (
        ("execution contract", lambda item: item["contract"].update(initial_cash=1.0)),
        (
            "repository locks",
            lambda item: item["repositories"]["aquant"].update(commit="corrupt"),
        ),
        (
            "source hashes",
            lambda item: item["source_hashes"].update(aquant="0" * 64),
        ),
        ("adapter hash", lambda item: item.update(adapter_sha256="0" * 64)),
        (
            "data provenance",
            lambda item: item["data_provenance"].update(sha256="0" * 64),
        ),
        (
            "requested pool membership",
            lambda item: item["rows"][0].update(requested_symbols=[]),
        ),
        (
            "effective pool membership",
            lambda item: item["rows"][0].update(effective_symbols=[]),
        ),
        (
            "evidence metric mismatch",
            lambda item: item["rows"][0].update(final_wealth=2.0),
        ),
    )
    for message, mutate in mutations:
        corrupted = copy.deepcopy(payload)
        mutate(corrupted)
        with pytest.raises(RuntimeError, match=message):
            outperformance._compact_competitor_rows(
                corrupted,
                repository_root=repository_root,
            )


def test_content_addressed_evidence_rejects_missing_or_unreferenced_blobs() -> None:
    rows = [_row("uquant", "a", "h1_2023")]
    acute_start, acute_end = outperformance.ACUTE_WINDOWS["h1_2023"]
    evidence = {
        "equity_curve": [
            {"date": acute_start, "equity": outperformance.INITIAL_CASH},
            {"date": acute_end, "equity": outperformance.INITIAL_CASH * 1.01},
        ],
        "order_ledger": [{} for _ in range(5)],
        "fills": [{"gross_value": outperformance.INITIAL_CASH}],
    }
    digest = outperformance._canonical_hash(evidence)
    rows[0]["evidence_sha256"] = digest
    rows[0]["final_wealth"] = 1.01
    rows[0]["max_drawdown"] = 0.0
    rows[0]["gross_turnover"] = 1.0
    rows[0]["acute_return"] = 0.01

    outperformance._validate_evidence(rows, {digest: evidence})

    with pytest.raises(RuntimeError, match="missing or not content-addressed"):
        outperformance._validate_evidence(rows, {})
    with pytest.raises(RuntimeError, match="unreferenced blobs"):
        outperformance._validate_evidence(
            rows,
            {digest: evidence, "0" * 64: {}},
        )


def test_output_cannot_overwrite_the_competitor_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a report path destroying the evidence consumed by the report."""

    artifact = tmp_path / "competitor.json"
    original = json.dumps({"source": "competitor"})
    artifact.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        outperformance,
        "build",
        lambda **kwargs: {"evaluation": {"passed": True}},
    )

    with pytest.raises(ValueError, match="protected path"):
        outperformance.main(
            [
                "--competitor-results",
                str(artifact),
                "--output",
                str(artifact),
            ]
        )

    assert artifact.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "protected_output",
    [
        SCRIPT.parents[1] / "data" / "frozen" / "SHA256SUMS",
        SCRIPT.parents[1] / "uquant" / "engine.py",
    ],
)
def test_output_preflights_data_and_source_inputs_before_build(
    protected_output: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches evidence output damaging data or source after a costly build."""

    artifact = tmp_path / "competitor.json"
    artifact.write_text("{}", encoding="utf-8")
    original = protected_output.read_bytes()

    def fail_build(**_: object) -> dict[str, object]:
        raise AssertionError("outperformance build started before output preflight")

    monkeypatch.setattr(outperformance, "build", fail_build)
    with pytest.raises(ValueError, match="protected input tree"):
        outperformance.main(
            [
                "--competitor-results",
                str(artifact),
                "--output",
                str(protected_output),
            ]
        )

    assert protected_output.read_bytes() == original
