from research.revenue_signal_audit import partial_rank_ic, report_signals


def report(period, date, growth):
    return {"status": "verified_numeric_original", "period_index": period,
            "period": str(period), "disclosed_date": date,
            "cumulative_revenue_yoy_percent": growth, "sha256": str(growth)}


def test_availability_excludes_same_day_and_later_old_period_does_not_replace_new():
    rows = [report(1, "2023-01-03", 1.), report(2, "2023-04-20", 2.),
            report(1, "2023-04-21", 9.), report(3, "2023-07-20", 3.)]
    assert report_signals(rows, "2023-01-03") is None
    assert report_signals(rows, "2023-07-20")["revenue_yoy"] == 2.
    assert report_signals(rows, "2023-07-21")["revenue_yoy_change"] == 1.


def test_missing_intermediate_period_does_not_create_acceleration():
    result = report_signals([report(1, "2023-01-03", 1.), report(3, "2023-07-20", 3.)], "2023-07-21")
    assert result["revenue_yoy_change"] is None


def test_price_duplicate_has_no_independent_rank_information():
    assert partial_rank_ic([1, 2, 3, 4], [1, 3, 2, 4], [1, 2, 3, 4]) is None
