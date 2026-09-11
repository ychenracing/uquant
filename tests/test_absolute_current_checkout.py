"""Real checkout contract tests; no simulated producer authority."""
from pathlib import Path

import pytest

from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.validation.absolute_generalization.contract import load_absolute_generalization_contract

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("merge_sha", [None, "0" * 40, "a" * 40])
def test_pr_event_binds_merge_parents_not_cached_merge_sha(tmp_path, monkeypatch, merge_sha):
    import json

    from uquant.validation.absolute_generalization.contract import _verify_ci_event

    event = {"number": 61, "pull_request": {
        "base": {"sha": "b" * 40}, "head": {"sha": "c" * 40},
        "merge_commit_sha": merge_sha,
    }}
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_REF", "refs/pull/61/merge")
    _verify_ci_event("a" * 40, ["b" * 40, "c" * 40])
    for parents in (["b" * 40], ["c" * 40, "b" * 40], ["b" * 40, "d" * 40]):
        with pytest.raises(ValueError, match="event target"):
            _verify_ci_event("a" * 40, parents)
    monkeypatch.setenv("GITHUB_REF", "refs/pull/62/merge")
    with pytest.raises(ValueError, match="event target"):
        _verify_ci_event("a" * 40, ["b" * 40, "c" * 40])


@pytest.mark.parametrize("name,event", [
    ("push", {"after": "a" * 40}),
    ("merge_group", {"merge_group": {"head_sha": "a" * 40}}),
    ("workflow_dispatch", {}),
])
def test_ci_event_retains_exact_checkout_binding(tmp_path, monkeypatch, name, event):
    import json

    from uquant.validation.absolute_generalization.contract import _verify_ci_event

    path = tmp_path / "event.json"
    path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", name)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    _verify_ci_event("a" * 40, [])
    with pytest.raises(ValueError, match="checkout differs"):
        _verify_ci_event("b" * 40, [])
    if name != "workflow_dispatch":
        monkeypatch.setenv("GITHUB_SHA", "b" * 40)
        with pytest.raises(ValueError, match="event target"):
            _verify_ci_event("b" * 40, [])


def test_current_checkout_loads_independently_of_historical_candidate() -> None:
    contract = load_absolute_generalization_contract()
    assert contract.candidate.production_source_sha256 == source_surface_fingerprint(
        ROOT, "economic_decision_v1"
    )
    assert contract.thresholds.minimum_positive_return_fraction == 0.9
    assert contract.thresholds.maximum_p90_drawdown == 0.3


def test_preflight_failure_keeps_diagnostic_without_manifest(tmp_path, monkeypatch) -> None:
    import json

    from scripts.run_absolute_generalization_acceptance import main

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "0" * 40)
    output = tmp_path / "manifest.json"
    result = main([
        "--shard", "champion", "--run-id", "test", "--run-attempt", "1",
        "--output", str(output), "--cache-dir", str(tmp_path / "cache"),
        "--data-dir", str(ROOT / "data/frozen"), "--preflight-only",
    ])
    assert result != 0
    assert not output.exists()
    diagnostics = list(tmp_path.glob("*.diagnostic.json"))
    assert len(diagnostics) == 1
    diagnostic = json.loads(diagnostics[0].read_bytes())
    assert "CI checkout differs" in diagnostic["error"]
    assert "canonical_sha256" not in diagnostic


def test_unavailable_relative_baseline_fails_during_preflight(tmp_path, monkeypatch) -> None:
    import json

    from scripts.run_absolute_generalization_acceptance import main
    from uquant.validation import evidence_source

    evidence_source.evidence_root.cache_clear()
    monkeypatch.setattr(evidence_source, "_SOURCE_COMMIT", "0" * 40)
    output = tmp_path / "manifest.json"
    try:
        result = main([
            "--shard", "champion", "--run-id", "missing-baseline", "--run-attempt", "1",
            "--output", str(output), "--cache-dir", str(tmp_path / "cache"),
            "--data-dir", str(ROOT / "data/frozen"), "--preflight-only",
        ])
        assert result == 2
        assert not output.exists()
        diagnostic = json.loads(next(tmp_path.glob("*.diagnostic.json")).read_bytes())
        assert "immutable evidence source unavailable" in diagnostic["error"]
    finally:
        evidence_source.evidence_root.cache_clear()
