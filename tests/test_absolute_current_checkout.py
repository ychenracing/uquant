"""Real checkout contract tests; no simulated producer authority."""
from pathlib import Path

from uquant.provenance.fingerprints import source_surface_fingerprint
from uquant.validation.absolute_generalization.contract import load_absolute_generalization_contract

ROOT = Path(__file__).resolve().parents[1]


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
