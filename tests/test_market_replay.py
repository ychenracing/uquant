from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

from research.candidate_runner import CandidateRunner
from uquant import engine as engine_module
from uquant.data import DataStore
from uquant.market import ReplayCache

ROOT = Path(__file__).parents[1]


def _trace_at(data_dir: Path, symbols: tuple[str, ...], *, universe: str = "parallel"):
    return CandidateRunner(data_dir).trace_cell(
        symbols=symbols,
        start="2026-07-01",
        end="2026-07-03",
        universe=universe,
        scenario="task4",
    )


def _trace(symbols: tuple[str, ...]):
    return _trace_at(ROOT / "data" / "frozen", symbols)


def test_parallel_explicit_replay_universes_match_isolated_runs_exactly() -> None:
    left_symbols = ("sz300308", "sz300502", "sz300394")
    right_symbols = ("sh688008", "sh603986")
    expected_left = _trace(left_symbols)
    expected_right = _trace(right_symbols)
    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(_trace, left_symbols)
        right = executor.submit(_trace, right_symbols)
    assert left.result() == expected_left
    assert right.result() == expected_right


def test_parallel_distinct_causal_data_identities_match_isolated_runs(
    tmp_path: Path,
) -> None:
    left_data = tmp_path / "left"
    right_data = tmp_path / "right"
    shutil.copytree(ROOT / "data" / "frozen", left_data)
    shutil.copytree(ROOT / "data" / "frozen", right_data)
    changed_path = right_data / "sz300502.csv"
    original = changed_path.read_text(encoding="utf-8")
    changed = original.replace(
        "2026-07-01,608.0,618.87,566.56,575.56,476237.0,28256949371.0",
        "2026-07-01,608.0,618.87,566.56,575.56,476238.0,28256949371.0",
        1,
    )
    assert changed != original
    changed_path.write_text(changed, encoding="utf-8")
    symbols = ("sz300308", "sz300502")
    left_universe = CandidateRunner(left_data).replay_universe(symbols)
    right_universe = CandidateRunner(right_data).replay_universe(symbols)
    assert left_universe.identity_sha256 == right_universe.identity_sha256
    assert DataStore(left_data).manifest(left_universe.all_symbols, as_of="2026-07-03").digest != (
        DataStore(right_data).manifest(right_universe.all_symbols, as_of="2026-07-03").digest
    )

    expected_left = _trace_at(left_data, symbols, universe="left")
    expected_right = _trace_at(right_data, symbols, universe="right")
    barrier = Barrier(2)

    def interleaved(data_dir: Path, universe: str):
        barrier.wait()
        return _trace_at(data_dir, symbols, universe=universe)

    with ThreadPoolExecutor(max_workers=2) as executor:
        left = executor.submit(interleaved, left_data, "left")
        right = executor.submit(interleaved, right_data, "right")
    assert left.result() == expected_left
    assert right.result() == expected_right


def test_replay_cache_has_finite_deterministic_lru_and_owned_cleanup() -> None:
    cache: ReplayCache[str, str] = ReplayCache(capacity=2)
    builds: list[str] = []

    def value(key: str) -> str:
        builds.append(key)
        return key.upper()

    assert cache.get_or_build("a", lambda: value("a")) == "A"
    assert cache.get_or_build("b", lambda: value("b")) == "B"
    assert cache.get_or_build("a", lambda: value("never")) == "A"
    assert cache.get_or_build("c", lambda: value("c")) == "C"
    assert cache.keys() == ("a", "c")
    assert cache.get("b") is None
    assert builds == ["a", "b", "c"]
    cache.clear()
    assert len(cache) == 0


def test_replay_cache_get_build_update_boundary_is_thread_safe() -> None:
    workers = 8
    barrier = Barrier(workers)
    counter_lock = Lock()
    builds = 0
    cache: ReplayCache[str, object] = ReplayCache(capacity=4)
    expected = object()

    def build() -> object:
        nonlocal builds
        with counter_lock:
            builds += 1
        return expected

    def read() -> object:
        barrier.wait()
        return cache.get_or_build("same", build)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        observed = tuple(executor.map(lambda _: read(), range(workers)))
    assert all(value is expected for value in observed)
    assert builds == 1


def test_engine_shared_timeline_cache_uses_owned_finite_replay_cache() -> None:
    cache = engine_module._SHARED_RISK_TIMELINE_CACHE
    assert isinstance(cache, ReplayCache)
    assert cache.capacity > 0
    cache.get_or_build(("fixture",), object)
    cache.clear()
    assert len(cache) == 0
