from research.opportunity_capture_audit import label, rank_ic, ranks, select, valid_features


def leader(symbol, score, momentum, group="a", strength=.5):
    return {"symbol": symbol, "score": score, "industry": group,
            "components": {"raw_ret120": momentum, "industry_rotation_strength": strength}}


def test_ranking_uses_only_contemporaneous_features_and_stable_ties():
    rows = [leader("c", .9, .1), leader("b", .9, .2),
            leader("a", .2, .3), leader("d", .1, .4, "b", .8)]
    assert select(rows, "score") == ["b", "c", "a"]
    assert select(rows, "momentum") == ["d", "a", "b"]
    assert select(rows, "industry_first") == ["d"]
    assert rows[0]["symbol"] == "c"


def test_forward_label_uses_next_open_and_censors_without_future_data():
    dates = ["2026-08-03", "2026-08-04", "2026-08-05"]
    prices = {"a": {dates[0]: 100, dates[1]: 10, dates[2]: 12}}
    result = label(prices, "a", dates, 0, 1)
    assert abs(result["gross_return"] - .2) < 1e-12
    assert result["entry_date"] == dates[1]
    assert label(prices, "a", dates, 0, 2) is None
    del prices["a"][dates[2]]
    assert label(prices, "a", dates, 0, 1) is None


def test_rank_correlation_handles_ties_and_undefined_population():
    assert ranks([10, 10, 30]) == [1.5, 1.5, 3]
    assert rank_ic([1, 2, 3], [30, 20, 10]) == -1
    assert rank_ic([1, 1, 1], [30, 20, 10]) is None


def test_missing_history_is_excluded_before_labeling_for_all_rankings():
    assert not valid_features(leader("new", .9, "NaN"))
    assert not valid_features(leader("new", .9, float("nan")))
    assert valid_features(leader("old", .9, -.1))
