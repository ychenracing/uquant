"""Public report projection with encrypted, byte-verified continuation originals."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from scripts.daily_scan_store import GitStore, fingerprint, put_json, read_json, safe_path, verify_manifest

PART_BYTES = 8 * 1024 * 1024
REPORT_PATH = re.compile(r"reports/\d{4}-\d{2}-\d{2}/(?:report\.md|result\.json)\Z")
STATUS_FIELDS = ("status", "target_date", "previous_session", "checked_at", "run_url", "source_sha", "started_at", "finished_at", "decision_claimed", "signals_generated")
RESULT_FIELDS = ("schema", "observer_id", "observer_start", "initial_cash", "status", "target_date", "actual_market_date", "previous_session", "source_sha", "run_url", "started_at", "computed_at", "economic_code_hash", "config_sha256", "data_manifest", "warnings", "signals", "comparison")


class SealedFiles:
    """GnuPG owns encryption/integrity; credentials never enter argv or file content."""

    def __init__(self, root: Path, key: str) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", key) is None:
            raise RuntimeError("STATE_KEY_UNAVAILABLE")
        self.root = root
        self.key = key

    def _crypt(self, source: Path, destination: Path, relative: str, *, decrypt: bool) -> None:
        passphrase = hmac.new(bytes.fromhex(self.key), relative.encode(), hashlib.sha256).hexdigest()
        with tempfile.TemporaryDirectory(prefix="uquant-gpg-") as home:
            args = ["gpg", "--no-options", "--homedir", home, "--batch", "--no-tty",
                    "--pinentry-mode", "loopback", "--no-symkey-cache", "--passphrase-fd", "0",
                    "--status-fd", "2", "--output", str(destination)]
            if decrypt:
                args += ["--decrypt"]
            else:
                args += ["--symmetric", "--cipher-algo", "AES256", "--compress-algo", "zlib", "--set-filename", ""]
            args.append(str(source))
            try:
                result = subprocess.run(args, input=(passphrase + "\n").encode(), capture_output=True, timeout=180, check=False)
            finally:
                subprocess.run(["gpgconf", "--homedir", home, "--kill", "gpg-agent"],
                               capture_output=True, timeout=10, check=False)
            if result.returncode or (decrypt and b"[GNUPG:] GOODMDC" not in result.stderr):
                destination.unlink(missing_ok=True)
                raise RuntimeError("SEALED_STATE_AUTHENTICATION_FAILED" if decrypt else "STATE_ENCRYPTION_FAILED")

    def manifest_path(self, relative: str) -> str:
        safe_path(self.root, relative)
        return ".state/" + relative + ".manifest.json"

    def exists(self, relative: str) -> bool:
        return safe_path(self.root, self.manifest_path(relative)).is_file()

    def seal(self, origin: Path, relative: str) -> list[str]:
        if not origin.is_file() or origin.is_symlink():
            raise ValueError("only regular original files may be preserved")
        before = fingerprint(origin)
        paths = []
        with tempfile.TemporaryDirectory(prefix="uquant-seal-") as temporary:
            encrypted = Path(temporary) / "cipher.gpg"
            checked = Path(temporary) / "checked"
            self._crypt(origin, encrypted, relative, decrypt=False)
            self._crypt(encrypted, checked, relative, decrypt=True)
            if fingerprint(origin) != before or fingerprint(checked) != before:
                raise RuntimeError("encryption roundtrip or source identity changed")
            with origin.open("rb") as left, checked.open("rb") as right:
                while block := left.read(1024 * 1024):
                    if block != right.read(len(block)):
                        raise RuntimeError("encryption plaintext byte mismatch")
                if right.read(1):
                    raise RuntimeError("encryption plaintext length mismatch")
            parts = []
            offset = 0
            with encrypted.open("rb") as stream:
                while block := stream.read(PART_BYTES):
                    path = ".state/" + relative + f".gpg.part{len(parts):05d}"
                    destination = safe_path(self.root, path)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(block)
                    parts.append({"path": path, "offset": offset, "size": len(block), "sha256": hashlib.sha256(block).hexdigest()})
                    offset += len(block)
                    paths.append(path)
            manifest_path = self.manifest_path(relative)
            put_json(self.root, manifest_path, {"schema": "uquant.sealed.v1", "relative": relative,
                                                "ciphertext": fingerprint(encrypted), "parts": parts})
            return [*paths, manifest_path]

    def restore(self, relative: str, destination: Path) -> None:
        manifest = read_json(self.root, self.manifest_path(relative))
        if manifest.get("schema") != "uquant.sealed.v1" or manifest.get("relative") != relative:
            raise ValueError("sealed file identity mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="uquant-restore-") as temporary:
            encrypted = Path(temporary) / "cipher.gpg"
            plain = Path(temporary) / "plain"
            offset = 0
            with encrypted.open("wb") as stream:
                for index, part in enumerate(manifest["parts"]):
                    expected = ".state/" + relative + f".gpg.part{index:05d}"
                    if part["path"] != expected or part["offset"] != offset or not 0 < part["size"] <= PART_BYTES:
                        raise ValueError("sealed file part sequence mismatch")
                    path = safe_path(self.root, expected)
                    if fingerprint(path) != {"size": part["size"], "sha256": part["sha256"]}:
                        raise ValueError("sealed file part identity mismatch")
                    with path.open("rb") as source:
                        shutil.copyfileobj(source, stream, length=1024 * 1024)
                    offset += part["size"]
            if fingerprint(encrypted) != manifest["ciphertext"] or offset != manifest["ciphertext"]["size"]:
                raise ValueError("sealed file ciphertext identity mismatch")
            self._crypt(encrypted, plain, relative, decrypt=True)
            shutil.copyfile(plain, destination)
            os.chmod(destination, 0o600)


class PublicReportStore:
    """Keep the working account private; publish only reports and sealed originals."""

    def __init__(self, root: Path, repository: GitStore, key: str) -> None:
        self.root = root
        self.repository = repository
        self.sealed = SealedFiles(repository.root, key)
        root.mkdir(parents=True, mode=0o700, exist_ok=False)
        if self.sealed.exists("latest.json"):
            self.sealed.restore("latest.json", root / "latest.json")
            receipt = read_json(root, "latest.json")
            for relative in receipt["files"]:
                self.sealed.restore(relative, safe_path(root, relative))
            verify_manifest(root, receipt["files"])
        elif (repository.root / "latest.json").exists():
            raise RuntimeError("missing encrypted continuity; refusing account reset")
        for path in (repository.root / ".state/claims").glob("*.json.manifest.json"):
            relative = "claims/" + path.name.removesuffix(".manifest.json")
            self.sealed.restore(relative, safe_path(root, relative))

    def publish(self, paths: list[str], message: str) -> str:
        outgoing = []
        report_dates = set()
        for relative in dict.fromkeys(paths):
            origin = safe_path(self.root, relative)
            outgoing.extend(self.sealed.seal(origin, relative))
            if REPORT_PATH.fullmatch(relative):
                report_dates.add(relative.split("/")[1])
            elif re.fullmatch(r"(?:status|claims)/\d{4}-\d{2}-\d{2}\.json", relative):
                value = read_json(self.root, relative)
                put_json(self.repository.root, relative, {key: value[key] for key in STATUS_FIELDS if key in value})
                outgoing.append(relative)
        for day in sorted(report_dates):
            result_path = f"reports/{day}/result.json"
            original = read_json(self.root, result_path)
            # Projection cannot accidentally inherit a newly added raw account/source field.
            public = {key: original[key] for key in RESULT_FIELDS if key in original}
            if public.get("target_date") != day or public.get("observer_id") != "uquant-13-continuous-no-execution-v1":
                raise ValueError("public report identity mismatch")
            put_json(self.repository.root, result_path, public)
            from scripts.daily_scan_report import render_report

            report_path = f"reports/{day}/report.md"
            # Deliberately regenerate from signal fields, never copy the internal report file.
            safe_path(self.repository.root, report_path).write_text(render_report(public, ""), encoding="utf-8")
            outgoing.extend([result_path, report_path])
        if "latest.json" in paths:
            receipt = read_json(self.root, "latest.json")
            result_path = receipt["result_path"]
            if not REPORT_PATH.fullmatch(result_path) or not result_path.endswith("/result.json"):
                raise ValueError("invalid public result pointer")
            result = read_json(self.repository.root, result_path)
            report_path = result_path.removesuffix("result.json") + "report.md"
            put_json(self.repository.root, "latest.json", {
                "status": result["status"], "target_date": result["target_date"], "source_sha": result["source_sha"],
                "run_url": result["run_url"], "result_path": result_path, "report_path": report_path,
                "files": {path: fingerprint(safe_path(self.repository.root, path)) for path in (result_path, report_path)},
            })
            outgoing.append("latest.json")
        return self.repository.publish(list(dict.fromkeys(outgoing)), message)
