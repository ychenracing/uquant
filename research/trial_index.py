"""Trial index derived from the committed research artifacts, never hand-maintained.

Each JSON artifact is listed with its path, bytes and SHA-256 together with
the candidate, status and pass fields it already declares. Missing fields stay
missing: the index does not infer how many trials ran or fill unknown results.
Artifacts are historical evidence and are only read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DECLARED_FIELDS = (
    "name", "candidate", "candidate_source", "candidate_head", "contract_id",
    "status", "passed", "complete", "production_source",
)


def _declared(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in DECLARED_FIELDS
            if isinstance(payload.get(key), (str, int, float, bool))}


def build_index(root: Path = ROOT) -> dict[str, Any]:
    rows = []
    for path in sorted((root / "artifacts").rglob("*.json")):
        raw = path.read_bytes()
        try:
            declared = _declared(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError):
            declared = {"unreadable": True}
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "campaign": path.relative_to(root / "artifacts").parts[0],
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            **declared,
        })
    return {
        "artifact_count": len(rows),
        "by_campaign": dict(sorted(Counter(row["campaign"] for row in rows).items())),
        "declared_failures": sorted(row["path"] for row in rows
                                    if row.get("passed") is False or row.get("status") in {"FAIL", "FAILED"}),
        "artifacts": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    text = json.dumps(build_index(), ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
