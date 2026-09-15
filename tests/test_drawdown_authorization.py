"""The user's one percentage point margin retains the original judgment."""
import math

from uquant.validation.promotion import _champion_violations, _hard_violations


def test_observed_h2_drawdown_uses_explicit_absolute_margin_once():
    champion = {"final_wealth": 1., "max_drawdown": .08592480598703311, "acute_return": None}
    metrics = {"final_wealth": 1.8168767799091217, "max_drawdown": .09854024217858659, "acute_return": None}
    args = dict(name="e/h2_2024", metrics=metrics, champion=champion)
    assert not _champion_violations(**args)
    assert _champion_violations(**args, authorized=False) == ["e/h2_2024: max_drawdown regressed from production champion"]
    metrics["max_drawdown"] = champion["max_drawdown"] + .005 + .01
    assert not _champion_violations(**args)
    metrics["max_drawdown"] = math.nextafter(metrics["max_drawdown"], math.inf)
    assert _champion_violations(**args)


def test_hard_drawdown_tolerance_is_one_point_not_one_percent():
    args = dict(name="e/h2_2024", metrics={"max_drawdown": .11}, gate={"max_drawdown": .1})
    assert not _hard_violations(**args)
    assert _hard_violations(**args, authorized=False)
    args["metrics"]["max_drawdown"] = .1100001
    assert _hard_violations(**args)
