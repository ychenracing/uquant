import pytest

from research.capital_holding_attribution import shapley, value


def test_attribution_conserves_value_and_reverses_symmetrically():
    before, after = (100, 10, .5), (80, 9, .25)
    forward = shapley(before, after, 15, 12)
    reverse = shapley(after, before, 15, 12)
    assert sum(forward.values()) == pytest.approx(
        value(after, 15, 12) - value(before, 15, 12)
    )
    for key in forward:
        assert forward[key] == pytest.approx(-reverse[key])


def test_only_purchase_price_changes_has_no_quantity_or_sale_contribution():
    result = shapley((100, 10, .5), (100, 9, .5), 15, 12)
    assert result == pytest.approx(
        {"quantity": 0, "purchase_price": 100, "sold_fraction": 0}
    )
