"""Protect date boundaries and source semantics in research price acquisition."""

import json

import pytest

from research.historical_prices import parse_prices, request_url


def payload(rows=None, code="cn_000555"):
    return json.dumps([{"status": 0, "code": code, "hq": rows or [
        ["2021-01-04", "15.57", "15.69", "0.19", "1.23%", "15.55",
         "15.84", "114266", "17910.49", "1.18%"],
    ], "stat": ["累计:"]}], ensure_ascii=False).encode("gb18030")


def test_encoding_units_and_no_invented_status():
    rows = parse_prices(payload(), "sz000555", "2021-01-01", "2021-12-31")
    assert rows[0]["volume"] == 11426600
    assert rows[0]["amount"] == 179104900
    assert rows[0]["close"] == 15.69
    assert "tradable" not in rows[0]
    assert "adjustment_factor" not in rows[0]


@pytest.mark.parametrize("start,end", [
    ("2026-08-06", "2026-08-06"), ("2021-01-01", "2026-09-01"),
    ("2022-01-01", "2021-01-01"),
])
def test_invalid_or_protected_request_never_builds_url(start, end):
    with pytest.raises(ValueError):
        request_url("sz000555", start, end)


def test_response_symbol_and_date_must_match_request():
    with pytest.raises(ValueError, match="symbol"):
        parse_prices(payload(code="cn_000001"), "sz000555", "2021-01-01", "2021-12-31")
    with pytest.raises(ValueError, match="date"):
        parse_prices(payload(), "sz000555", "2021-02-01", "2021-12-31")


def test_duplicate_nonfinite_and_invalid_price_geometry_rejected():
    row = json.loads(payload().decode("gb18030"))[0]["hq"][0]
    for rows in [[row, row], [[*row[:1], "NaN", *row[2:]]],
                 [[*row[:5], "20", *row[6:]]]]:
        with pytest.raises(ValueError):
            parse_prices(payload(rows), "sz000555", "2021-01-01", "2021-12-31")


def test_documented_post_close_volume_is_preserved_separately():
    row = json.loads(payload().decode("gb18030"))[0]["hq"][0]
    result = parse_prices(payload([[*row, "2.00"]]), "sz000555", "2021-01-01", "2021-12-31")
    assert result[0]["post_close_volume"] == 200
    assert result[0]["volume"] == 11426600


def test_provider_jsonp_wrapper_and_descending_rows_are_data_only():
    row = json.loads(payload().decode("gb18030"))[0]["hq"][0]
    raw = b"historySearchHandler(" + payload([["2021-01-05", *row[1:]], row]) + b");"
    result = parse_prices(raw, "sz000555", "2021-01-01", "2021-12-31")
    assert [r["date"] for r in result] == ["2021-01-04", "2021-01-05"]
    with pytest.raises(ValueError):
        parse_prices(raw.replace(b"historySearchHandler", b"otherFunction"),
                     "sz000555", "2021-01-01", "2021-12-31")
