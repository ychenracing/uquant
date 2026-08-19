from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from uquant.config import DEFAULT_CONFIG, config_fingerprint
from uquant.engine import code_fingerprint
from uquant.risk_sentinel.cli import sentinel_source_fingerprint
from uquant.risk_sentinel.validation import validate_contracts

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "artifacts" / "sentinel" / "shadow_equivalence.json"
BASELINE_COMMIT = "87f4366683e4531d0744d78380bf5c336fce2f57"
SOURCE_COMMIT = "e02b0ad5c38aa119b2d21cb3142589b1f3f2fae1"
SENTINEL_SOURCE_SHA256 = "0f26fc5be244a985b20cb426b025a909f85939ee7a5ee8905b9367559093b46e"
ECONOMIC_OUTPUTS = {
    "decision_digest",
    "target_weights",
    "orders",
    "fills",
    "final_wealth",
    "max_drawdown",
    "trade_count",
}


def _git_bytes(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{commit}:{path}"])


def test_shadow_artifact_proves_exact_production_bytes_and_import_isolation() -> None:
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    unsealed = {key: value for key, value in payload.items() if key != "canonical_sha256"}

    assert payload["baseline_commit"] == BASELINE_COMMIT
    assert payload["sentinel_source_commit"] == SOURCE_COMMIT
    assert payload["sentinel_source_sha256"] == SENTINEL_SOURCE_SHA256
    assert sentinel_source_fingerprint(ROOT) == SENTINEL_SOURCE_SHA256
    assert payload["effective_config_sha256"] == config_fingerprint(DEFAULT_CONFIG)
    assert payload["production_code_fingerprint"]["baseline"] == code_fingerprint()
    assert payload["production_code_fingerprint"]["candidate"] == code_fingerprint()
    assert set(payload["economic_outputs"]) == ECONOMIC_OUTPUTS
    assert set(payload["economic_outputs"].values()) == {"IDENTICAL_BY_EXACT_SOURCE"}
    assert validate_contracts(ROOT)["import_isolation"] == "PASS"
    encoded = json.dumps(
        unsealed,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert payload["canonical_sha256"] == hashlib.sha256(encoded).hexdigest()

    for path, evidence in payload["protected_paths"].items():
        baseline = hashlib.sha256(_git_bytes(BASELINE_COMMIT, path)).hexdigest()
        candidate = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert evidence == {"baseline_sha256": baseline, "candidate_sha256": candidate}
        assert baseline == candidate
