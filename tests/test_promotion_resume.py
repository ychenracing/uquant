"""Resume preserves raw failures and never reduces the complete aggregation."""

import json
from pathlib import Path

import pytest
from test_validation import _install_runtime, _PassingEngine, _valid_spec, _write_spec

from uquant.validation import promotion
from uquant.validation._promotion_cache import replay_unit


def test_interruption_resumes_all_units_and_retains_economic_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class InterruptedEngine(_PassingEngine):
        count = 0
        interrupt = True

        def backtest(self, **kwargs):
            type(self).count += 1
            if type(self).interrupt and type(self).count == 3:
                raise RuntimeError("interrupted")
            raw = super().backtest(**kwargs)
            raw["account_orders"] = 99
            return raw

    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)
    _install_runtime(monkeypatch, spec, engine=InterruptedEngine)
    cache = tmp_path / "units"
    kwargs = {"data_dir": "fixture", "baseline": baseline, "cache_dir": cache}
    with pytest.raises(RuntimeError, match="interrupted"):
        promotion.run_promotion(**kwargs)
    assert len(list(cache.glob("*.json"))) == 2
    InterruptedEngine.interrupt = False
    report = promotion.run_promotion(**kwargs)
    assert InterruptedEngine.count == 46  # 45 genuine units plus the interrupted call.
    assert report["summary"]["official_cells"] == 30
    assert report["summary"]["protected_cells"] == 15
    assert not report["passed"]
    assert report["failures"]
    again = promotion.run_promotion(**kwargs)
    assert InterruptedEngine.count == 46
    assert again["failures"] == report["failures"]
    assert again["cells"] == report["cells"]
    assert all(json.loads(p.read_bytes())["payload"]["raw"]["account_orders"] == 99
               for p in cache.glob("*.json"))


@pytest.mark.parametrize("field", ["source", "config", "data", "runtime", "runner", "baseline", "roles", "window"])
def test_unit_rejects_changed_identity(tmp_path: Path, field: str) -> None:
    identity = {field: "original"}
    replay_unit(tmp_path, name="A/window", identity=identity, replay=lambda: {"orders": []})
    with pytest.raises(RuntimeError, match="invalid promotion cache"):
        replay_unit(tmp_path, name="A/window", identity={field: "changed"}, replay=lambda: pytest.fail("replayed"))


@pytest.mark.parametrize("damage", ["raw", "truncated", "duplicate"])
def test_unit_rejects_corruption_without_overwrite(tmp_path: Path, damage: str) -> None:
    replay_unit(tmp_path, name="A/window", identity={}, replay=lambda: {"orders": [1]})
    path = next(tmp_path.glob("*.json"))
    content = path.read_text()
    if damage == "raw":
        content = content.replace('"orders":[1]', '"orders":[2]')
    elif damage == "truncated":
        content = content[:20]
    else:
        content = content.replace('"orders":[1]', '"orders":[1],"orders":[1]')
    path.write_text(content)
    with pytest.raises(RuntimeError, match="invalid promotion cache"):
        replay_unit(tmp_path, name="A/window", identity={}, replay=lambda: pytest.fail("replayed"))
    assert path.read_text() == content


def test_drift_does_not_publish_unit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    spec = _valid_spec()
    baseline = _write_spec(tmp_path / "baseline.json", spec)

    class DriftEngine(_PassingEngine):
        def backtest(self, **kwargs):
            raw = super().backtest(**kwargs)
            baseline.write_text("{}")
            return raw

    _install_runtime(monkeypatch, spec, engine=DriftEngine)
    with pytest.raises(RuntimeError, match="changed during replay"):
        promotion.run_promotion(data_dir="fixture", baseline=baseline, cache_dir=tmp_path / "units")
    assert not list((tmp_path / "units").glob("*.json"))


@pytest.mark.parametrize("target", ["directory", "unit"])
def test_cache_rejects_symlinks(tmp_path: Path, target: str) -> None:
    cache = tmp_path / "cache"
    replay_unit(cache, name="A/window", identity={}, replay=lambda: {"orders": []})
    if target == "directory":
        link = tmp_path / "linked"
        link.symlink_to(cache, target_is_directory=True)
        cache = link
    else:
        path = next(cache.glob("*.json"))
        saved = tmp_path / "saved.json"
        path.rename(saved)
        path.symlink_to(saved)
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        replay_unit(cache, name="A/window", identity={}, replay=lambda: pytest.fail("replayed"))
