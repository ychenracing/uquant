"""Overfitting diagnostics used by candidate promotion, never by execution."""

from __future__ import annotations

import math

import numpy as np


def probability_of_backtest_overfitting(train_scores: np.ndarray, test_scores: np.ndarray) -> float:
    if train_scores.shape != test_scores.shape or train_scores.ndim != 2:
        raise ValueError("train_scores and test_scores must be equal two-dimensional matrices")
    if train_scores.shape[1] < 2:
        return 0.0
    failures = 0
    for train, test in zip(train_scores, test_scores, strict=True):
        winner = int(np.nanargmax(train))
        test_rank = int(np.argsort(np.argsort(test))[winner])
        failures += test_rank < len(test) / 2
    return failures / len(train_scores) if len(train_scores) else 0.0


def deflated_sharpe_ratio(
    sharpe: float, trials: int, observations: int, skew: float = 0.0, kurtosis: float = 3.0
) -> float:
    if trials < 1 or observations < 3:
        raise ValueError("trials and observations must be positive")
    expected_max = math.sqrt(max(0.0, 2.0 * math.log(max(1, trials))))
    standard_error = math.sqrt(
        max(1e-12, (1.0 - skew * sharpe + (kurtosis - 1.0) * sharpe**2 / 4.0) / (observations - 1))
    )
    z_score = (sharpe - expected_max) / standard_error
    return 0.5 * (1.0 + math.erf(z_score / math.sqrt(2.0)))
