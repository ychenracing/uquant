from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from test_attribution_identity import (
    _native_multilot_partial_sell_account,
)

from uquant import types as domain
from uquant.account import load_account
from uquant.engine import ProductionEngine
from uquant.validation.universe import REQUIRED_AI_UNIVERSE_SHA256


@pytest.mark.parametrize(
    ("section", "field", "value", "match"),
    [
        ("order_ledger", "origin_subsystem", "UNREGISTERED", "origin_subsystem"),
        ("order_ledger", "mechanism", "UNREGISTERED", "mechanism"),
        ("order_ledger", "event_id", "uuid-like", "event_id"),
        ("fills", "industry_manifest_sha256", "0" * 64, "industry manifest"),
    ],
)
def test_native_schema_rejects_unknown_or_malformed_identity(
    tmp_path,
    section: str,
    field: str,
    value: str,
    match: str,
) -> None:
    payload = _native_multilot_partial_sell_account().to_dict()
    payload[section][0][field] = value
    malformed = tmp_path / f"malformed-{section}-{field}.json"
    malformed.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=match):
        load_account(malformed)

def test_repeated_production_decisions_include_byte_identical_causal_metadata(
    data_dir,
) -> None:
    symbols = ["sz300308", "sz300502", "sz300394", "sh688008", "sh603986"]
    engine = ProductionEngine(data_dir)
    initial = domain.AccountState.empty(2_000_000.0)
    _, initial = engine.deterministic_decision(
        symbols=symbols, as_of="2023-01-03", account=initial,
    )
    assert not initial.pending_orders

    first, first_state = engine.deterministic_decision(
        symbols=symbols,
        as_of="2023-01-04",
        account=initial,
    )
    second, second_state = engine.deterministic_decision(
        symbols=list(reversed(symbols)),
        as_of="2023-01-04",
        account=initial,
    )

    canonical = lambda value: json.dumps(  # noqa: E731 - immutable test unit
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    assert canonical(asdict(first)) == canonical(asdict(second))
    assert canonical(first_state.to_dict()) == canonical(second_state.to_dict())
    assert first.targets
    for target in first.targets:
        assert target.event_id.startswith("evt_")
        assert domain.OriginSubsystem(target.origin_subsystem)
        assert domain.AttributionMechanism(target.mechanism)
        assert target.origin_lifecycle in {item.value for item in domain.Lifecycle}
        assert target.industry_at_entry != ""
        assert target.industry_manifest_sha256 == REQUIRED_AI_UNIVERSE_SHA256
    by_event = {target.event_id: target for target in first.targets}
    assert len(by_event) == len(first.targets)
    for order in first.pending_orders:
        target = by_event[order.event_id]
        assert order.origin_subsystem == target.origin_subsystem
        assert order.mechanism == target.mechanism
        assert order.origin_lifecycle == target.origin_lifecycle
        assert order.replaces_symbol == target.replaces_symbol
        assert order.industry_at_entry == target.industry_at_entry
