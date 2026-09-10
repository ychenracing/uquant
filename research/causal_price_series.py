"""Forward-linked research prices; raw execution fields remain distinct.

These prices remove the issuer's reference-price discontinuity on an ex-date.
They are signal inputs, not a dividend/tax/share-lot cash-account simulator.
"""
from datetime import date
from decimal import Decimal
from typing import Any

PROTECTED_FROM = date(2026, 8, 6)
PRICE_FIELDS = ('open', 'high', 'low', 'close')


def _day(value: str) -> date:
    point = date.fromisoformat(value)
    if point >= PROTECTED_FROM:
        raise ValueError('protected date in research price input')
    return point


def _number(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('nonfinite source value')
    return result


def linked_prices(rows: list[dict[str, Any]], events: list[dict[str, Any]], *, as_of: str) -> list[dict[str, Any]]:
    """Link only already-effective events, without changing prior output rows."""
    bound = _day(as_of)
    days = [_day(row['date']) for row in rows]
    if days != sorted(set(days)):
        raise ValueError('source dates must be unique and increasing')
    visible = [row for row, point in zip(rows, days, strict=True) if point <= bound]
    visible_days = {row['date'] for row in visible}
    active = {}
    for event in events:
        ex_date = _day(event['ex_date'])
        disclosed = _day(event['disclosed'])
        if disclosed >= ex_date:
            raise ValueError('action must be disclosed before ex-date')
        if ex_date > bound:
            continue
        if event['ex_date'] in active:
            raise ValueError('duplicate action ex-date')
        if event['ex_date'] not in visible_days:
            raise ValueError('action ex-date requires an observed session')
        active[event['ex_date']] = event
    scale = Decimal(1)
    previous_close: Decimal | None = None
    output: list[dict[str, Any]] = []
    for row in visible:
        values = {name: _number(row[name]) for name in PRICE_FIELDS}
        if min(values.values()) <= 0:
            raise ValueError('source prices must be positive')
        if values['high'] < max(values.values()) or values['low'] > min(values.values()):
            raise ValueError('invalid source OHLC geometry')
        action = active.get(row['date'])
        if action is not None:
            if previous_close is None:
                raise ValueError('action requires previous observed close')
            if 'ratio_denominator' in action:
                denominator = _number(action['ratio_denominator'])
                if denominator <= 0:
                    raise ValueError('action denominator must be positive')
                cash = _number(action['cash_ratio_numerator']) / denominator
                shares = _number(action['share_ratio_numerator']) / denominator
            else:
                cash = _number(action['cash_adjustment_per_share'])
                shares = _number(action['share_change_ratio'])
            if cash < 0 or shares < 0:
                raise ValueError('negative action coefficient requires separate review')
            reference = (previous_close - cash) / (1 + shares)
            if reference <= 0:
                raise ValueError('action reference price must be positive')
            scale *= previous_close / reference
        linked = {**row, 'adjustment_scale': float(scale)}
        linked.update({f'signal_{name}': float(value * scale) for name, value in values.items()})
        output.append(linked)
        previous_close = values['close']
    return output
