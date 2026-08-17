from __future__ import annotations

import importlib.util
import os
import stat
import sys
from email.message import Message
from pathlib import Path
from types import ModuleType

import pytest


def _load_backfill_module() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_tencent_history.py"
    spec = importlib.util.spec_from_file_location("uquant_test_backfill_tencent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Tencent backfill module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backfill = _load_backfill_module()


def _result(symbol: str) -> object:
    return backfill.BackfillResult(
        symbol=symbol,
        first_date="2022-01-04",
        last_date="2022-01-04",
        historical_rows_added=0,
        total_rows=1,
        anchor_max_relative_difference=0.0,
        sha256="0" * 64,
    )


def test_tencent_redirect_handler_rejects_cross_origin_destination() -> None:
    with pytest.raises(backfill.HTTPError, match="refusing cross-origin Tencent redirect"):
        backfill._TencentRedirectHandler().redirect_request(
            backfill.Request(backfill.ENDPOINT),
            None,
            302,
            "Found",
            Message(),
            "http://127.0.0.1/internal",
        )


def test_tencent_redirect_handler_accepts_same_https_origin() -> None:
    redirected = backfill._TencentRedirectHandler().redirect_request(
        backfill.Request(backfill.ENDPOINT),
        None,
        302,
        "Found",
        Message(),
        backfill.ENDPOINT + "?redirected=1",
    )
    assert redirected is not None
    assert redirected.full_url.startswith(backfill.ENDPOINT)


def test_backfill_rejects_symlinked_managed_inputs_before_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Catches a managed CSV or metadata write escaping the selected directory."""

    victim = tmp_path / "victim.csv"
    payload = b"date,open,high,low,close,volume,amount\n2022-01-04,1,1,1,1,1,1\n"
    victim.write_bytes(payload)
    data_dir = tmp_path / "frozen"
    data_dir.mkdir()
    (data_dir / "sz300308.csv").symlink_to(victim)

    def fail_download(_path: Path) -> object:
        raise AssertionError("download must not start for an unsafe managed path")

    monkeypatch.setattr(backfill, "_backfill_one", fail_download)

    with pytest.raises(SystemExit):
        backfill.main(["--data-dir", str(data_dir), "--workers", "1"])

    assert "symlink" in capsys.readouterr().err
    assert victim.read_bytes() == payload


def test_backfill_rejects_a_symlinked_ancestor_before_downloading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Catches an apparently regular data directory reached through a symlink."""

    actual_parent = tmp_path / "actual"
    data_dir = actual_parent / "frozen"
    data_dir.mkdir(parents=True)
    market_data = data_dir / "sz300308.csv"
    original = b"date,open,high,low,close,volume,amount\n2022-01-04,1,1,1,1,1,1\n"
    market_data.write_bytes(original)
    alias = tmp_path / "alias"
    alias.symlink_to(actual_parent, target_is_directory=True)

    def fail_download(_path: Path) -> object:
        raise AssertionError("download must not start through a symlinked ancestor")

    monkeypatch.setattr(backfill, "_backfill_one", fail_download)
    with pytest.raises(SystemExit):
        backfill.main(["--data-dir", str(alias / "frozen"), "--workers", "1"])

    assert "symlink" in capsys.readouterr().err
    assert market_data.read_bytes() == original


def test_metadata_replacement_preserves_prior_bytes_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches truncating managed metadata before its replacement commits."""

    manifest = tmp_path / "DATA_MANIFEST.json"
    checksums = tmp_path / "SHA256SUMS"
    manifest.write_bytes(b"prior manifest\n")
    checksums.write_bytes(b"prior checksums\n")

    def fail_replace(source: object, destination: object) -> None:
        raise OSError("publish interrupted")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError, match="publish interrupted"):
        backfill._write_metadata(tmp_path, [_result("sz300308")])

    assert manifest.read_bytes() == b"prior manifest\n"
    assert checksums.read_bytes() == b"prior checksums\n"


def test_tech_proxy_replacement_does_not_mutate_a_hardlinked_external_inode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches tech-proxy output truncating another name for the same inode."""

    data_dir = tmp_path / "frozen"
    data_dir.mkdir()
    victim = tmp_path / "external.csv"
    prior = b"date,open,high,low,close,volume,amount\n2022-01-04,1,1,1,1,1,1\n"
    updated = b"date,open,high,low,close,volume,amount\n2021-01-04,2,2,2,2,2,2\n"
    victim.write_bytes(prior)
    if os.name != "nt":
        victim.chmod(0o644)
    managed = data_dir / f"{backfill.TECH_INDEX}.csv"
    os.link(victim, managed)
    result = _result(backfill.TECH_INDEX)
    (data_dir / "DATA_MANIFEST.json").write_text(
        backfill.json.dumps({"results": [backfill.asdict(result)]}),
        encoding="utf-8",
    )
    (data_dir / "DATA_MANIFEST.json").chmod(0o640)
    monkeypatch.setattr(
        backfill,
        "_prepend_tech_proxy",
        lambda current, payload: (current, updated),
    )

    if os.name == "nt":
        assert backfill.main(["--data-dir", str(data_dir), "--tech-proxy-only"]) == 0
    else:
        prior_umask = os.umask(0o027)
        try:
            assert (
                backfill.main(["--data-dir", str(data_dir), "--tech-proxy-only"])
                == 0
            )
        finally:
            os.umask(prior_umask)

    assert victim.read_bytes() == prior
    assert managed.read_bytes() == updated
    if os.name != "nt":
        assert stat.S_IMODE(managed.stat().st_mode) == 0o644
        assert stat.S_IMODE((data_dir / "DATA_MANIFEST.json").stat().st_mode) == 0o640
        assert stat.S_IMODE((data_dir / "SHA256SUMS").stat().st_mode) == 0o640
