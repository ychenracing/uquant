"""Public-delivery security and continuation checks using synthetic inputs only."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import daily_scan_public
from scripts.daily_scan_market import SYMBOLS
from scripts.daily_scan_public import PublicReportStore, SealedFiles
from scripts.daily_scan_report import comparison, project_signals
from scripts.daily_scan_store import GitStore, fingerprint, put_json, read_json

KEY = "a" * 64
CANARY = "NEVER_PUBLIC_ACCOUNT_OR_DIAGNOSTIC"


def repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    return remote


def open_store(tmp_path: Path, remote: Path, name: str, key: str = KEY) -> PublicReportStore:
    git = GitStore(tmp_path / (name + "-git"), str(remote))
    return PublicReportStore(tmp_path / name, git, key)


def example() -> dict:
    value = {"observer_id": "uquant-13-continuous-no-execution-v1", "status": "COMPLETE",
             "target_date": "2026-09-23", "previous_session": "2026-09-22", "source_sha": "a" * 40,
             "run_url": "https://github.com/geniusgrok/uquant-cli/actions/runs/1", "observer_start": "2026-09-23",
             "initial_cash": 2000000, "started_at": "2026-09-23T17:01:00+08:00", "computed_at": "2026-09-23T17:02:00+08:00",
             "config_sha256": "fixture", "economic_code_hash": "fixture",
             "signals": project_signals({"risk_summary": {}, "opportunity": "NORMAL", "risk": "CAUTION",
                                         "targets": [], "pending_orders": []}, {})}
    value["comparison"] = comparison(value, None)
    return value


def test_public_reports_and_private_state_roundtrip(tmp_path):
    remote = repo(tmp_path)
    first = open_store(tmp_path, remote, "first")
    result = example()
    result["raw_account"] = CANARY
    paths = ["account.json", "reports/2026-09-23/result.json", "reports/2026-09-23/report.md", "claims/2026-09-23.json"]
    put_json(first.root, paths[0], {"internal_state": CANARY})
    put_json(first.root, paths[1], result)
    (first.root / paths[2]).write_text(CANARY)
    put_json(first.root, paths[3], {"status": "COMPLETE", "target_date": "2026-09-23", "internal": CANARY})
    put_json(first.root, "latest.json", {"result_path": paths[1], "files": {path: fingerprint(first.root / path) for path in paths}})
    first.publish([*paths, "latest.json"], "Fixture report publication")
    tracked = first.repository.git("ls-files").stdout.decode().splitlines()
    assert "account.json" not in tracked
    for path in tracked:
        assert CANARY.encode() not in (first.repository.root / path).read_bytes()
    public = read_json(first.repository.root, paths[1])
    assert "raw_account" not in public
    assert tuple(row["symbol"] for row in public["signals"]["stocks"]) == SYMBOLS
    report = (first.repository.root / paths[2]).read_text()
    assert "生产系统完整原始日报" not in report
    assert all(symbol[2:] in report for symbol in SYMBOLS)
    second = open_store(tmp_path, remote, "second")
    for path in [*paths, "latest.json"]:
        assert (first.root / path).read_bytes() == (second.root / path).read_bytes()
    with pytest.raises(RuntimeError, match="AUTHENTICATION_FAILED"):
        open_store(tmp_path, remote, "wrong", "b" * 64)


def test_encrypted_claim_race_and_recovery(tmp_path):
    remote = repo(tmp_path)
    first = open_store(tmp_path, remote, "first")
    put_json(first.root, "status/2026-09-23.json", {"status": "MARKET_NOT_CLOSED", "target_date": "2026-09-23"})
    first.publish(["status/2026-09-23.json"], "Fixture status")
    second = open_store(tmp_path, remote, "second")
    claim = "claims/2026-09-23.json"
    for store, marker in [(first, "one"), (second, "two")]:
        put_json(store.root, claim, {"status": "STARTED", "target_date": "2026-09-23", "owner": marker})
    first.publish([claim], "First claim")
    with pytest.raises(RuntimeError, match="conflict"):
        second.publish([claim], "Competing claim")
    restored = open_store(tmp_path, remote, "restored")
    assert read_json(restored.root, claim)["owner"] == "one"


def test_chunk_integrity_and_path_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(daily_scan_public, "PART_BYTES", 128)
    root = tmp_path / "store"
    root.mkdir()
    source = tmp_path / "original"
    source.write_bytes(os.urandom(1024))
    sealed = SealedFiles(root, KEY)
    paths = sealed.seal(source, "account.json")
    assert len(paths) > 2
    restored = tmp_path / "restored"
    sealed.restore("account.json", restored)
    assert restored.read_bytes() == source.read_bytes()
    part = root / paths[0]
    part.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity mismatch"):
        sealed.restore("account.json", tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    with pytest.raises(ValueError):
        sealed.manifest_path("../escape")


def test_gpg_rejects_modified_ciphertext_even_with_rewritten_manifest(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    source = tmp_path / "source"
    source.write_bytes(os.urandom(1024))
    sealed = SealedFiles(root, KEY)
    paths = sealed.seal(source, "account.json")
    part = root / paths[0]
    block = bytearray(part.read_bytes())
    block[-12] ^= 1
    part.write_bytes(block)
    manifest = read_json(root, paths[-1])
    manifest["parts"][0].update(fingerprint(part))
    manifest["ciphertext"] = fingerprint(part)
    put_json(root, paths[-1], manifest)
    with pytest.raises(RuntimeError, match="AUTHENTICATION_FAILED"):
        sealed.restore("account.json", tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_missing_state_does_not_initialize_over_published_report(tmp_path):
    remote = repo(tmp_path)
    git = GitStore(tmp_path / "git", str(remote))
    put_json(git.root, "latest.json", {"target_date": "2026-09-23"})
    git.publish(["latest.json"], "Fixture report without continuation")
    with pytest.raises(RuntimeError, match="refusing account reset"):
        PublicReportStore(tmp_path / "private", git, KEY)


def test_symlink_original_never_sealed(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    outside = tmp_path / "source"
    outside.write_text(CANARY)
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="regular"):
        SealedFiles(root, KEY).seal(link, "linked")
