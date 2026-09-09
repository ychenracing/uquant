"""Causal synchronized-holdings shock evidence for the portfolio risk owner."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import SystemConfig
from .features import scalar
from .holding_history import protected_weights_for_current_episode
from .types import AccountState


@dataclass(frozen=True, slots=True)
class SectorObservation:
    """One point-in-time breadth and economic-exposure holdings observation."""

    symbol_count: int
    equal_return: float
    weighted_return: float
    positive_breadth: float
    negative_exposure: float
    recovery_breadth: float


@dataclass(frozen=True, slots=True)
class SectorGuardTransition:
    """Persistent guard state and the event produced by the current session."""

    active: bool
    triggered: bool
    recovered: bool
    shock: bool
    shock_count: int
    active_sessions: int
    observation: SectorObservation | None


def _session_distance(
    calendar: pd.DatetimeIndex,
    start: str,
    end: pd.Timestamp,
) -> int:
    """Return completed common-market sessions between two inclusive labels."""
    bounded = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= end)]
    return max(0, len(bounded) - 1)


def observe_deployed_sector(
    *,
    date: pd.Timestamp,
    panel: dict[str, pd.DataFrame],
    symbols: set[str],
    cfg: SystemConfig,
    weights: dict[str, float] | None = None,
    minimum_symbols: int | None = None,
) -> SectorObservation | None:
    """Build a causal breadth snapshot from currently deployed securities.

    The provider deliberately observes holdings rather than the full reference
    universe. A synchronized break in a narrow winning cohort can otherwise be
    diluted by unrelated industries that happen to rise on the same session.
    """
    daily_returns: list[float] = []
    economic_weights: list[float] = []
    recovery_structure: list[bool] = []
    for symbol in sorted(symbols):
        frame = panel.get(symbol)
        if frame is None or date not in frame.index:
            continue
        history = frame.loc[:date, "close"].dropna().astype(float)
        if len(history) < cfg.sector_recovery_ma:
            continue
        current = float(history.iloc[-1])
        previous = float(history.iloc[-2])
        if current <= 0 or previous <= 0:
            continue
        daily_returns.append(current / previous - 1.0)
        economic_weights.append(max(0.0, (weights or {}).get(symbol, 1.0)))
        recovery_structure.append(current > float(history.tail(cfg.sector_recovery_ma).mean()))
    required_symbols = cfg.sector_guard_min_symbols if minimum_symbols is None else minimum_symbols
    if required_symbols < 1:
        raise ValueError("sector observation minimum_symbols must be positive")
    if len(daily_returns) < required_symbols:
        return None
    returns = np.asarray(daily_returns, dtype=float)
    raw_weights = np.asarray(economic_weights, dtype=float)
    if float(raw_weights.sum()) <= 1e-12:
        raw_weights = np.ones_like(returns)
    normalized_weights = raw_weights / raw_weights.sum()
    # BLAS dot kernels may differ by one ULP across CPU families because some
    # use fused multiply-add while others round each product first.  Advance a
    # correctly rounded fused accumulator explicitly so persisted risk evidence
    # and its canonical identity are independent of the execution host.
    from fractions import Fraction

    weighted_return = 0.0
    for daily_return, normalized_weight in zip(returns, normalized_weights, strict=True):
        weighted_return = float(
            Fraction.from_float(weighted_return)
            + Fraction.from_float(float(daily_return))
            * Fraction.from_float(float(normalized_weight))
        )
    return SectorObservation(
        symbol_count=len(daily_returns),
        equal_return=float(returns.mean()),
        weighted_return=weighted_return,
        positive_breadth=float(np.mean(returns > 0.0)),
        negative_exposure=float(normalized_weights[returns < 0.0].sum()),
        recovery_breadth=float(np.mean(recovery_structure)),
    )


def _observe_sector_guard_cohort(
    *,
    date: pd.Timestamp,
    panel: dict[str, pd.DataFrame],
    account: AccountState,
    cfg: SystemConfig,
) -> tuple[set[str], SectorObservation | None]:
    protected = protected_weights_for_current_episode(account)
    deployed = {symbol for symbol, position in account.positions.items() if position.shares > 0}
    if account.sector_guard_active:
        # Recovery must observe the economic cohort that triggered the guard,
        # not merely the residual holdings after the guard's own sparse cut.
        # Otherwise a 3-name shock reduced to one survivor can never satisfy
        # ``sector_guard_min_symbols`` and the state machine locks forever.
        deployed.update(account.sector_guard_symbols)
        deployed.update(protected)
    economic_weights: dict[str, float] = {}
    for symbol in deployed:
        position = account.positions.get(symbol)
        frame = panel.get(symbol)
        if position is not None and frame is not None and date in frame.index:
            economic_weights[symbol] = position.shares * scalar(frame.loc[date], "close", 0.0)
        elif symbol in protected:
            economic_weights[symbol] = max(0.0, protected[symbol])
    observation = observe_deployed_sector(
        date=date,
        panel=panel,
        symbols=deployed,
        cfg=cfg,
        weights=economic_weights,
        # The ordinary guard still needs breadth.  A one-name trigger cohort
        # can exist only after the acute owner explicitly activated it; allow
        # that same observed name to prove recovery instead of locking cash.
        minimum_symbols=(
            1 if account.sector_guard_active and len(account.sector_guard_symbols) == 1 else None
        ),
    )
    return deployed, observation


def _advance_sector_shock_dates(
    *,
    date: pd.Timestamp,
    calendar: pd.DatetimeIndex,
    account: AccountState,
    observation: SectorObservation | None,
    cfg: SystemConfig,
) -> tuple[bool, str]:
    account.sector_shock_dates = [
        value
        for value in account.sector_shock_dates
        if pd.Timestamp(value) <= date and _session_distance(calendar, value, date) < cfg.sector_shock_window
    ]
    shock = bool(
        observation is not None
        and (
            (
                observation.equal_return <= cfg.sector_shock_return
                and observation.positive_breadth <= cfg.sector_shock_breadth
            )
            or (
                observation.weighted_return <= cfg.sector_weighted_shock_return
                and observation.negative_exposure >= cfg.sector_weighted_negative_exposure
            )
        )
    )
    date_label = str(date.date())
    if shock and date_label not in account.sector_shock_dates:
        account.sector_shock_dates.append(date_label)
    return shock, date_label


def _activate_sector_guard(
    *,
    account: AccountState,
    deployed: set[str],
    date_label: str,
    leadership_divergence: float,
    cfg: SystemConfig,
) -> bool:
    triggered = bool(
        not account.sector_guard_active
        and len(account.sector_shock_dates) >= cfg.sector_shock_confirmations
        and leadership_divergence >= cfg.sector_guard_divergence
    )
    if triggered:
        account.sector_guard_active = True
        account.sector_guard_started = date_label
        account.sector_guard_symbols = sorted(deployed)
        account.sector_recovery_streak = 0
    return triggered


def _recover_sector_guard(
    *,
    date: pd.Timestamp,
    calendar: pd.DatetimeIndex,
    account: AccountState,
    observation: SectorObservation | None,
    shock: bool,
    cfg: SystemConfig,
) -> tuple[int, bool]:
    active_sessions = (
        _session_distance(calendar, account.sector_guard_started, date)
        if account.sector_guard_active and account.sector_guard_started
        else 0
    )
    recovered = False
    if account.sector_guard_active:
        repair = bool(
            observation is not None
            and not shock
            and observation.equal_return > cfg.sector_recovery_return
            and observation.recovery_breadth >= cfg.sector_recovery_breadth
            and active_sessions >= cfg.sector_guard_min_sessions
        )
        account.sector_recovery_streak = account.sector_recovery_streak + 1 if repair else 0
        if account.sector_recovery_streak >= cfg.sector_recovery_confirmations:
            recovered = True
            account.sector_guard_active = False
            account.sector_guard_started = ""
            account.sector_guard_symbols.clear()
            account.sector_recovery_streak = 0
            account.sector_shock_dates.clear()
    return active_sessions, recovered


def update_sector_guard(
    *,
    date: pd.Timestamp,
    calendar: pd.DatetimeIndex,
    panel: dict[str, pd.DataFrame],
    account: AccountState,
    leadership_divergence: float,
    cfg: SystemConfig,
) -> SectorGuardTransition:
    """Advance the default-on shock/guard/recovery state machine.

    Activation needs repeated synchronized losses plus an unusually narrow
    technology leadership premium. This retains the early warning supplied by
    a sector breadth guard without treating ordinary trend pullbacks as crises.
    Recovery is deliberately slower than activation and pauses when coverage is
    insufficient.
    """
    if not cfg.sector_guard_enabled:
        account.sector_shock_dates.clear()
        account.sector_guard_active = False
        account.sector_guard_started = ""
        account.sector_guard_symbols.clear()
        account.sector_recovery_streak = 0
        return SectorGuardTransition(False, False, False, False, 0, 0, None)

    deployed, observation = _observe_sector_guard_cohort(
        date=date,
        panel=panel,
        account=account,
        cfg=cfg,
    )
    shock, date_label = _advance_sector_shock_dates(
        date=date,
        calendar=calendar,
        account=account,
        observation=observation,
        cfg=cfg,
    )
    triggered = _activate_sector_guard(
        account=account,
        deployed=deployed,
        date_label=date_label,
        leadership_divergence=leadership_divergence,
        cfg=cfg,
    )
    active_sessions, recovered = _recover_sector_guard(
        date=date,
        calendar=calendar,
        account=account,
        observation=observation,
        shock=shock,
        cfg=cfg,
    )

    return SectorGuardTransition(
        active=account.sector_guard_active,
        triggered=triggered,
        recovered=recovered,
        shock=shock,
        shock_count=len(account.sector_shock_dates),
        active_sessions=active_sessions,
        observation=observation,
    )
