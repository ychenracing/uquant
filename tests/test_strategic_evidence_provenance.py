from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from research.strategic_evidence.provenance import (
    read_gzip_shard,
    write_gzip_shard,
)


def _provenance() -> dict[str, str]:
    return {
        "base_commit": "a" * 40,
        "experiment_commit": "b" * 40,
        "production_source_sha256": "c" * 64,
        "research_source_sha256": "d" * 64,
        "config_sha256": "e" * 64,
        "data_manifest_sha256": "f" * 64,
        "universe_sha256": "1" * 64,
        "industry_mapping_sha256": "2" * 64,
        "window_sha256": "3" * 64,
        "scenario_sha256": "4" * 64,
        "python": "3.12.0",
        "numpy": "2.5.1",
        "pandas": "3.0.5",
        "uv": "0.9",
        "uv_lock_sha256": "5" * 64,
        "generated_at": "2026-08-26T00:00:00Z",
    }


def test_gzip_shard_is_deterministic_and_rejects_an_unsealed_mutation(tmp_path: Path) -> None:
    """Catches a writer that leaves gzip timestamps or accepts altered evidence."""

    first = tmp_path / "first.jsonl.gz"
    second = tmp_path / "second.jsonl.gz"
    rows = (
        {"date": "2026-01-05", "equity": 100.0},
        {"date": "2026-01-06", "equity": 101.0},
    )
    provenance = _provenance()

    write_gzip_shard(first, rows=rows, provenance=provenance)
    write_gzip_shard(second, rows=rows, provenance=provenance)

    assert first.read_bytes() == second.read_bytes()
    assert len(gzip.decompress(first.read_bytes()).splitlines()) == 3
    assert read_gzip_shard(first)["rows"] == rows

    records = gzip.decompress(first.read_bytes()).splitlines()
    first_row = json.loads(records[1])
    first_row["equity"] = 999.0
    records[1] = json.dumps(first_row, sort_keys=True).encode("utf-8")
    first.write_bytes(gzip.compress(b"\n".join(records) + b"\n", mtime=0))
    with pytest.raises(ValueError, match="unsealed"):
        read_gzip_shard(first)


@pytest.mark.parametrize("mutation", ["missing", "empty", "malformed"])
def test_shard_provenance_fails_closed(mutation: str, tmp_path: Path) -> None:
    """Catches missing, blank, or malformed preregistered shard identities."""

    provenance = _provenance()
    if mutation == "missing":
        provenance.pop("window_sha256")
    elif mutation == "empty":
        provenance["generated_at"] = ""
    else:
        provenance["config_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="provenance"):
        write_gzip_shard(tmp_path / "invalid.jsonl.gz", rows=(), provenance=provenance)
