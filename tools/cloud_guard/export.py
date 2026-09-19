"""Bounded metadata inspection and byte-verified local preservation bundles."""
import hashlib
import os
import zipfile
from pathlib import Path

from .journal import NAME, atomic, digest, load, packed
from .report import summarize


def directories(root):
    if root.exists():
        for path in sorted(root.iterdir()):
            if path.is_dir() and not path.name.startswith("_"):
                yield path


def inspect_root(root):
    results = []
    for directory in directories(root):
        try:
            results.append(summarize(load(directory)))
        except (OSError, ValueError, KeyError, TypeError):
            results.append({"operation_id": directory.name,
                            "finding": "JOURNAL_UNREADABLE_OR_INCOMPLETE_REVIEW_REQUIRED",
                            "automatic_retry": False})
    return {"operations": results, "empty_does_not_prove_no_platform_work": not results}


def export_bundle(root, output, include_private=False, operation=None):
    """Never upload; select only journal files, never caller-supplied paths."""
    root, output = Path(root).resolve(), Path(output).absolute()
    if output.exists():
        raise ValueError("bundle exists; preserve the previous original")
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    names = ["events.jsonl", "latest.json"]
    if include_private:
        names += ["stdout.private.log", "stderr.private.log"]
    selected = [root / operation] if operation is not None else directories(root)
    for directory in selected:
        if directory.is_symlink() or not NAME.fullmatch(directory.name):
            raise ValueError("untrusted journal directory")
        for name in names:
            path = directory / name
            if path.is_symlink():
                raise ValueError("journal symlink is not an original")
            if path.is_file():
                files.append(path)
    if operation is None:
        files += sorted(root.glob("snapshot-*.json"))
    manifest = {"schema": 1, "private_logs_included": include_private,
                "platform_root_cause": "NOT_OBSERVED", "files": []}
    # ZIP uses deflate streaming, not a base64/string body or in-memory archive.
    try:
        with output.open("xb") as target:
            os.chmod(output, 0o600)
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    if path.is_symlink() or not path.resolve().is_relative_to(root):
                        raise ValueError("bundle path escapes journal")
                    name = path.relative_to(root).as_posix()
                    expected = {"path": name, "bytes": path.stat().st_size, "sha256": digest(path)}
                    archive.write(path, name)
                    manifest["files"].append(expected)
                archive.writestr("MANIFEST.json", packed(manifest) + b"\n")
            target.flush()
            os.fsync(target.fileno())
        with zipfile.ZipFile(output) as archive:
            for row in manifest["files"]:
                h, length = hashlib.sha256(), 0
                with archive.open(row["path"]) as source, (root / row["path"]).open("rb") as original:
                    while chunk := source.read(1024 * 1024):
                        if chunk != original.read(len(chunk)):
                            raise ValueError("journal changed during export")
                        h.update(chunk)
                        length += len(chunk)
                    if original.read(1):
                        raise ValueError("journal grew during export")
                if length != row["bytes"] or h.hexdigest() != row["sha256"]:
                    raise ValueError("bundle content mismatch")
        receipt = {"bundle": str(output), "bytes": output.stat().st_size, "sha256": digest(output),
                   "verified_files": len(files), "private_logs_included": include_private,
                   "remote_saved": False}
        atomic(output.with_suffix(output.suffix + ".receipt.json"), packed(receipt) + b"\n")
        return receipt
    except BaseException:
        # Preserve any partial file, never certify it as a valid bundle.
        raise
