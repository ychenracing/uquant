from __future__ import annotations

from collections.abc import Mapping

import pytest

from ._analysis import (
    PUBLIC_API_PATH,
    ROOT,
    canonical_sha256,
    representative_replay,
    sha256_file,
    tracked_file_inventory,
)


def test_same_named_module_and_package_never_compete_for_import_authority() -> None:
    conflicts = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "uquant").rglob("*.py")
        if path.name != "__init__.py" and path.with_suffix("").is_dir()
    )
    assert conflicts == []


def test_baseline_tracked_file_authority_is_complete_and_immutable(
    baseline_inventory: dict[str, object],
) -> None:
    baseline = baseline_inventory["baseline"]
    authority = baseline_inventory["tracked_file_authority"]
    assert isinstance(baseline, Mapping)
    assert isinstance(authority, Mapping)
    assert authority == tracked_file_inventory(ROOT, str(baseline["commit"]))
    assert authority["canonical_sha256"] == canonical_sha256(authority["entries"])


def test_baseline_source_surface_and_public_contract_have_closed_integrity_hashes(
    baseline_inventory: dict[str, object],
) -> None:
    baseline = baseline_inventory["baseline"]
    public_contract = baseline_inventory["public_api_contract"]
    assert isinstance(baseline, Mapping)
    assert isinstance(public_contract, Mapping)
    source_surface = baseline["production_source_surface"]
    assert isinstance(source_surface, Mapping)
    entries = source_surface["entries"]
    assert isinstance(entries, list)
    assert source_surface["canonical_sha256"] == canonical_sha256(entries)
    assert source_surface["tree_sha256"] == canonical_sha256(
        [(entry["path"], entry["sha256"]) for entry in entries]
    )
    assert all(
        isinstance(entry, Mapping)
        and str(entry["path"]).startswith("uquant/")
        and str(entry["path"]).endswith(".py")
        for entry in entries
    )
    assert public_contract["sha256"] == sha256_file(PUBLIC_API_PATH)


@pytest.mark.parametrize("scenario_index", range(3))
def test_three_representative_replays_match_the_frozen_baseline(
    baseline_inventory: dict[str, object], scenario_index: int
) -> None:
    scenarios = baseline_inventory["representative_replays"]
    assert isinstance(scenarios, list)
    assert len(scenarios) == 3
    expected = scenarios[scenario_index]
    assert isinstance(expected, Mapping)
    observed = representative_replay(
        name=str(expected["name"]),
        start=str(expected["requested_start"]),
        end=str(expected["requested_end"]),
        symbols=tuple(str(symbol) for symbol in expected["symbols"]),
    )
    assert observed == expected


def test_performance_baseline_records_wall_rss_and_pytest_time(
    baseline_inventory: dict[str, object],
) -> None:
    performance = baseline_inventory["performance_baseline"]
    assert isinstance(performance, Mapping)
    replay = performance["representative_replay"]
    pytest_run = performance["pytest_core_contracts"]
    assert isinstance(replay, Mapping)
    assert isinstance(pytest_run, Mapping)
    assert float(replay["wall_seconds"]) > 0.0
    assert int(replay["peak_rss_kib"]) > 0
    assert float(pytest_run["wall_seconds"]) > 0.0
    assert int(pytest_run["peak_rss_kib"]) > 0
