"""The reference owner resolves the same dated taxonomy as its former caller."""
import pandas as pd

from uquant.config import DEFAULT_CONFIG
from uquant.industry import decision_industries
from uquant.reference import build_reference_context


def test_default_reference_industries_equal_the_previous_explicit_input():
    dates = pd.bdate_range('2025-01-02', periods=80)
    close = pd.Series(range(100, 180), index=dates, dtype=float)
    frame = pd.DataFrame({'close': close, 'ma20': close*.98, 'ma60': close*.95,
                          'ret20': .2, 'ret60': .4, 'ret1': .01}, index=dates)
    panel = {s: frame for s in ['sz300308', 'sh688012', 'sh688008', 'sh688072']}
    explicit = build_reference_context(date=dates[-1], panel=panel, cfg=DEFAULT_CONFIG,
                                       industries=decision_industries(str(dates[-1].date())))
    default = build_reference_context(date=dates[-1], panel=panel, cfg=DEFAULT_CONFIG)
    assert default == explicit
    empty = build_reference_context(date=dates[-1], panel=panel, cfg=DEFAULT_CONFIG, industries={})
    assert empty.visible_groups != default.visible_groups
