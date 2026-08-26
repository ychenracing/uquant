from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from uquant.validation.generalization_matrix import _hash_json
from uquant.validation.promotion import _artifact_binding

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_WINDOWS = (
    "h1_2023",
    "h2_2023",
    "h1_2024",
    "h2_2024",
    "bull_crash_2025_2026",
    "continuous_ai_era",
)
PREFIX = "ai-era-generalization-123-attempt-2"


def _ci_module() -> Any:
    try:
        return importlib.import_module("uquant.validation.ci_artifacts")
    except ModuleNotFoundError:
        pytest.fail("executable CI artifact validator module is missing")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _performance_candidate() -> dict[str, Any]:
    return {
        "data": {
            "snapshot_id": "snapshot",
            "files_verified": 36,
            "manifest_sha256": "1" * 64,
            "checksums_sha256": "2" * 64,
        },
        "production": {
            "repository": "ychenracing/uquant",
            "commit": "a" * 40,
            "source_sha256": "3" * 64,
        },
        "environment": {
            "python_full_version": "3.12.13",
            "numpy_version": "2.5.1",
            "pandas_version": "3.0.5",
            "uv_version": "0.11.33",
            "uv_lock_sha256": "4" * 64,
        },
        "effective_config_sha256": "5" * 64,
    }


def _performance_payload(candidate: Mapping[str, Any]) -> dict[str, Any]:
    generated_at = "2026-08-16T00:00:00+00:00"
    return {
        "schema_version": 3,
        "profile": "full",
        "passed": True,
        "failures": [],
        "cells": {},
        "protected": {},
        "summary": {},
        "provenance": {
            "candidate": copy.deepcopy(candidate),
            "binding": _artifact_binding(candidate, generated_at=generated_at),
            "baseline_sha256": "6" * 64,
            "validation_fingerprint": "7" * 64,
            "champion_commit": "b" * 40,
            "generated_at": generated_at,
        },
    }


def _run_performance(
    tmp_path: Path,
    payload: Mapping[str, Any],
    *,
    upstream_result: str = "success",
) -> dict[str, Any]:
    module = _ci_module()
    artifact = tmp_path / "phase1.json"
    report = tmp_path / "phase1-diagnostic.json"
    _write_json(artifact, payload)
    result = module.run_phase1_validation(
        artifact=artifact,
        report_output=report,
        upstream_result=upstream_result,
        expected_candidate=_performance_candidate(),
        checkout_head="a" * 40,
    )
    assert json.loads(report.read_text(encoding="utf-8")) == result
    return result


def test_performance_validator_accepts_exact_full_provenance_and_success(tmp_path: Path) -> None:
    """Catches the executable validator rejecting a complete exact-HEAD artifact."""
    result = _run_performance(tmp_path, _performance_payload(_performance_candidate()))

    assert result == {"passed": True, "failures": []}


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda payload: payload["provenance"]["candidate"].pop("environment"),
            "candidate provenance differs",
        ),
        (
            lambda payload: payload["provenance"]["candidate"]["production"].update(
                commit="c" * 40
            ),
            "candidate provenance differs",
        ),
        (
            lambda payload: payload.update(passed=False, failures=["economic gate failed"]),
            "performance gate did not pass",
        ),
    ),
    ids=("incomplete-provenance", "stale-head", "failed-gate"),
)
def test_performance_validator_rejects_incomplete_stale_or_failed_artifact(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    """Catches incomplete/stale provenance or an advertised failed performance gate."""
    payload = _performance_payload(_performance_candidate())
    mutate(payload)

    result = _run_performance(tmp_path, payload)

    assert result["passed"] is False
    assert any(message in failure for failure in result["failures"])


def test_performance_validator_rejects_upstream_failure_and_writes_diagnostics(tmp_path: Path) -> None:
    """Catches a failed gate step being converted to success by provenance readback."""
    result = _run_performance(
        tmp_path,
        _performance_payload(_performance_candidate()),
        upstream_result="failure",
    )

    assert result["passed"] is False
    assert "upstream performance result was failure" in result["failures"]


def test_performance_validator_writes_diagnostic_when_authoritative_provenance_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches production provenance construction escaping before diagnostics exist."""
    module = _ci_module()
    artifact = tmp_path / "phase1.json"
    report = tmp_path / "phase1-diagnostic.json"
    _write_json(artifact, _performance_payload(_performance_candidate()))

    def fail_runtime_provenance(data_dir: str | Path) -> dict[str, Any]:
        raise RuntimeError(f"authoritative provenance unavailable for {data_dir}")

    monkeypatch.setattr(module, "_runtime_provenance", fail_runtime_provenance)

    exit_code = module.main(
        [
            "phase1",
            "--artifact",
            str(artifact),
            "--report-output",
            str(report),
            "--upstream-result",
            "success",
            "--data-dir",
            "data/frozen",
        ]
    )
    result = json.loads(report.read_text(encoding="utf-8"))

    assert exit_code == 1
    assert result["passed"] is False
    assert result["failures"] == [
        "cannot construct authoritative performance provenance: "
        "authoritative provenance unavailable for data/frozen"
    ]


def test_performance_validator_rejects_duplicate_json_keys_and_writes_diagnostics(
    tmp_path: Path,
) -> None:
    """Catches ambiguous duplicate-key evidence being silently last-key-wins parsed."""
    module = _ci_module()
    artifact = tmp_path / "phase1.json"
    report = tmp_path / "phase1-diagnostic.json"
    artifact.write_text(
        '{"passed":true,"provenance":{},"provenance":{}}\n',
        encoding="utf-8",
    )

    result = module.run_phase1_validation(
        artifact=artifact,
        report_output=report,
        upstream_result="success",
        expected_candidate=_performance_candidate(),
        checkout_head="a" * 40,
    )

    assert result["passed"] is False
    assert any("duplicate JSON key: provenance" in failure for failure in result["failures"])
    assert json.loads(report.read_text(encoding="utf-8")) == result


@pytest.mark.parametrize(
    "mutation",
    (
        "provenance_type",
        "provenance_fields",
        "generated_at",
        "binding",
        "candidate_type",
        "production",
        "binding_type",
    ),
)
def test_performance_validator_rejects_every_ambiguous_provenance_shape(
    mutation: str,
    tmp_path: Path,
) -> None:
    payload = _performance_payload(_performance_candidate())
    if mutation == "provenance_type":
        payload["provenance"] = []
    elif mutation == "provenance_fields":
        payload["provenance"].pop("baseline_sha256")
    elif mutation == "generated_at":
        payload["provenance"]["generated_at"] = ""
    elif mutation == "binding":
        payload["provenance"]["binding"]["production_commit"] = "f" * 40
    elif mutation == "candidate_type":
        payload["provenance"]["candidate"] = []
    elif mutation == "production":
        payload["provenance"]["candidate"].pop("production")
    else:
        payload["provenance"]["binding"] = []

    result = _run_performance(tmp_path, payload)

    assert result["passed"] is False
    assert result["failures"]


def test_ci_artifact_scalar_helpers_reject_malformed_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ci_module()
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a JSON object"):
        module._load_json_object(array, label="fixture")
    with pytest.raises(ValueError, match="has no cells"):
        module._shard_fingerprints("h1_2023", [])
    with pytest.raises(ValueError, match="missing aggregate metrics"):
        module._valid_aggregate([{"economic": True, "replay_error": None}])
    with pytest.raises(ValueError, match="not an integer"):
        module._valid_aggregate(
            [
                {
                    "economic": True,
                    "replay_error": None,
                    "metrics": {
                        "final_wealth": 1.0,
                        "max_drawdown": 0.0,
                        "account_orders": True,
                    },
                }
            ]
        )
    monkeypatch.setattr(module, "_load_json_object", lambda *_args, **_kwargs: {})
    with pytest.raises(ValueError, match="scenario contract is malformed"):
        module._policy_scenario_contract()


def _champion() -> dict[str, Any]:
    return json.loads(
        (ROOT / "artifacts" / "phase2" / "champion-generalization-matrix.json").read_text(
            encoding="utf-8"
        )
    )


def _common_provenance(champion: Mapping[str, Any]) -> dict[str, Any]:
    provenance = champion["provenance"]
    return {
        key: copy.deepcopy(value)
        for key, value in provenance.items()
        if key not in {"window_fingerprint", "scenario_fingerprint", "evidence_fingerprint"}
    }


def _shard_provenance(
    common: Mapping[str, Any],
    *,
    window: str,
    cells: list[dict[str, Any]],
) -> dict[str, Any]:
    first = cells[0]
    scenario_payload = [
        {
            "window": {
                "name": cell["window"],
                "start": cell["start"],
                "end": cell["end"],
            },
            "name": cell["scenario"],
            "family": cell["family"],
            "symbols": cell["symbols"],
            "reference_symbols": cell["reference_symbols"],
            "removed_symbols": cell["removed_symbols"],
            "status": cell["status"],
            "industry": cell["industry"],
            "pool_size": cell["pool_size"],
            "seed_index": cell["seed_index"],
            "derived_seed": cell["derived_seed"],
            "evidence": cell["evidence"],
        }
        for cell in cells
    ]
    return {
        **copy.deepcopy(common),
        "window_fingerprint": _hash_json(
            [{"name": window, "start": first["start"], "end": first["end"]}]
        ),
        "scenario_fingerprint": _hash_json(scenario_payload),
        "evidence_fingerprint": _hash_json(
            [{"window": window, "evidence": first["evidence"]}]
        ),
    }


def _write_champion_shards(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    champion = _champion()
    common = _common_provenance(champion)
    root = tmp_path / "shards"
    for window in OFFICIAL_WINDOWS:
        cells = [
            copy.deepcopy(cell) for cell in champion["cells"] if cell["window"] == window
        ]
        failures = [
            failure for failure in champion["failures"] if f"{window}/" in failure
        ]
        aggregate = copy.deepcopy(champion["aggregates"]["by_window"][window])
        shard = {
            "schema_version": champion["schema_version"],
            "gate": champion["gate"],
            "passed": not failures,
            "failures": failures,
            "provenance": _shard_provenance(common, window=window, cells=cells),
            "concentration_definition": copy.deepcopy(champion["concentration_definition"]),
            "aggregates": {"all": aggregate, "by_window": {window: aggregate}},
            "cells": cells,
        }
        _write_json(root / f"{PREFIX}-{window}" / f"{window}.json", shard)
    return root, common


def _run_generalization(
    tmp_path: Path,
    *,
    mutate: Callable[[Path], None] | None = None,
    upstream_result: str = "success",
) -> dict[str, Any]:
    module = _ci_module()
    shard_root, common = _write_champion_shards(tmp_path)
    if mutate is not None:
        mutate(shard_root)
    report = tmp_path / "generalization-diagnostic.json"
    merged = tmp_path / "generalization-merged.json"
    result = module.run_generalization_validation(
        shard_root=shard_root,
        artifact_prefix=PREFIX,
        report_output=report,
        merged_output=merged,
        upstream_result=upstream_result,
        expected_common_provenance=common,
        expected_schema_version=1,
        data_dir=None,
    )
    assert json.loads(report.read_text(encoding="utf-8")) == result
    return result


def test_generalization_validator_rejects_missing_and_extra_attempt_shards(
    tmp_path: Path,
) -> None:
    """Catches an incomplete attempt or a broad pattern mixing an extra artifact."""

    def missing(root: Path) -> None:
        target = root / f"{PREFIX}-h1_2023" / "h1_2023.json"
        target.unlink()
        target.parent.rmdir()

    missing_result = _run_generalization(tmp_path / "missing", mutate=missing)
    assert any("artifact set differs" in failure for failure in missing_result["failures"])

    def extra(root: Path) -> None:
        (root / f"{PREFIX}-prior-attempt").mkdir()

    extra_result = _run_generalization(tmp_path / "extra", mutate=extra)
    assert any("artifact set differs" in failure for failure in extra_result["failures"])


def test_generalization_validator_rejects_duplicate_cell_and_mixed_window(
    tmp_path: Path,
) -> None:
    """Catches duplicate evidence or a shard carrying records from another window."""

    def duplicate(root: Path) -> None:
        path = root / f"{PREFIX}-h1_2023" / "h1_2023.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cells"].append(copy.deepcopy(payload["cells"][0]))
        _write_json(path, payload)

    duplicate_result = _run_generalization(tmp_path / "duplicate", mutate=duplicate)
    assert any("duplicate cell" in failure for failure in duplicate_result["failures"])

    def mixed(root: Path) -> None:
        path = root / f"{PREFIX}-h2_2023" / "h2_2023.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cells"][0]["window"] = "h1_2023"
        _write_json(path, payload)

    mixed_result = _run_generalization(tmp_path / "mixed", mutate=mixed)
    assert any("contains mixed windows" in failure for failure in mixed_result["failures"])


def test_generalization_validator_rejects_stale_exact_head_provenance(
    tmp_path: Path,
) -> None:
    """Catches a complete shard set whose HEAD/provenance belongs to another checkout."""

    def stale(root: Path) -> None:
        path = root / f"{PREFIX}-h1_2024" / "h1_2024.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["provenance"]["head"] = "f" * 40
        _write_json(path, payload)

    result = _run_generalization(tmp_path, mutate=stale)

    assert any("provenance differs from exact HEAD and inputs" in failure for failure in result["failures"])


def test_generalization_validator_rejects_replay_policy_and_upstream_failure_with_diagnostics(
    tmp_path: Path,
) -> None:
    """Catches a changed replay error or upstream failure being hidden by aggregation."""

    def changed_replay_error(root: Path) -> None:
        path = root / f"{PREFIX}-continuous_ai_era" / "continuous_ai_era.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        replay_error = next(
            cell for cell in payload["cells"] if cell["replay_error"] is not None
        )
        replay_error["replay_error"]["message"] = "changed replay failure"
        _write_json(path, payload)

    result = _run_generalization(
        tmp_path / "policy",
        mutate=changed_replay_error,
    )

    assert result["passed"] is False
    assert any("continuous_ai_era: shard gate failed" in failure for failure in result["failures"])
    assert any("policy/evidence validation failed" in failure for failure in result["failures"])
    assert any("cell replay failed" in failure for failure in result["failures"])

    upstream = _run_generalization(tmp_path / "upstream", upstream_result="failure")
    assert "generalization shard job result was failure" in upstream["failures"]


def test_generalization_validator_rejects_duplicate_shard_json_key(tmp_path: Path) -> None:
    """Catches a shard using duplicate JSON keys to make provenance ambiguous."""

    def duplicate_key(root: Path) -> None:
        path = root / f"{PREFIX}-h2_2024" / "h2_2024.json"
        path.write_text('{"cells":[],"cells":[],"provenance":{}}\n', encoding="utf-8")

    result = _run_generalization(tmp_path, mutate=duplicate_key)

    assert any("duplicate JSON key: cells" in failure for failure in result["failures"])


@pytest.mark.parametrize(
    "mutation",
    ("cells", "provenance", "unknown_cell", "malformed_contract"),
)
def test_generalization_validator_rejects_every_ambiguous_shard_shape(
    mutation: str,
    tmp_path: Path,
) -> None:
    def mutate(root: Path) -> None:
        path = root / f"{PREFIX}-h1_2023" / "h1_2023.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "cells":
            payload["cells"] = "bad"
        elif mutation == "provenance":
            payload["provenance"] = []
        elif mutation == "unknown_cell":
            payload["cells"][0]["scenario"] = "unknown"
        else:
            payload["cells"][0].pop("symbols")
        _write_json(path, payload)

    result = _run_generalization(tmp_path, mutate=mutate)

    assert result["passed"] is False
    assert result["failures"]


def test_generalization_validator_reports_compiled_control_and_outer_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _ci_module()
    monkeypatch.setattr(module, "_OFFICIAL_WINDOWS", ("changed",))
    report = module.run_generalization_validation(
        shard_root=tmp_path,
        artifact_prefix=PREFIX,
        report_output=tmp_path / "report.json",
        merged_output=tmp_path / "merged.json",
        upstream_result="success",
        data_dir=None,
        expected_common_provenance={},
    )
    assert any("official window set" in failure for failure in report["failures"])

    monkeypatch.setattr(module, "_policy_scenario_contract", lambda: (_ for _ in ()).throw(TypeError("bad")))
    report = module.run_generalization_validation(
        shard_root=tmp_path,
        artifact_prefix=PREFIX,
        report_output=tmp_path / "outer-report.json",
        merged_output=tmp_path / "outer-merged.json",
        upstream_result="success",
        data_dir=None,
        expected_common_provenance={},
    )
    assert any("aggregator raised TypeError" in failure for failure in report["failures"])
