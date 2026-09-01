from __future__ import annotations

import importlib
import math
import pickle
from collections.abc import Iterator, Sequence
from typing import Never

import pytest


def _linear_quantile():
    module = importlib.import_module("uquant.validation.statistics")
    return module.linear_quantile


class _ValuesWereRead(RuntimeError):
    pass


class _ExplodingValues(Sequence[float]):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __len__(self) -> Never:
        self.calls.append("len")
        raise _ValuesWereRead

    def __getitem__(self, index: int | slice) -> Never:
        self.calls.append(f"getitem:{index!r}")
        raise _ValuesWereRead

    def __iter__(self) -> Iterator[float]:
        self.calls.append("iter")
        raise _ValuesWereRead


@pytest.mark.parametrize(
    ("values", "probability", "expected"),
    (
        ([7.0], 0.10, 7.0),
        ([7.0], 0.90, 7.0),
        ([0.0, 10.0], 0.10, 1.0),
        ([0.0, 10.0], 0.90, 9.0),
        ([1.0, 2.0, 10.0], 0.25, 1.5),
        ([1.0, 2.0, 3.0, 4.0], 0.10, 1.3),
        ([1.0, 2.0, 3.0, 4.0], 0.90, 3.7),
        ([4.0, 1.0, 3.0, 2.0], 0, 1.0),
        ([4.0, 1.0, 3.0, 2.0], 1, 4.0),
    ),
)
def test_linear_quantile_uses_n_minus_one_interpolation(
    values: list[float],
    probability: float,
    expected: float,
) -> None:
    """Catch nearest-rank, midpoint, lower, higher, and boundary mistakes."""

    assert _linear_quantile()(values, probability) == pytest.approx(expected)


def test_linear_quantile_is_independent_of_input_order() -> None:
    """Catch interpolation against caller order instead of sorted values."""

    quantile = _linear_quantile()

    assert quantile([8.0, 1.0, 5.0, 3.0], 0.25) == pytest.approx(2.5)
    assert quantile([1.0, 3.0, 5.0, 8.0], 0.25) == pytest.approx(2.5)


@pytest.mark.parametrize(
    "values",
    (
        [],
        [True],
        [False, 1.0],
        ["1.0"],
        [math.nan],
        [math.inf],
        [-math.inf],
    ),
)
def test_linear_quantile_rejects_non_finite_or_non_numeric_values(
    values: list[object],
) -> None:
    """Catch sorting or coercing malformed metric cells into a percentile."""

    with pytest.raises(ValueError):
        _linear_quantile()(values, 0.5)


@pytest.mark.parametrize(
    "probability",
    (-0.01, 1.01, True, False, "0.5", math.nan, math.inf, -math.inf),
)
def test_linear_quantile_rejects_invalid_probability(probability: object) -> None:
    """Catch out-of-domain or non-finite percentile selectors."""

    with pytest.raises(ValueError):
        _linear_quantile()([1.0, 2.0], probability)


def test_linear_quantile_rejects_non_numeric_value_before_sorting() -> None:
    """Catch leaking a heterogeneous-sort TypeError for malformed cells."""

    with pytest.raises(ValueError, match="quantile value must be numeric"):
        _linear_quantile()([1.0, "bad"], 0.5)


def test_linear_quantile_rejects_probability_before_reading_values() -> None:
    """Catch consuming an expensive or failing sequence before selector validation."""

    values = _ExplodingValues()

    with pytest.raises(ValueError, match="quantile probability must be in"):
        _linear_quantile()(values, -0.01)

    assert values.calls == []


def test_generalization_facade_preserves_central_quantile_owner_metadata() -> None:
    """Catch a compatibility alias rewriting the central owner's pickle identity."""

    statistics = importlib.import_module("uquant.validation.statistics")
    linear_quantile = statistics.linear_quantile
    assert linear_quantile.__module__ == "uquant.validation.statistics"

    importlib.import_module("uquant.validation.generalization")

    assert linear_quantile.__module__ == "uquant.validation.statistics"
    restored = pickle.loads(pickle.dumps(linear_quantile))
    assert restored is linear_quantile
    assert restored.__module__ == "uquant.validation.statistics"


def test_existing_generalization_paths_share_the_linear_quantile_owner() -> None:
    """Catch copied interpolation owners reappearing in legacy validation paths."""

    linear_quantile = _linear_quantile()
    matrix_evidence = importlib.import_module(
        "uquant.validation.generalization_matrix_evidence"
    )
    generalization_metrics = importlib.import_module(
        "uquant.validation.generalization.metrics"
    )
    cell_policy = importlib.import_module(
        "uquant.validation.generalization_policy.cell_policy"
    )

    for module, compatibility_name in (
        (generalization_metrics, "quantile"),
        (matrix_evidence, "matrix_quantile"),
        (cell_policy, "policy_quantile"),
    ):
        compatibility = getattr(module, compatibility_name)
        assert module._linear_quantile is linear_quantile
        assert compatibility is not linear_quantile
        assert compatibility.__code__.co_names == ("_linear_quantile",)
        assert compatibility([0.0, 10.0], 0.90) == pytest.approx(9.0)
        with pytest.raises(ValueError):
            compatibility([True, 10.0], 0.90)
