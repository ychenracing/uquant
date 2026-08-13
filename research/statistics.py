"""Deterministic overfitting diagnostics for production-backed research."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """Integer row indexes for one chronological train/test split."""

    train: tuple[int, ...]
    test: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PBOResult:
    """Probability-of-overfitting estimate and its CSCV evidence."""

    probability: float
    logits: tuple[float, ...]
    combinations: int


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    """Multiple-trial Sharpe significance and its calibration terms."""

    probability: float
    expected_max_sharpe: float
    standard_error: float


def walk_forward_folds(
    sample_count: int,
    *,
    train_size: int,
    test_size: int,
    step: int,
    purge: int = 0,
) -> tuple[WalkForwardFold, ...]:
    """Create expanding-in-time independent folds with an explicit purge gap."""
    if min(sample_count, train_size, test_size, step) <= 0 or purge < 0:
        raise ValueError("walk-forward sizes must be positive and purge nonnegative")
    folds: list[WalkForwardFold] = []
    train_start = 0
    while train_start + train_size + purge + test_size <= sample_count:
        train_end = train_start + train_size
        test_start = train_end + purge
        folds.append(
            WalkForwardFold(
                train=tuple(range(train_start, train_end)),
                test=tuple(range(test_start, test_start + test_size)),
            )
        )
        train_start += step
    if not folds:
        raise ValueError("sample_count is insufficient for one walk-forward fold")
    return tuple(folds)


def probability_of_backtest_overfitting(
    returns: np.ndarray,
    *,
    slices: int = 8,
) -> PBOResult:
    """Estimate CSCV PBO from observation-by-candidate return data."""
    matrix = np.asarray(returns, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("PBO requires a 2D matrix with at least two candidates")
    if slices < 4 or slices % 2 or matrix.shape[0] < slices * 2:
        raise ValueError("PBO requires an even slices count and at least two rows per slice")
    if not np.isfinite(matrix).all():
        raise ValueError("PBO returns must be finite")
    blocks = tuple(np.asarray(block, dtype=int) for block in np.array_split(np.arange(len(matrix)), slices))
    logits: list[float] = []
    all_blocks = set(range(slices))
    for selected in itertools.combinations(range(slices), slices // 2):
        train_rows = np.concatenate([blocks[index] for index in selected])
        test_rows = np.concatenate([blocks[index] for index in sorted(all_blocks - set(selected))])
        train_scores = matrix[train_rows].mean(axis=0)
        winner = int(np.argmax(train_scores))
        test_scores = matrix[test_rows].mean(axis=0)
        order = np.argsort(np.argsort(test_scores, kind="stable"), kind="stable")
        percentile = (float(order[winner]) + 1.0) / (matrix.shape[1] + 1.0)
        logits.append(math.log(percentile / (1.0 - percentile)))
    probability = float(np.mean(np.asarray(logits) <= 0.0))
    return PBOResult(probability=probability, logits=tuple(logits), combinations=len(logits))


def deflated_sharpe_ratio(
    *,
    observed_sharpe: float,
    trials: int,
    sample_count: int,
    skew: float,
    kurtosis: float,
) -> DeflatedSharpeResult:
    """Return the probability that Sharpe exceeds the multiple-trial expectation."""
    values = (observed_sharpe, skew, kurtosis)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("deflated Sharpe inputs must be finite")
    if trials < 1:
        raise ValueError("trials must be positive")
    if sample_count < 3:
        raise ValueError("sample_count must be at least three")
    if kurtosis < 1.0:
        raise ValueError("kurtosis must be at least one")
    normal = NormalDist()
    if trials == 1:
        expected = 0.0
    else:
        euler_gamma = 0.5772156649015329
        expected = (
            (1.0 - euler_gamma) * normal.inv_cdf(1.0 - 1.0 / trials)
            + euler_gamma * normal.inv_cdf(1.0 - 1.0 / (trials * math.e))
        )
    variance_term = max(
        1e-12,
        1.0 - skew * observed_sharpe + 0.25 * (kurtosis - 1.0) * observed_sharpe**2,
    )
    standard_error = math.sqrt(variance_term / (sample_count - 1))
    probability = normal.cdf((observed_sharpe - expected) / standard_error)
    return DeflatedSharpeResult(
        probability=float(min(1.0, max(0.0, probability))),
        expected_max_sharpe=float(expected),
        standard_error=float(standard_error),
    )
