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
    """Probability-of-overfitting estimate and its CSCV evidence.

    ``probability`` is ``None`` with ``status == "NOT_ESTIMABLE"`` when no
    combination has a distinguishable in-sample winner.
    """

    probability: float | None
    logits: tuple[float, ...]
    combinations: int
    status: str = "OK"
    reason: str = ""


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
    expanding: bool = False,
    non_overlapping_test: bool = False,
) -> tuple[WalkForwardFold, ...]:
    """Create chronological train/test folds with an explicit purge gap.

    By default the train window has a fixed width and rolls forward by
    ``step``; ``expanding=True`` keeps the train start at row zero. Test
    windows overlap whenever ``step < test_size`` unless
    ``non_overlapping_test`` is requested. Folds are not independent
    samples: serially correlated returns remain correlated across folds.
    """
    if min(sample_count, train_size, test_size, step) <= 0 or purge < 0:
        raise ValueError("walk-forward sizes must be positive and purge nonnegative")
    if non_overlapping_test and step < test_size:
        raise ValueError("non-overlapping test windows require step >= test_size")
    folds: list[WalkForwardFold] = []
    offset = 0
    while offset + train_size + purge + test_size <= sample_count:
        train_start = 0 if expanding else offset
        train_end = offset + train_size
        test_start = train_end + purge
        folds.append(
            WalkForwardFold(
                train=tuple(range(train_start, train_end)),
                test=tuple(range(test_start, test_start + test_size)),
            )
        )
        offset += step
    if not folds:
        raise ValueError("sample_count is insufficient for one walk-forward fold")
    return tuple(folds)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Return 1-based ranks with tied values sharing their mean rank."""

    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    sorted_values = values[order]
    start = 0
    while start < len(values):
        stop = start + 1
        while stop < len(values) and sorted_values[stop] == sorted_values[start]:
            stop += 1
        ranks[order[start:stop]] = (start + stop + 1) / 2.0
        start = stop
    return ranks


def probability_of_backtest_overfitting(
    returns: np.ndarray,
    *,
    slices: int = 8,
) -> PBOResult:
    """Estimate CSCV PBO from observation-by-candidate return data.

    Candidates are ranked by their mean return in each half. Tied test
    scores share their average rank; tied in-sample winners contribute the
    mean of their logits, so column order cannot change the result. A
    combination whose candidates are all tied in-sample has no winner and
    is excluded; if none remain the estimate is ``NOT_ESTIMABLE``.
    """
    matrix = np.asarray(returns, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] < 2:
        raise ValueError("PBO requires a 2D matrix with at least two candidates")
    if slices < 4 or slices % 2 or matrix.shape[0] < slices * 2:
        raise ValueError("PBO requires an even slices count and at least two rows per slice")
    if not np.isfinite(matrix).all():
        raise ValueError("PBO returns must be finite")
    blocks = tuple(np.asarray(block, dtype=int) for block in np.array_split(np.arange(len(matrix)), slices))
    logits: list[float] = []
    combinations = 0
    all_blocks = set(range(slices))
    candidates = matrix.shape[1]
    for selected in itertools.combinations(range(slices), slices // 2):
        combinations += 1
        train_rows = np.concatenate([blocks[index] for index in selected])
        test_rows = np.concatenate([blocks[index] for index in sorted(all_blocks - set(selected))])
        train_scores = matrix[train_rows].mean(axis=0)
        winners = np.flatnonzero(train_scores == train_scores.max())
        if len(winners) == candidates:
            continue
        ranks = _average_ranks(matrix[test_rows].mean(axis=0))
        values = []
        for winner in winners:
            percentile = float(ranks[winner]) / (candidates + 1.0)
            values.append(math.log(percentile / (1.0 - percentile)))
        logits.append(float(np.mean(values)))
    if not logits:
        return PBOResult(None, (), combinations, "NOT_ESTIMABLE", "no distinguishable in-sample winner")
    probability = float(np.mean(np.asarray(logits) <= 0.0))
    return PBOResult(probability=probability, logits=tuple(logits), combinations=combinations)


def deflated_sharpe_ratio(
    *,
    observed_sharpe: float,
    trials: int,
    sample_count: int,
    skew: float,
    kurtosis: float,
    trial_sharpe_variance: float,
) -> DeflatedSharpeResult:
    """Return the probability that Sharpe exceeds the multiple-trial expectation.

    All Sharpe inputs are per-observation (not annualized). ``kurtosis`` is
    ordinary (non-excess) kurtosis, 3 for a normal distribution.
    ``trial_sharpe_variance`` is the cross-sectional variance of the
    per-observation Sharpe estimates of the ``trials`` effectively
    independent candidates; the expected maximum scales with its square root
    (Bailey and Lopez de Prado, 2014).
    """
    values = (observed_sharpe, skew, kurtosis, trial_sharpe_variance)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("deflated Sharpe inputs must be finite")
    if trials < 1:
        raise ValueError("trials must be positive")
    if sample_count < 3:
        raise ValueError("sample_count must be at least three")
    if kurtosis < 1.0:
        raise ValueError("kurtosis must be at least one")
    if trial_sharpe_variance < 0:
        raise ValueError("trial_sharpe_variance cannot be negative")
    normal = NormalDist()
    if trials == 1 or trial_sharpe_variance == 0:
        expected = 0.0
    else:
        euler_gamma = 0.5772156649015329
        expected = math.sqrt(trial_sharpe_variance) * (
            (1.0 - euler_gamma) * normal.inv_cdf(1.0 - 1.0 / trials)
            + euler_gamma * normal.inv_cdf(1.0 - 1.0 / (trials * math.e))
        )
    variance_term = 1.0 - skew * observed_sharpe + 0.25 * (kurtosis - 1.0) * observed_sharpe**2
    if variance_term <= 0:
        raise ValueError("Sharpe standard-error variance is not positive for these moments")
    standard_error = math.sqrt(variance_term / (sample_count - 1))
    probability = normal.cdf((observed_sharpe - expected) / standard_error)
    return DeflatedSharpeResult(
        probability=float(min(1.0, max(0.0, probability))),
        expected_max_sharpe=float(expected),
        standard_error=float(standard_error),
    )
