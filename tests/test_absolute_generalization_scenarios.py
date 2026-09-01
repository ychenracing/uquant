from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields, replace
from datetime import date
from pathlib import Path

import pytest

from uquant.validation.absolute_generalization import AbsoluteGeneralizationContract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "benchmarks/absolute_generalization_acceptance_contract.json"

CANONICAL_UNIVERSE = (
    "sh600487",
    "sh601869",
    "sh603688",
    "sh603986",
    "sh688008",
    "sh688012",
    "sh688019",
    "sh688037",
    "sh688041",
    "sh688072",
    "sh688082",
    "sh688110",
    "sh688120",
    "sh688146",
    "sh688200",
    "sh688233",
    "sh688256",
    "sh688268",
    "sh688300",
    "sh688347",
    "sh688361",
    "sh688498",
    "sh688766",
    "sz000636",
    "sz002281",
    "sz002371",
    "sz002409",
    "sz300054",
    "sz300223",
    "sz300308",
    "sz300394",
    "sz300502",
    "sz300604",
    "sz300666",
)

EXPECTED_SHARDS = {
    "loo-a": (
        "sh600487",
        "sh688019",
        "sh688120",
        "sh688300",
        "sz002281",
        "sz300394",
    ),
    "loo-b": (
        "sh601869",
        "sh688037",
        "sh688146",
        "sh688347",
        "sz002371",
        "sz300502",
    ),
    "loo-c": (
        "sh603688",
        "sh688041",
        "sh688200",
        "sh688361",
        "sz002409",
        "sz300604",
    ),
    "loo-d": (
        "sh603986",
        "sh688072",
        "sh688233",
        "sh688498",
        "sz300054",
        "sz300666",
    ),
    "loo-e": (
        "sh688008",
        "sh688082",
        "sh688256",
        "sh688766",
        "sz300223",
    ),
    "loo-f": (
        "sh688012",
        "sh688110",
        "sh688268",
        "sz000636",
        "sz300308",
    ),
}


def _package() -> object:
    return importlib.import_module("uquant.validation.absolute_generalization")


def _contract_and_scenarios() -> tuple[object, tuple[object, ...]]:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)
    return contract, package.build_leave_one_out_scenarios(contract)


class _AlwaysEqualContract(AbsoluteGeneralizationContract):
    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


class _AlwaysEqualContractDuck:
    def __init__(self, contract: AbsoluteGeneralizationContract) -> None:
        for field in fields(contract):
            setattr(self, field.name, getattr(contract, field.name))

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


class _AlwaysEqualNestedDuck:
    def __init__(self, nested: object, **changes: object) -> None:
        for field in fields(nested):
            setattr(
                self,
                field.name,
                changes.get(field.name, getattr(nested, field.name)),
            )

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False


def _always_equal_nested_subclass(nested: object, **changes: object) -> object:
    def _equal(self: object, other: object) -> bool:
        return True

    def _not_equal(self: object, other: object) -> bool:
        return False

    hostile_type = type(
        f"_AlwaysEqual{type(nested).__name__}",
        (type(nested),),
        {"__eq__": _equal, "__ne__": _not_equal},
    )
    return hostile_type(
        **{
            field.name: changes.get(field.name, getattr(nested, field.name))
            for field in fields(nested)
        }
    )


class _HostileScenarioFieldAccess(RuntimeError):
    pass


class _ExplodingTuple(tuple[str, ...]):
    def __new__(
        cls, values: tuple[str, ...], calls: list[str]
    ) -> _ExplodingTuple:
        instance = super().__new__(cls, values)
        instance.calls = calls
        return instance

    def __len__(self) -> int:
        self.calls.append("len")
        raise _HostileScenarioFieldAccess("hostile tuple length executed")


class _ExplodingString(str):
    def __new__(cls, value: str, calls: list[str]) -> _ExplodingString:
        instance = super().__new__(cls, value)
        instance.calls = calls
        return instance

    def __eq__(self, other: object) -> bool:
        self.calls.append("eq")
        raise _HostileScenarioFieldAccess("hostile string equality executed")

    def __ne__(self, other: object) -> bool:
        self.calls.append("ne")
        raise _HostileScenarioFieldAccess("hostile string inequality executed")

    __hash__ = str.__hash__


def test_scenarios_are_exactly_34_sorted_unique_canonical_full_removals() -> None:
    contract, scenarios = _contract_and_scenarios()

    assert len(scenarios) == 34
    assert tuple(scenario.removed_symbol for scenario in scenarios) == CANONICAL_UNIVERSE
    assert tuple(scenario.cell_id for scenario in scenarios) == tuple(
        f"remove-{symbol}" for symbol in CANONICAL_UNIVERSE
    )
    assert len({scenario.cell_id for scenario in scenarios}) == 34
    for scenario in scenarios:
        remaining = set(contract.canonical_universe) - {scenario.removed_symbol}
        assert len(remaining) == 33
        assert scenario.removed_symbol not in remaining


def test_static_shards_are_disjoint_complete_and_use_fixed_membership() -> None:
    _, scenarios = _contract_and_scenarios()
    observed = {
        shard: tuple(
            scenario.removed_symbol for scenario in scenarios if scenario.shard == shard
        )
        for shard in EXPECTED_SHARDS
    }

    assert observed == EXPECTED_SHARDS
    flattened = tuple(symbol for values in observed.values() for symbol in values)
    assert len(flattened) == 34
    assert len(set(flattened)) == 34
    assert set(flattened) == set(CANONICAL_UNIVERSE)


def test_scenario_flags_window_and_identity_are_exact_and_immutable() -> None:
    contract, scenarios = _contract_and_scenarios()
    by_symbol = {scenario.removed_symbol: scenario for scenario in scenarios}

    assert {symbol for symbol, row in by_symbol.items() if row.is_critical} == {
        "sz300308",
        "sz300502",
        "sz300394",
    }
    assert {symbol for symbol, row in by_symbol.items() if row.is_witness} == {
        "sh603688",
        "sh688008",
        "sh688082",
        "sz002409",
        "sz300666",
    }
    assert {
        (scenario.window_start.isoformat(), scenario.window_end.isoformat())
        for scenario in scenarios
    } == {("2023-01-03", "2026-08-05")}
    assert {scenario.contract_sha256 for scenario in scenarios} == {
        contract.canonical_sha256
    }
    assert {field.name for field in fields(scenarios[0])} == {
        "cell_id",
        "contract_sha256",
        "is_critical",
        "is_witness",
        "removed_symbol",
        "shard",
        "window_end",
        "window_start",
    }
    with pytest.raises(FrozenInstanceError):
        scenarios[0].removed_symbol = "sz300308"


def test_scenario_builder_is_deterministic_and_rejects_noncanonical_contracts() -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)

    assert package.build_leave_one_out_scenarios(contract) == (
        package.build_leave_one_out_scenarios(contract)
    )
    malformed = replace(contract, canonical_universe=contract.canonical_universe[:-1])
    with pytest.raises(ValueError, match="complete validated contract shape"):
        package.build_leave_one_out_scenarios(malformed)


@pytest.mark.parametrize(
    "malformed",
    (
        lambda contract: replace(
            contract,
            critical_removals=("sh600487", "sz300502", "sz300394"),
        ),
        lambda contract: replace(
            contract,
            required_witnesses=(
                "sh600487",
                "sh688008",
                "sh688082",
                "sz002409",
                "sz300666",
            ),
        ),
        lambda contract: replace(
            contract,
            shards=(
                ("loo-a", contract.shards[1][1]),
                ("loo-b", contract.shards[0][1]),
                *contract.shards[2:],
            ),
        ),
        lambda contract: replace(contract, window_start=date(2023, 1, 4)),
    ),
)
def test_scenario_builder_rejects_replaced_contract_semantics(
    malformed: Callable[
        [AbsoluteGeneralizationContract], AbsoluteGeneralizationContract
    ],
) -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)

    with pytest.raises(ValueError, match=r"complete validated contract$"):
        package.build_leave_one_out_scenarios(malformed(contract))


@pytest.mark.parametrize(
    "malformed",
    (
        lambda contract: replace(contract, baseline_can_relax_absolute_limits=True),
        lambda contract: replace(
            contract,
            candidate=replace(
                contract.candidate,
                production_source_sha256="0" * 64,
            ),
        ),
        lambda contract: replace(
            contract,
            thresholds=replace(
                contract.thresholds,
                minimum_positive_return_fraction=0.1,
            ),
        ),
        lambda contract: replace(
            contract,
            frozen_baseline=replace(
                contract.frozen_baseline,
                production_source_sha256="0" * 64,
            ),
        ),
    ),
)
def test_scenario_builder_requires_the_complete_validated_contract_instance(
    malformed: Callable[
        [AbsoluteGeneralizationContract], AbsoluteGeneralizationContract
    ],
) -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)

    with pytest.raises(ValueError, match="complete validated contract"):
        package.build_leave_one_out_scenarios(malformed(contract))


@pytest.mark.parametrize(
    "hostile",
    (
        lambda contract: _AlwaysEqualContract(
            **{
                field.name: getattr(contract, field.name)
                for field in fields(contract)
            }
        ),
        _AlwaysEqualContractDuck,
    ),
)
def test_scenario_builder_rejects_hostile_equal_contract_lookalikes(
    hostile: Callable[[AbsoluteGeneralizationContract], object],
) -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)

    with pytest.raises(ValueError, match="exact validated contract type"):
        package.build_leave_one_out_scenarios(hostile(contract))


@pytest.mark.parametrize(
    "hostile_nested",
    (
        lambda contract: replace(
            contract,
            candidate=_always_equal_nested_subclass(
                contract.candidate,
                production_source_sha256="0" * 64,
            ),
        ),
        lambda contract: replace(
            contract,
            thresholds=_AlwaysEqualNestedDuck(
                contract.thresholds,
                minimum_positive_return_fraction=0.1,
            ),
        ),
    ),
)
def test_scenario_builder_rejects_hostile_equal_nested_contract_values(
    hostile_nested: Callable[
        [AbsoluteGeneralizationContract], AbsoluteGeneralizationContract
    ],
) -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)
    malformed = hostile_nested(contract)
    assert type(malformed) is AbsoluteGeneralizationContract

    with pytest.raises(ValueError, match="complete validated contract shape"):
        package.build_leave_one_out_scenarios(malformed)


@pytest.mark.parametrize(
    "hostile_scenario_field",
    (
        lambda contract, calls: replace(
            contract,
            canonical_universe=_ExplodingTuple(
                contract.canonical_universe,
                calls,
            ),
        ),
        lambda contract, calls: replace(
            contract,
            canonical_sha256=_ExplodingString(
                contract.canonical_sha256,
                calls,
            ),
        ),
    ),
)
def test_scenario_builder_shape_checks_before_touching_hostile_scenario_fields(
    hostile_scenario_field: Callable[
        [AbsoluteGeneralizationContract, list[str]],
        AbsoluteGeneralizationContract,
    ],
) -> None:
    package = _package()
    contract = package.load_absolute_generalization_contract(CONTRACT_PATH)
    calls: list[str] = []
    malformed = hostile_scenario_field(contract, calls)
    assert type(malformed) is AbsoluteGeneralizationContract

    with pytest.raises(ValueError, match="complete validated contract shape"):
        package.build_leave_one_out_scenarios(malformed)

    assert calls == []
