import numpy as np

from unified_ai_quant.validation.statistics import deflated_sharpe_ratio, probability_of_backtest_overfitting


def test_pbo_and_dsr_are_bounded():
    train = np.array([[3.0, 2.0, 1.0], [1.0, 3.0, 2.0]])
    test = np.array([[1.0, 2.0, 3.0], [2.0, 3.0, 1.0]])
    assert 0 <= probability_of_backtest_overfitting(train, test) <= 1
    assert 0 <= deflated_sharpe_ratio(2.0, 5, 250) <= 1
