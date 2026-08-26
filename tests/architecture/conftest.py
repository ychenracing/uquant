from __future__ import annotations

from collections.abc import Iterator

import pytest

from ._analysis import INVENTORY_PATH, PUBLIC_API_PATH, architecture_snapshot, load_json


@pytest.fixture(scope="session")
def public_api_contract() -> dict[str, object]:
    assert PUBLIC_API_PATH.is_file(), f"missing current public API contract: {PUBLIC_API_PATH}"
    return load_json(PUBLIC_API_PATH)


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
