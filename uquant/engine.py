"""The only production engine; daily and backtest call the same decision path."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, SystemConfig
from .data import DataStore, normalize_symbol
from .execution import (
    ExecutionPlanner,
    merge_pending_orders,
    plan_orders,
    reconcile_account_orders,
)
from .features import compute_features
from .leader import REFERENCE_UNIVERSE, compute_leaders
from .opportunity import classify_opportunity
from .portfolio import PortfolioAllocator, current_weights
from .risk import assess_risk
from .types import AccountOrder, AccountState, Decision, Fill, LeaderScore

INDEX_SYMBOLS = ("sh000300", "sh000682")


def code_fingerprint() -> str:
    """Return a stable digest of all production modules in the package root."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ProductionEngine:
    """Own the single decision path used by both daily operation and replay."""
    def __init__(self, data_dir: str | Path, cfg: SystemConfig = DEFAULT_CONFIG) -> None:
        self.cfg = cfg
        self.data = DataStore(data_dir)
        self.execution = ExecutionPlanner(cfg)
        self.allocator = PortfolioAllocator(cfg)
        self._raw: dict[str, pd.DataFrame] = {}
        self._features: dict[str, pd.DataFrame] = {}
        self._manifest_cache: dict[tuple[tuple[str, ...], str], str] = {}
        self._reference_returns: pd.DataFrame | None = None
        self._code_hash: str | None = None
        self._leader_score_cache: dict[
            tuple[pd.Timestamp, tuple[str, ...]], dict[str, LeaderScore]
        ] = {}

    def _load(self, symbols: Iterable[str]) -> None:
        for symbol in sorted({normalize_symbol(item) for item in symbols}):
            if symbol not in self._raw:
                raw = self.data.load(symbol)
                self._raw[symbol] = raw
                self._features[symbol] = compute_features(raw, self.cfg)
        if self._reference_returns is None and set(REFERENCE_UNIVERSE).issubset(self._raw):
            self._reference_returns = pd.DataFrame(
                {
                    symbol: self._raw[symbol]["close"].pct_change(fill_method=None)
                    for symbol in REFERENCE_UNIVERSE
                }
            )

    def _price(self, symbol: str, date: pd.Timestamp, field: str = "close") -> float:
        frame = self._raw[symbol].loc[:date]
        if frame.empty:
            raise RuntimeError(f"{symbol} has no mark price at {date.date()}")
        return float(frame.iloc[-1][field])

    def equity(self, account: AccountState, date: pd.Timestamp, field: str = "close") -> float:
        return account.cash + sum(
            position.shares * self._price(symbol, date, field)
            for symbol, position in account.positions.items()
            if symbol in self._raw
        )

    def decide(self, *, symbols: Iterable[str], as_of: str, account: AccountState) -> Decision:
        user_symbols = tuple(sorted({normalize_symbol(item) for item in symbols}))
        if not user_symbols:
            raise ValueError("at least one technology-sector symbol is required")
        all_symbols = set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
        self._load(all_symbols)
        date = pd.Timestamp(as_of).normalize()
        if date not in self._features["sh000300"].index or date not in self._features["sh000682"].index:
            raise RuntimeError("decision date is not a common index session")
        if account.last_successful_run and pd.Timestamp(account.last_successful_run) > date:
            raise RuntimeError("account risk state comes from a future date")
        current_symbols = tuple(
            sorted(
                symbol
                for symbol in all_symbols
                if not self._raw[symbol].loc[:date].empty
            )
        )
        if account.data_hash:
            verification_symbols = tuple(
                sorted(account.data_hash_symbols or current_symbols)
            )
            verification_as_of = account.data_hash_as_of or account.last_successful_run
            if verification_as_of:
                verification_date = pd.Timestamp(verification_as_of).normalize()
                if verification_date > date:
                    raise RuntimeError("account data provenance comes from a future date")
                verification_key = (
                    verification_symbols,
                    str(verification_date.date()),
                )
                if verification_key not in self._manifest_cache:
                    self._manifest_cache[verification_key] = self.data.manifest(
                        verification_symbols,
                        as_of=verification_date,
                    ).digest
                verified_digest = self._manifest_cache[verification_key]
            else:
                # Account files without an as-of boundary can be opened only
                # while their exact data snapshot is still present. A successful
                # run upgrades the state to bounded, append-safe provenance.
                verified_digest = self.data.manifest(verification_symbols).digest
            if account.data_hash != verified_digest and self.cfg.fail_closed:
                raise RuntimeError("historical data prefix differs from account state")
        manifest_key = (current_symbols, str(date.date()))
        if manifest_key not in self._manifest_cache:
            self._manifest_cache[manifest_key] = self.data.manifest(
                current_symbols,
                as_of=date,
            ).digest
        data_digest = self._manifest_cache[manifest_key]
        if self._code_hash is None:
            self._code_hash = code_fingerprint()
        current_code_hash = self._code_hash
        if account.code_hash and account.code_hash != current_code_hash and self.cfg.fail_closed:
            raise RuntimeError("production code hash differs from account state")
        reference_panel = {symbol: self._features[symbol] for symbol in REFERENCE_UNIVERSE}
        user_panel = {symbol: self._features[symbol] for symbol in user_symbols}
        combined = dict(reference_panel)
        combined.update(user_panel)
        broad = self._features["sh000300"]
        tech = self._features["sh000682"]
        all_leaders = compute_leaders(
            combined,
            as_of=date,
            tech=tech,
            account=account,
            cfg=self.cfg,
            score_cache=self._leader_score_cache,
        )
        # A historical universe can legitimately contain securities that had not
        # listed yet. They are invisible until their first row; an existing
        # position, however, must always remain markable and therefore still
        # fails closed through ``_price`` if its data disappears.
        visible_users = {
            symbol for symbol in user_symbols if not self._raw[symbol].loc[:date].empty
        }
        prices = {
            symbol: self._price(symbol, date)
            for symbol in visible_users | set(account.positions)
        }
        _, equity = current_weights(account, prices)
        risk = assess_risk(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            reference_returns=self._reference_returns,
            user_panel=user_panel,
            leaders=all_leaders,
            account=account,
            equity=equity,
            cfg=self.cfg,
        )
        user_leaders = {symbol: all_leaders[symbol] for symbol in user_symbols if symbol in all_leaders}
        opportunity = classify_opportunity(
            date=date,
            broad=broad,
            tech=tech,
            reference_panel=reference_panel,
            leaders=user_leaders,
            risk=risk.state,
            account=account,
            cfg=self.cfg,
        )
        targets = self.allocator.allocate(
            date=date,
            opportunity=opportunity,
            risk=risk,
            user_panel=user_panel,
            leaders=user_leaders,
            account=account,
            prices=prices,
        )
        planned_orders = plan_orders(
            signal_date=str(date.date()),
            targets=targets,
            account=account,
            prices=prices,
            cfg=self.cfg,
        )
        previous_orders = list(account.pending_orders)
        orders = merge_pending_orders(
            retained=previous_orders,
            planned=planned_orders,
            targets=targets,
        )
        orders = reconcile_account_orders(
            account=account,
            previous=previous_orders,
            current=orders,
            submitted_date=str(date.date()),
        )
        canonical = {
            "date": str(date.date()),
            "opportunity": opportunity.value,
            "risk": risk.state.value,
            "targets": [
                {"symbol": item.symbol, "weight": round(item.weight, 12), "lifecycle": item.lifecycle}
                for item in targets
            ],
            "orders": [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side,
                    "target_weight": round(item.target_weight, 12),
                }
                for item in orders
            ],
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        account.last_successful_run = str(date.date())
        account.data_hash = data_digest
        account.data_hash_as_of = str(date.date())
        account.data_hash_symbols = list(current_symbols)
        account.code_hash = current_code_hash
        return Decision(
            date=str(date.date()),
            opportunity=opportunity,
            risk=risk.state,
            target_gross=sum(item.weight for item in targets),
            target_k=sum(item.weight > 0 for item in targets),
            targets=targets,
            pending_orders=orders,
            risk_summary={
                **risk.evidence,
                "votes": risk.votes,
                "reasons": list(risk.reasons),
                "shock_state": risk.shock_state,
            },
            decision_digest=digest,
        )

    def deterministic_decision(
        self, *, symbols: Iterable[str], as_of: str, account: AccountState
    ) -> tuple[Decision, AccountState]:
        cloned = copy.deepcopy(account)
        return self.decide(symbols=symbols, as_of=as_of, account=cloned), cloned

    def backtest(
        self,
        *,
        symbols: Iterable[str],
        start: str,
        end: str,
        initial_cash: float | None = None,
    ) -> dict[str, Any]:
        user_symbols = tuple(sorted({normalize_symbol(item) for item in symbols}))
        self._load(set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS))
        sessions = self._raw["sh000300"].index.intersection(self._raw["sh000682"].index)
        sessions = sessions[(sessions >= pd.Timestamp(start)) & (sessions <= pd.Timestamp(end))]
        if len(sessions) < 2:
            raise RuntimeError("backtest window has fewer than two sessions")
        account = AccountState.empty(initial_cash or self.cfg.initial_cash)
        # First state has no prior persisted hash. Daily production requires hashes after initialization.
        equity_rows: list[tuple[pd.Timestamp, float]] = []
        decisions: list[Decision] = []
        raw_user_panel = {symbol: self._raw[symbol] for symbol in user_symbols}
        for date in sessions:
            self.execution.execute_open(date=date, account=account, panel=raw_user_panel)
            for symbol, position in account.positions.items():
                if date in self._raw[symbol].index:
                    close = float(self._raw[symbol].loc[date, "close"])
                    position.highest_close = max(position.highest_close, close)
                    for tranche in position.tranches:
                        tranche.highest_close = max(tranche.highest_close, close)
            equity = self.equity(account, date)
            equity_rows.append((date, equity))
            decision = self.decide(symbols=user_symbols, as_of=str(date.date()), account=account)
            decisions.append(decision)
            account.pending_orders = list(decision.pending_orders)
        final_date = sessions[-1]
        final_equity = self.equity(account, final_date)
        metrics = performance_metrics(
            equity_rows=equity_rows,
            fills=account.fills,
            orders=account.order_ledger,
            initial_cash=account.initial_cash,
            risk_events=account.risk_events,
            benchmark_total_return=(
                float(
                    self._raw["sh000682"].loc[final_date, "close"]
                    / self._raw["sh000682"].loc[sessions[0], "close"]
                    - 1.0
                )
            ),
        )
        metrics.update(
            start=str(sessions[0].date()),
            end=str(sessions[-1].date()),
            final_wealth=final_equity / account.initial_cash,
            final_equity=final_equity,
            decision_digests=[item.decision_digest for item in decisions],
            pending_orders=len(account.pending_orders),
            final_account=account.to_dict(),
            attribution=attribution(account.fills),
            internal_events={
                "risk": len(account.risk_events),
                "lifecycle": len(account.lifecycle_events),
                "replacement": len(account.replacement_events),
                "target_decisions": sum(len(item.targets) for item in decisions),
                "pending_order_intents": sum(
                    len(item.pending_orders) for item in decisions
                ),
                "broker_submissions": len(account.order_ledger),
                "unfilled_broker_submissions": sum(
                    item.filled_shares == 0 for item in account.order_ledger
                ),
            },
            daily_risk_states=[
                {"date": item.date, "state": item.risk.value}
                for item in decisions
            ],
        )
        return metrics


def _drawdown_stats(equity: pd.Series) -> dict[str, float | int]:
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    underwater = drawdown < 0
    duration = current = 0
    for flag in underwater:
        current = current + 1 if flag else 0
        duration = max(duration, current)
    trough = int(drawdown.values.argmin())
    recovery = 0
    peak_value = float(peak.iloc[trough])
    for value in equity.iloc[trough + 1 :]:
        recovery += 1
        if value >= peak_value:
            break
    return {
        "max_drawdown": float(-drawdown.min()),
        "rolling_drawdown_p95": float((-drawdown).quantile(0.95)),
        "max_drawdown_duration": duration,
        "peak_to_recovery_days": recovery,
    }


def performance_metrics(
    *,
    equity_rows: list[tuple[pd.Timestamp, float]],
    fills: list[Fill],
    orders: list[AccountOrder],
    initial_cash: float,
    risk_events: list[dict[str, Any]],
    benchmark_total_return: float,
) -> dict[str, Any]:
    """Calculate portfolio, drawdown, turnover, order, and attribution metrics."""
    broker_orders = [item for item in orders if item.filled_shares > 0]
    equity = pd.Series({date: value for date, value in equity_rows}, dtype=float).sort_index()
    returns = equity.pct_change(fill_method=None).dropna()
    years = max(len(equity) / 242.0, 1 / 242.0)
    total_return = float(equity.iloc[-1] / initial_cash - 1.0)
    cagr = float((equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0)
    sharpe = (
        float(np.sqrt(242) * returns.mean() / returns.std(ddof=0)) if returns.std(ddof=0) > 1e-12 else 0.0
    )
    dd = _drawdown_stats(equity)
    max_dd = float(dd["max_drawdown"])
    gross_turnover = sum(item.gross_value for item in fills) / initial_cash
    fees = sum(item.commission + item.stamp_duty + item.transfer_fee for item in fills)
    holding_days: list[int] = []
    buy_lots: dict[str, list[list[Any]]] = {}
    inventory: dict[str, int] = {}
    round_trips = 0
    for fill in fills:
        if fill.side == "BUY":
            buy_lots.setdefault(fill.symbol, []).append(
                [fill.shares, pd.Timestamp(fill.fill_date)]
            )
            inventory[fill.symbol] = inventory.get(fill.symbol, 0) + fill.shares
            continue
        before = inventory.get(fill.symbol, 0)
        remaining = fill.shares
        for lot in buy_lots.get(fill.symbol, []):
            available = int(lot[0])
            if available <= 0 or remaining <= 0:
                continue
            sold = min(available, remaining)
            holding_days.append(
                (pd.Timestamp(fill.fill_date) - pd.Timestamp(lot[1])).days
            )
            lot[0] = available - sold
            remaining -= sold
        buy_lots[fill.symbol] = [
            lot for lot in buy_lots.get(fill.symbol, []) if int(lot[0]) > 0
        ]
        inventory[fill.symbol] = max(0, before - fill.shares)
        if before > 0 and inventory[fill.symbol] == 0:
            round_trips += 1
    rolling20 = equity.pct_change(20, fill_method=None)
    rolling60 = equity.pct_change(60, fill_method=None)
    first_caution = next(
        (str(item.get("date")) for item in risk_events if item.get("to") == "CAUTION"),
        None,
    )
    first_risk_off = next(
        (
            str(item.get("date"))
            for item in risk_events
            if item.get("to") in {"RISK_OFF", "CRISIS"}
        ),
        None,
    )
    risk_tokens = ("risk", "drawdown", "shock", "crisis", "capital protection")
    first_reduce = next(
        (
            fill.fill_date
            for fill in fills
            if fill.side == "SELL"
            and any(token in fill.reason.lower() for token in risk_tokens)
        ),
        None,
    )
    first_action = min(
        (pd.Timestamp(value) for value in (first_caution, first_risk_off, first_reduce) if value),
        default=None,
    )
    drawdown = 1.0 - equity / equity.cummax()

    def lead_to_drawdown(threshold: float) -> int | None:
        crossings = drawdown[drawdown >= threshold]
        if crossings.empty or first_action is None:
            return None
        target = crossings.index[0]
        return int(equity.index.get_indexer([target])[0] - equity.index.get_indexer([first_action], method="ffill")[0])

    return {
        "total_return": total_return,
        "cagr": cagr,
        "benchmark_total_return": benchmark_total_return,
        "excess_return": total_return - benchmark_total_return,
        "sharpe": sharpe,
        "calmar": cagr / max_dd if max_dd > 1e-12 else 0.0,
        **dd,
        "worst_20d": float(rolling20.min()) if rolling20.notna().any() else 0.0,
        "worst_60d": float(rolling60.min()) if rolling60.notna().any() else 0.0,
        "account_orders": len(broker_orders),
        "submitted_account_orders": len(orders),
        "unfilled_account_submissions": sum(
            item.filled_shares == 0 for item in orders
        ),
        "round_trips": round_trips,
        "gross_turnover": gross_turnover,
        "annual_turnover": gross_turnover / years,
        "median_holding_days": float(median(holding_days)) if holding_days else 0.0,
        "fees": fees,
        "slippage_cost": sum(item.slippage_cost for item in fills),
        "first_caution": first_caution,
        "first_risk_off": first_risk_off,
        "first_reduce": first_reduce,
        "lead_to_10pct_dd": lead_to_drawdown(0.10),
        "lead_to_15pct_dd": lead_to_drawdown(0.15),
        "risk_events": risk_events,
        "order_ledger": [
            {
                "order_id": item.order_id,
                "signal_date": item.signal_date,
                "submitted_date": item.submitted_date,
                "symbol": item.symbol,
                "side": item.side,
                "target_weight": item.target_weight,
                "reason": item.reason,
                "lifecycle": item.lifecycle,
                "status": item.status,
                "requested_shares": item.requested_shares,
                "filled_shares": item.filled_shares,
                "remaining_shares": item.remaining_shares,
                "attempts": item.attempts,
                "last_update_date": item.last_update_date,
                "last_event": item.last_event,
                "replaced_by": item.replaced_by,
                "cancel_reason": item.cancel_reason,
            }
            for item in broker_orders
        ],
        "submission_ledger": [
            {
                "order_id": item.order_id,
                "signal_date": item.signal_date,
                "submitted_date": item.submitted_date,
                "symbol": item.symbol,
                "side": item.side,
                "target_weight": item.target_weight,
                "reason": item.reason,
                "lifecycle": item.lifecycle,
                "status": item.status,
                "requested_shares": item.requested_shares,
                "filled_shares": item.filled_shares,
                "remaining_shares": item.remaining_shares,
                "attempts": item.attempts,
                "last_update_date": item.last_update_date,
                "last_event": item.last_event,
                "replaced_by": item.replaced_by,
                "cancel_reason": item.cancel_reason,
            }
            for item in orders
        ],
        "equity_curve": [{"date": str(date.date()), "equity": value} for date, value in equity.items()],
    }


def attribution(fills: list[Fill]) -> dict[str, Any]:
    """Aggregate realized results by lifecycle and decision-reason family."""
    lifecycle_names = ("core", "add1", "add2", "satellite", "recovery")
    lifecycle: dict[str, dict[str, float | int]] = {
        name: {"fills": 0, "gross_value": 0.0, "fees": 0.0, "realized_pnl": 0.0}
        for name in lifecycle_names
    }
    lots: dict[str, list[dict[str, float | str]]] = {}
    reason_buckets = {
        "rotation": {"fills": 0, "gross_value": 0.0, "fees": 0.0},
        "risk_cuts": {"fills": 0, "gross_value": 0.0, "fees": 0.0},
    }
    for fill in fills:
        name = fill.lifecycle.lower()
        bucket = lifecycle.setdefault(
            name,
            {"fills": 0, "gross_value": 0.0, "fees": 0.0, "realized_pnl": 0.0},
        )
        fees = fill.commission + fill.stamp_duty + fill.transfer_fee
        bucket["fills"] = int(bucket["fills"]) + 1
        bucket["gross_value"] = float(bucket["gross_value"]) + fill.gross_value
        bucket["fees"] = float(bucket["fees"]) + fees
        normalized_reason = fill.reason.lower().replace("-", "_")
        reason_name = (
            "rotation"
            if "rotation" in normalized_reason or "replacement" in normalized_reason
            else "risk_cuts"
            if any(
                token in normalized_reason
                for token in (
                    "risk",
                    "drawdown",
                    "shock",
                    "crisis",
                    "capital protection",
                )
            )
            else ""
        )
        if reason_name:
            reason_bucket = reason_buckets[reason_name]
            reason_bucket["fills"] = int(reason_bucket["fills"]) + 1
            reason_bucket["gross_value"] = (
                float(reason_bucket["gross_value"]) + fill.gross_value
            )
            reason_bucket["fees"] = float(reason_bucket["fees"]) + fees
        if fill.side == "BUY":
            lots.setdefault(fill.symbol, []).append(
                {
                    "shares": float(fill.shares),
                    "unit_cost": (fill.gross_value + fees) / fill.shares,
                    "lifecycle": name,
                }
            )
            continue
        remaining = fill.shares
        unit_proceeds = (fill.gross_value - fees) / fill.shares
        for lot in lots.get(fill.symbol, []):
            available = int(float(lot["shares"]))
            if available <= 0 or remaining <= 0:
                continue
            sold = min(available, remaining)
            origin = str(lot["lifecycle"])
            origin_bucket = lifecycle.setdefault(
                origin,
                {
                    "fills": 0,
                    "gross_value": 0.0,
                    "fees": 0.0,
                    "realized_pnl": 0.0,
                },
            )
            origin_bucket["realized_pnl"] = float(
                origin_bucket["realized_pnl"]
            ) + sold * (unit_proceeds - float(lot["unit_cost"]))
            lot["shares"] = float(available - sold)
            remaining -= sold
        lots[fill.symbol] = [
            lot for lot in lots.get(fill.symbol, []) if float(lot["shares"]) > 0
        ]
    open_lots = {
        name: int(
            sum(
                float(lot["shares"])
                for symbol_lots in lots.values()
                for lot in symbol_lots
                if str(lot["lifecycle"]) == name
            )
        )
        for name in lifecycle
    }
    return {
        "by_lifecycle": lifecycle,
        "by_reason": reason_buckets,
        "open_shares_by_lifecycle": open_lots,
        "false_exit_regret": [],
        "replacement_spread": {"20": [], "40": []},
    }
