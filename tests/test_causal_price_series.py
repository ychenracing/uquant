"""Research price links must not revise the past or impersonate execution prices."""
from copy import deepcopy

import pytest

from uquant.market.price_series import linked_prices


def bar(day, price):
    return {'date': day, 'open': price, 'high': price, 'low': price,
            'close': price, 'volume': 1000.0, 'amount': 1000.0 * price}


def event(day, cash='0', split='0', disclosed='2021-01-01'):
    return {'ex_date': day, 'disclosed': disclosed,
            'cash_adjustment_per_share': cash, 'share_change_ratio': split}


def test_split_and_dividend_link_preserves_raw_execution_fields():
    rows = [bar('2021-01-04', 100), bar('2021-01-05', 49)]
    original = deepcopy(rows)
    linked = linked_prices(rows, [event('2021-01-05', '2', '1')], as_of='2021-01-05')
    assert linked[0]['signal_close'] == 100
    assert linked[1]['signal_close'] == pytest.approx(100)
    assert linked[1]['close'] == 49
    assert linked[1]['volume'] == 1000
    assert linked[1]['amount'] == 49000
    assert linked[1]['share_scale'] == 2
    assert linked[1]['reference_close'] == 49
    assert rows == original


def test_cash_adjustment_does_not_change_share_volume_units():
    linked = linked_prices([bar('2021-01-04', 100), bar('2021-01-05', 99)],
                           [event('2021-01-05', '1')], as_of='2021-01-05')
    assert linked[1]['share_scale'] == 1
    assert linked[1]['reference_close'] == 99


def test_appended_actions_and_market_rows_cannot_change_visible_prefix():
    rows = [bar('2021-01-04', 100), bar('2021-01-05', 101)]
    before = linked_prices(rows, [], as_of='2021-01-05')
    extended = [*rows, bar('2021-01-06', 50)]
    actions = [event('2021-01-06', '1', '1', disclosed='2021-01-05')]
    assert linked_prices(extended, actions, as_of='2021-01-05') == before
    assert linked_prices(extended, actions, as_of='2021-01-06')[:2] == before


@pytest.mark.parametrize('events,match', [
    ([event('2021-01-05', disclosed='2021-01-05')], 'disclosed before'),
    ([event('2021-01-05'), event('2021-01-05')], 'duplicate'),
    ([event('2021-01-05', '100')], 'reference price'),
    ([event('2021-01-03')], 'observed session'),
])
def test_invalid_action_evidence_is_rejected(events, match):
    with pytest.raises(ValueError, match=match):
        linked_prices([bar('2021-01-04', 100), bar('2021-01-05', 99)], events,
                      as_of='2021-01-05')


def test_protected_date_is_rejected_before_price_use():
    with pytest.raises(ValueError, match='protected'):
        linked_prices([bar('2026-08-06', 1)], [], as_of='2026-08-05')
