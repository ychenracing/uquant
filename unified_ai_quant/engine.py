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
from .execution import ExecutionPlanner, merge_pending_orders, plan_orders
from .features import compute_features
from .leader import REFERENCE_UNIVERSE, compute_leaders
from .opportunity import classify_opportunity
from .portfolio import PortfolioAllocator, current_weights
from .risk import assess_risk
from .types import AccountState, Decision, Fill, LeaderScore

INDEX_SYMBOLS = ("sh000300", "sh000682")


def code_fingerprint() -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ProductionEngine:
    def __init__(self, data_dir: str | Path, cfg: SystemConfig = DEFAULT_CONFIG) -> None:
        self.cfg = cfg
        self.data = DataStore(data_dir)
        self.execution = ExecutionPlanner(cfg)
        self.allocator = PortfolioAllocator(cfg)
        self._raw: dict[str, pd.DataFrame] = {}
        self._features: dict[str, pd.DataFrame] = {}
        self._manifest_cache: dict[tuple[str, ...], str] = {}
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
            raise ValueError("at least one AI-chain symbol is required")
        all_symbols = set(user_symbols) | set(REFERENCE_UNIVERSE) | set(INDEX_SYMBOLS)
        self._load(all_symbols)
        date = pd.Timestamp(as_of).normalize()
        if date not in self._features["sh000300"].index or date not in self._features["sh000682"].index:
            raise RuntimeError("decision date is not a common index session")
        manifest_key = tuple(sorted(all_symbols))
        if manifest_key not in self._manifest_cache:
            self._manifest_cache[manifest_key] = self.data.manifest(all_symbols).digest
        data_digest = self._manifest_cache[manifest_key]
        if account.last_successful_run and pd.Timestamp(account.last_successful_run) > date:
            raise RuntimeError("account risk state comes from a future date")
        if account.data_hash and account.data_hash != data_digest and self.cfg.fail_closed:
            raise RuntimeError("frozen data hash differs from account state")
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
        orders = merge_pending_orders(
            retained=account.pending_orders,
            planned=planned_orders,
            targets=targets,
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
                {"symbol": item.symbol, "side": item.side, "target_weight": round(item.target_weight, 12)}
                for item in orders
            ],
        }
        digest = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        account.last_successful_run = str(date.date())
        account.data_hash = data_digest
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
            initial_cash=account.initial_cash,
            risk_events=account.risk_events,
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
    initial_cash: float,
    risk_events: list[dict[str, Any]],
) -> dict[str, Any]:
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
    order_keys = {(item.fill_date, item.symbol, item.side) for item in fills}
    fees = sum(item.commission + item.stamp_duty + item.transfer_fee for item in fills)
    holding_days: list[int] = []
    buys: dict[str, list[pd.Timestamp]] = {}
    for fill in fills:
        if fill.side == "BUY":
            buys.setdefault(fill.symbol, []).append(pd.Timestamp(fill.fill_date))
        elif buys.get(fill.symbol):
            holding_days.append((pd.Timestamp(fill.fill_date) - buys[fill.symbol].pop(0)).days)
    rolling20 = equity.pct_change(20, fill_method=None)
    rolling60 = equity.pct_change(60, fill_method=None)
    return {
        "total_return": total_return,
        "cagr": cagr,
        "excess_return": total_return,
        "sharpe": sharpe,
        "calmar": cagr / max_dd if max_dd > 1e-12 else 0.0,
        **dd,
        "worst_20d": float(rolling20.min()) if rolling20.notna().any() else 0.0,
        "worst_60d": float(rolling60.min()) if rolling60.notna().any() else 0.0,
        "account_orders": len(order_keys),
        "round_trips": sum(item.side == "SELL" for item in fills),
        "gross_turnover": gross_turnover,
        "annual_turnover": gross_turnover / years,
        "median_holding_days": float(median(holding_days)) if holding_days else 0.0,
        "fees": fees,
        "slippage_cost": sum(item.slippage_cost for item in fills),
        "risk_events": risk_events,
        "equity_curve": [{"date": str(date.date()), "equity": value} for date, value in equity.items()],
    }


def attribution(fills: list[Fill]) -> dict[str, Any]:
    lifecycle: dict[str, dict[str, float | int]] = {}
    for fill in fills:
        bucket = lifecycle.setdefault(fill.lifecycle.lower(), {"fills": 0, "gross_value": 0.0, "fees": 0.0})
        bucket["fills"] = int(bucket["fills"]) + 1
        bucket["gross_value"] = float(bucket["gross_value"]) + fill.gross_value
        bucket["fees"] = float(bucket["fees"]) + fill.commission + fill.stamp_duty + fill.transfer_fee
    return {"by_lifecycle": lifecycle}
