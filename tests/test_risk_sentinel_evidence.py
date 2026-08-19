from __future__ import annotations

from collections import OrderedDict

import pandas as pd

from uquant.risk_sentinel.evidence import build_market_evidence


def _prices(
    *,
    end: str = "2026-08-19",
    shock: float = 0.0,
    future: float | None = None,
) -> pd.DataFrame:
    index = pd.bdate_range(end=pd.Timestamp(end), periods=30)
    close = [100.0 + index_value * 0.2 for index_value in range(30)]
    if shock:
        for offset in range(5):
            close[-(offset + 1)] *= 1.0 + shock * (5 - offset) / 5
    frame = pd.DataFrame({"close": close}, index=index)
    if future is not None:
        frame.loc[pd.Timestamp(end) + pd.offsets.BDay(1), "close"] = future
    return frame


def test_subindustries_receive_equal_weight_despite_member_imbalance() -> None:
    panel = {f"large_{index}": _prices(shock=0.0) for index in range(9)}
    panel["small"] = _prices(shock=-0.10)
    industries = {symbol: "large" for symbol in panel}
    industries["small"] = "small"

    evidence = build_market_evidence(
        as_of="2026-08-19",
        broad_frame=_prices(),
        tech_frame=_prices(),
        reference_panel=panel,
        point_in_time_industries=industries,
        held_symbols=(),
    )

    by_group = {item.industry: item for item in evidence.subindustries}
    expected = (by_group["large"].fast_return + by_group["small"].fast_return) / 2
    assert evidence.metrics["equal_subindustry_fast_return"] == expected
    assert evidence.metrics["name_weighted_fast_return"] != expected


def test_one_extreme_member_cannot_dominate_its_subindustry() -> None:
    panel = {
        "a": _prices(),
        "b": _prices(),
        "extreme": _prices(shock=-0.90),
    }
    evidence = build_market_evidence(
        as_of="2026-08-19",
        broad_frame=_prices(),
        tech_frame=_prices(),
        reference_panel=panel,
        point_in_time_industries={symbol: "optical" for symbol in panel},
        held_symbols=(),
    )

    assert evidence.subindustries[0].fast_return > -0.05


def test_future_rows_and_mapping_order_do_not_change_evidence() -> None:
    first_panel = OrderedDict(
        (
            ("a", _prices(shock=-0.06, future=10000.0)),
            ("b", _prices(shock=-0.04, future=1.0)),
        )
    )
    second_panel = OrderedDict(reversed(list(first_panel.items())))
    industries = {"a": "optical", "b": "storage"}

    first = build_market_evidence(
        as_of="2026-08-19",
        broad_frame=_prices(shock=-0.04, future=10000.0),
        tech_frame=_prices(shock=-0.05, future=1.0),
        reference_panel=first_panel,
        point_in_time_industries=industries,
        held_symbols=("a",),
    )
    second = build_market_evidence(
        as_of="2026-08-19",
        broad_frame=_prices(shock=-0.04),
        tech_frame=_prices(shock=-0.05),
        reference_panel=second_panel,
        point_in_time_industries=dict(reversed(list(industries.items()))),
        held_symbols=("a",),
    )

    assert first.to_dict() == second.to_dict()
    assert first.metrics["latest_visible_ordinal"] == float(
        pd.Timestamp("2026-08-19").toordinal()
    )


def test_correlated_breadth_indicators_count_as_one_family() -> None:
    panel = {
        "a": _prices(shock=-0.08),
        "b": _prices(shock=-0.07),
        "c": _prices(shock=-0.09),
        "d": _prices(shock=-0.08),
    }
    evidence = build_market_evidence(
        as_of="2026-08-19",
        broad_frame=_prices(shock=-0.05),
        tech_frame=_prices(shock=-0.06),
        reference_panel=panel,
        point_in_time_industries={
            "a": "optical",
            "b": "optical",
            "c": "storage",
            "d": "storage",
        },
        held_symbols=("a", "c"),
    )

    assert evidence.family_votes["breadth_structure"] is True
    assert evidence.family_votes["market_velocity"] is True
    assert evidence.families.count("breadth_structure") == 1
    assert evidence.first_evidence_date is not None
