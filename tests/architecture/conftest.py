from __future__ import annotations

from collections.abc import Iterator

import pytest

from ._analysis import INVENTORY_PATH, PUBLIC_API_PATH, architecture_snapshot, canonical_sha256, load_json


@pytest.fixture(scope="session")
def public_api_contract() -> dict[str, object]:
    assert PUBLIC_API_PATH.is_file(), f"missing current public API contract: {PUBLIC_API_PATH}"
    document = load_json(PUBLIC_API_PATH)
    # Keep the large sealed snapshot intact; bind the three reviewed PR73 API
    # changes through a small, independently sealed evolution record.
    delta = load_json(PUBLIC_API_PATH.with_name("public_api_pr73_delta.json"))
    contract = document["contract"]
    assert isinstance(contract, dict)
    assert canonical_sha256(contract) == document["contract_sha256"] == delta["base_contract_sha256"]
    modules = contract["modules"]
    assert isinstance(modules, dict)
    changes = delta["modules"]
    assert isinstance(changes, dict)
    assert set(changes) == {
        "uquant.application.market_observations", "uquant.portfolio.leaders.cycle",
        "uquant.portfolio.strategic.qualification_candidates",
    }
    modules.update(changes)
    assert canonical_sha256(contract) == delta["contract_sha256"]
    document["contract_sha256"] = delta["contract_sha256"]
    return document


@pytest.fixture(scope="session")
def baseline_inventory() -> dict[str, object]:
    assert INVENTORY_PATH.is_file(), f"missing architecture inventory: {INVENTORY_PATH}"
    return load_json(INVENTORY_PATH)


@pytest.fixture(scope="session")
def current_architecture() -> dict[str, object]:
    return architecture_snapshot()


@pytest.fixture(scope="session", autouse=True)
def _clear_architecture_runtime_caches() -> Iterator[None]:
    yield
    from uquant import engine as engine_module

    engine_module._SHARED_RISK_TIMELINE_CACHE.clear()
