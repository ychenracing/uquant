from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st

from uquant.config import DEFAULT_CONFIG
from uquant.data import normalize_symbol
from uquant.execution import fee_components
from uquant.portfolio import effective_n
from uquant.types import Side


@given(
    weights=st.lists(
        st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=6,
    ),
    scale=st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=120, deadline=None)
def test_effective_diversification_is_scale_invariant_and_bounded(
    weights: list[float], scale: float
) -> None:
    original = {str(index): value for index, value in enumerate(weights)}
    scaled = {symbol: value * scale for symbol, value in original.items()}

    observed = effective_n(original)
    assert 1.0 - 1e-12 <= observed <= len(weights) + 1e-12
    assert math.isclose(effective_n(scaled), observed, rel_tol=1e-10, abs_tol=1e-10)


@given(
    first=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    increment=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=120, deadline=None)
def test_fees_are_nonnegative_monotone_and_side_specific(first: float, increment: float) -> None:
    second = first + increment
    buy_first = fee_components(Side.BUY.value, first, DEFAULT_CONFIG)
    buy_second = fee_components(Side.BUY.value, second, DEFAULT_CONFIG)
    sell_second = fee_components(Side.SELL.value, second, DEFAULT_CONFIG)

    assert all(value >= 0.0 for value in (*buy_first, *buy_second, *sell_second))
    assert all(later + 1e-12 >= earlier for earlier, later in zip(buy_first, buy_second, strict=True))
    assert buy_second[1] == 0.0
    assert math.isclose(
        sell_second[1],
        second * DEFAULT_CONFIG.stamp_duty,
        rel_tol=1e-10,
        abs_tol=1e-10,
    )


@given(code=st.integers(min_value=0, max_value=999_999))
@settings(max_examples=120)
def test_symbol_normalization_is_idempotent(code: int) -> None:
    digits = f"{code:06d}"
    normalized = normalize_symbol(digits)
    assert normalize_symbol(normalized) == normalized
    assert normalized[2:] == digits
