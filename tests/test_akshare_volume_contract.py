"""Source units must not create artificial volume shocks at feed boundaries."""

import pandas as pd

from uquant.config import SystemConfig
from uquant.data import DataStore
from uquant.features import compute_features


def source_frame(start: str, periods: int) -> pd.DataFrame:
    return pd.DataFrame({
        "日期": pd.bdate_range(start, periods=periods),
        "开盘": 10.0, "最高": 10.5, "最低": 9.5, "收盘": 10.0,
        "成交量": 100.0, "成交额": 100000.0,
    })


def test_stock_hands_become_shares_without_altering_cash_or_source():
    source = source_frame("2021-01-04", 2)
    original = source.copy(deep=True)
    result = DataStore._from_akshare_stock(source, "sh600000")
    assert result["volume"].tolist() == [10000.0, 10000.0]
    assert (result["amount"] / result["volume"]).tolist() == [10.0, 10.0]
    assert result["close"].tolist() == [10.0, 10.0]
    pd.testing.assert_frame_equal(source, original)


def test_equal_activity_across_feeds_does_not_create_volume_expansion():
    source = source_frame("2021-01-04", 40)
    existing = source.iloc[:20].rename(columns={
        "日期": "date", "开盘": "open", "最高": "high", "最低": "low",
        "收盘": "close", "成交量": "volume", "成交额": "amount",
    }).copy()
    existing["volume"] = 10000.0
    prior = DataStore._validate(existing, "sh600000")
    fresh = DataStore._from_akshare_stock(source.iloc[20:], "sh600000")
    features = compute_features(pd.concat([prior, fresh]), SystemConfig())
    assert features["volume_expansion"].iloc[19:].eq(1.0).all()
