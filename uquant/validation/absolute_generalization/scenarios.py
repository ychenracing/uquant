"""Deterministic canonical leave-one-out scenarios."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date

from .contract import (
    ABSOLUTE_GENERALIZATION_CONTRACT_SHA256,
    AbsoluteGeneralizationContract,
    load_absolute_generalization_contract,
)

_EXPECTED_SHARDS = (
    ("loo-a", ("sh600487", "sh688019", "sh688120", "sh688300", "sz002281", "sz300394")),
    ("loo-b", ("sh601869", "sh688037", "sh688146", "sh688347", "sz002371", "sz300502")),
    ("loo-c", ("sh603688", "sh688041", "sh688200", "sh688361", "sz002409", "sz300604")),
    ("loo-d", ("sh603986", "sh688072", "sh688233", "sh688498", "sz300054", "sz300666")),
    ("loo-e", ("sh688008", "sh688082", "sh688256", "sh688766", "sz300223")),
    ("loo-f", ("sh688012", "sh688110", "sh688268", "sz000636", "sz300308")),
)
_EXPECTED_UNIVERSE = tuple(
    sorted(symbol for _, symbols in _EXPECTED_SHARDS for symbol in symbols)
)
_EXPECTED_CRITICAL = ("sz300308", "sz300502", "sz300394")
_EXPECTED_WITNESSES = (
    "sh603688",
    "sh688008",
    "sh688082",
    "sz002409",
    "sz300666",
)
_EXPECTED_WINDOW = (date(2023, 1, 3), date(2026, 8, 5))


@dataclass(frozen=True, slots=True)
class AbsoluteGeneralizationScenario:
    """One immutable full-removal scenario from the reviewed contract."""

    cell_id: str
    removed_symbol: str
    window_start: date
    window_end: date
    shard: str
    is_critical: bool
    is_witness: bool
    contract_sha256: str


def _has_exact_runtime_shape(value: object, trusted: object) -> bool:
    if type(value) is not type(trusted):
        return False
    if isinstance(trusted, tuple):
        if not isinstance(value, tuple) or len(value) != len(trusted):
            return False
        return all(
            _has_exact_runtime_shape(item, trusted_item)
            for item, trusted_item in zip(value, trusted, strict=True)
        )
    if is_dataclass(trusted) and not isinstance(trusted, type):
        return all(
            _has_exact_runtime_shape(
                getattr(value, field.name), getattr(trusted, field.name)
            )
            for field in fields(trusted)
        )
    return True


def build_leave_one_out_scenarios(
    contract: AbsoluteGeneralizationContract,
) -> tuple[AbsoluteGeneralizationScenario, ...]:
    """Build all and only the 34 statically assigned full removals."""

    if type(contract) is not AbsoluteGeneralizationContract:
        raise ValueError(
            "absolute generalization scenarios require the exact validated contract type"
        )
    trusted = load_absolute_generalization_contract()
    if not _has_exact_runtime_shape(contract, trusted):
        raise ValueError(
            "absolute generalization scenarios require the complete validated contract shape"
        )
    if contract != trusted:
        raise ValueError(
            "absolute generalization scenarios require the complete validated contract"
        )
    universe = contract.canonical_universe
    assignments = {
        symbol: shard
        for shard, symbols in contract.shards
        for symbol in symbols
    }
    if (
        len(universe) != 34
        or len(assignments) != 34
        or set(assignments) != set(universe)
        or contract.canonical_sha256 != ABSOLUTE_GENERALIZATION_CONTRACT_SHA256
    ):
        raise ValueError("absolute generalization scenarios require exact 34/34 canonical coverage")
    if (
        universe != _EXPECTED_UNIVERSE
        or contract.shards != _EXPECTED_SHARDS
        or contract.critical_removals != _EXPECTED_CRITICAL
        or contract.required_witnesses != _EXPECTED_WITNESSES
        or (contract.window_start, contract.window_end) != _EXPECTED_WINDOW
    ):
        raise ValueError(
            "absolute generalization scenarios require frozen scenario semantics"
        )
    critical = frozenset(contract.critical_removals)
    witnesses = frozenset(contract.required_witnesses)
    scenarios = tuple(
        AbsoluteGeneralizationScenario(
            cell_id=f"remove-{symbol}",
            removed_symbol=symbol,
            window_start=contract.window_start,
            window_end=contract.window_end,
            shard=assignments[symbol],
            is_critical=symbol in critical,
            is_witness=symbol in witnesses,
            contract_sha256=contract.canonical_sha256,
        )
        for symbol in universe
    )
    if len({scenario.cell_id for scenario in scenarios}) != 34:
        raise ValueError("absolute generalization scenarios require exact 34/34 canonical coverage")
    return scenarios


__all__ = ("AbsoluteGeneralizationScenario", "build_leave_one_out_scenarios")
