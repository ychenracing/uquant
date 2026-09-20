"""Verify and restore a paused workspace; never launch its jobs.

Length, hash and ordering checks remain active under Python -O. Failed restores
leave an incomplete output for inspection; retry into a new or empty directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def main() -> None:
    """Restore only verified bytes without overwriting existing work."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--parts", required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    metadata = json.loads((base / "SNAPSHOT.json").read_text(encoding="utf-8"))
    output = Path(args.output).resolve()
    parts = Path(args.parts).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise SystemExit("Output must be a new or empty directory; existing work will not be overwritten.")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / "workspace-snapshot.zip"
    receipt = output / "RESTORED_MANIFEST.json"
    whole = hashlib.sha256()
    total = 0
    with archive.open("xb") as destination:
        for row in metadata["parts"]:
            source = (parts / row["name"]).resolve()
            if not source.is_relative_to(parts):
                raise ValueError(f"Unsafe part path: {row['name']}")
            if total != row["offset"]:
                raise ValueError(f"Part offset mismatch: {row['name']}")
            data = source.read_bytes()
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError(f"Part length or hash mismatch: {row['name']}")
            destination.write(data)
            whole.update(data)
            total += len(data)
    if total != metadata["bytes"] or whole.hexdigest() != metadata["sha256"]:
        raise ValueError("Archive length or hash mismatch")
    with zipfile.ZipFile(archive) as snapshot:
        manifest = json.loads(snapshot.read("MANIFEST.json"))
        # Reserve helper outputs and reject aliases/duplicates before any extraction.
        targets = {archive, receipt}
        for row in manifest["files"]:
            target = (output / row["path"]).resolve()
            if not target.is_relative_to(output) or target == output or target in targets:
                raise ValueError(f"Unsafe or duplicate output path: {row['path']}")
            targets.add(target)
        for row in manifest["files"]:
            target = (output / row["path"]).resolve()
            data = snapshot.read("objects/" + row["sha256"])
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError(f"Object length or hash mismatch: {row['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                destination.write(data)
            target.chmod(row["mode"])
        receipt.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"Verified and restored {len(manifest['files'])} files to {output}. "
        "Jobs remain paused. Read HANDOFF_PROMPT.md before resuming."
    )


if __name__ == "__main__":
    main()
