"""Historical native fixtures retain their real source and never certify a new tree."""

from __future__ import annotations

import gzip
import hashlib
import json

import pytest
from _absolute_generalization_acceptance_fixture import (
    _CHAMPION_RAW_SHA256,
    ROOT,
    _champion_from_raw,
)

from uquant.contracts.strict_json import canonical_json_sha256


def _historical_raw():
    encoded = gzip.decompress((ROOT / "tests/fixtures/absolute_champion_runtime_raw.json.gz").read_bytes())
    assert hashlib.sha256(encoded).hexdigest() == _CHAMPION_RAW_SHA256
    return json.loads(encoded)


def test_native_fixture_rejects_a_different_source_without_relabelling():
    raw = _historical_raw()
    before = canonical_json_sha256(raw)
    with pytest.raises(ValueError, match="source differs from its explicit test binding"):
        _champion_from_raw(raw, expected_source="0" * 64)
    assert canonical_json_sha256(raw) == before


def test_native_fixture_derives_claims_under_its_explicit_historical_binding():
    raw = _historical_raw()
    before = canonical_json_sha256(raw)
    native_source = raw["final_account"]["code_hash"]
    derived = _champion_from_raw(raw, expected_source=native_source)
    grant = derived["strategic_grant_acceptance"]["baseline"]
    ownership = derived["strategic_ownership_acceptance"]
    assert grant["acceptance_basis"]["production_source_sha256"] == native_source
    assert ownership["production_source_identity"] == native_source
    assert ownership["champion"]["final_account"]["code_hash"] == native_source
    assert derived["metrics"]["final_equity"] == raw["equity_curve"][-1]["equity"]
    assert ownership["report_13"]["account_orders"] == derived["metrics"]["account_orders"]
    assert derived["report_13"]["final_equity"] == derived["metrics"]["final_equity"]
    assert canonical_json_sha256(raw) == before
