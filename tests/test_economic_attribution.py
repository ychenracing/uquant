from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd
import pytest

from uquant.config import DEFAULT_CONFIG
from uquant.engine import ProductionEngine, code_fingerprint
from uquant.types import AccountState, Fill, Position, Tranche


def _identity(
    *,
    event: str,
    subsystem: str,
    mechanism: str,
    origin_lifecycle: str,
    industry: str,
    replaces_symbol: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": f"evt_{event * 64}",
        "origin_subsystem": subsystem,
        "mechanism": mechanism,
        "origin_lifecycle": origin_lifecycle,
        "replaces_symbol": replaces_symbol,
        "industry_at_entry": industry,
        "industry_manifest_sha256": "a" * 64,
    }


def _account_with_realized_and_open_lots() -> AccountState:
    leader = _identity(
        event="1",
        subsystem="LEADER",
        mechanism="LEADER_ROTATION",
        origin_lifecycle="CORE",
        industry="optical",
        replaces_symbol="old_leader",
    )
    recovery = _identity(
        event="2",
        subsystem="RECOVERY",
        mechanism="RECOVERY_COHORT",
        origin_lifecycle="RECOVERY",
        industry="storage",
    )
    sold = {
        "tranche_id": "2025-01-02:leader:1",
        "shares": 4,
        "cost": 10.11,
        "unit_cost": 10.11,
        "avg_cost": 10.11,
        "cost_basis": 40.44,
        "lifecycle": "ADD1",
        "entry_date": "2025-01-02",
        "commission": 0.6,
        "stamp_duty": 0.3,
        "transfer_fee": 0.06,
        "slippage_cost": 0.4,
        "fees": 0.96,
        "transaction_costs": 1.36,
        **leader,
    }
    account = AccountState.empty(1_000.0)
    account.cash = 856.84
    account.fills = [
        Fill(
            signal_date="2025-01-01",
            fill_date="2025-01-02",
            symbol="leader",
            side="BUY",
            shares=10,
            price=10.0,
            gross_value=100.0,
            commission=1.0,
            stamp_duty=0.0,
            transfer_fee=0.1,
            slippage_cost=2.0,
            reason="display prose is irrelevant",
            lifecycle="CORE",
            **leader,
        ),
        Fill(
            signal_date="2025-01-02",
            fill_date="2025-01-03",
            symbol="recovery",
            side="BUY",
            shares=5,
            price=20.0,
            gross_value=100.0,
            commission=1.0,
            stamp_duty=0.0,
            transfer_fee=0.1,
            slippage_cost=1.0,
            reason="more display prose",
            lifecycle="RECOVERY",
            **recovery,
        ),
        Fill(
            signal_date="2025-01-03",
            fill_date="2025-01-04",
            symbol="leader",
            side="SELL",
            shares=4,
            price=15.0,
            gross_value=60.0,
            commission=0.6,
            stamp_duty=0.3,
            transfer_fee=0.06,
            slippage_cost=0.4,
            reason="changed prose must not change ownership",
            lifecycle="ADD1",
            sold_tranches=[sold],
            **_identity(
                event="3",
                subsystem="RISK",
                mechanism="RISK_GROSS_CAP",
                origin_lifecycle="ADD1",
                industry="optical",
            ),
        ),
    ]
    account.positions = {
        "leader": Position(
            symbol="leader",
            shares=6,
            avg_cost=10.11,
            entry_date="2025-01-02",
            highest_close=15.0,
            lifecycle="ADD1",
            tranches=[
                Tranche(
                    tranche_id="2025-01-02:leader:1",
                    lifecycle="ADD1",
                    shares=6,
                    avg_cost=10.11,
                    entry_date="2025-01-02",
                    sellable_date="2025-01-03",
                    highest_close=15.0,
                    **leader,
                )
            ],
        ),
        "recovery": Position(
            symbol="recovery",
            shares=5,
            avg_cost=20.22,
            entry_date="2025-01-03",
            highest_close=20.0,
            lifecycle="RECOVERY",
            tranches=[
                Tranche(
                    tranche_id="2025-01-03:recovery:1",
                    lifecycle="RECOVERY",
                    shares=5,
                    avg_cost=20.22,
                    entry_date="2025-01-03",
                    sellable_date="2025-01-04",
                    highest_close=20.0,
                    **recovery,
                )
            ],
        ),
    }
    return account


def test_lot_accounting_reconciles_realized_open_and_all_execution_costs() -> None:
    """Catches collapsing sold lots to an exit event or double-counting slippage."""
    try:
        from uquant.attribution import build_economic_attribution
    except ModuleNotFoundError:
        pytest.fail("canonical economic attribution module is missing")

    result = build_economic_attribution(
        account=_account_with_realized_and_open_lots(),
        final_prices={"leader": 12.0, "recovery": 18.0},
        sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
        economic_start="2025-01-02",
        economic_end="2025-01-05",
        final_equity=1_018.84,
    )

    assert result["accounting"] == {
        "realized_pnl": pytest.approx(18.6),
        "open_pnl": pytest.approx(0.24),
        "total_pnl": pytest.approx(18.84),
        "expected_pnl": pytest.approx(18.84),
        "reconciliation_error": pytest.approx(0.0, abs=1e-12),
        "tolerance": 1e-6,
        "reconciled": True,
    }
    assert result["costs"] == {
        "commission": pytest.approx(2.6),
        "stamp_duty": pytest.approx(0.3),
        "transfer_fee": pytest.approx(0.26),
        "cash_fees": pytest.approx(3.16),
        "slippage": pytest.approx(3.4),
        "all_in": pytest.approx(6.56),
        "pre_all_in_cost_pnl": pytest.approx(25.4),
        "all_in_cost_drag_initial_cash": pytest.approx(0.00656),
        "slippage_accounting": "embedded_in_execution_price_not_double_subtracted",
    }
    assert result["by_symbol"]["leader"]["realized_pnl"] == pytest.approx(18.6)
    assert result["by_symbol"]["leader"]["open_pnl"] == pytest.approx(11.34)
    assert result["by_symbol"]["recovery"]["open_pnl"] == pytest.approx(-11.1)
    assert [row["economic_status"] for row in result["lots"]] == [
        "REALIZED",
        "OPEN",
        "OPEN",
    ]
    sold = result["lots"][0]
    assert sold["origin_subsystem"] == "LEADER"
    assert sold["origin_mechanism"] == "LEADER_ROTATION"
    assert sold["origin_lifecycle"] == "CORE"
    assert sold["current_lifecycle"] == "ADD1"
    assert sold["exit_subsystem"] == "RISK"
    assert sold["replaces_symbol"] == "old_leader"
    assert sold["industry_at_entry"] == "optical"


def test_contribution_denominators_define_positive_signed_and_absolute_edge_cases() -> None:
    """Catches signed cancellation or fabricated ratios for zero/negative denominators."""
    from uquant.attribution import contribution_concentration

    result = contribution_concentration({"a": 6.0, "b": 3.0, "c": 1.0, "d": -2.0})

    assert result["denominators"] == {"positive": 10.0, "signed_net": 8.0, "absolute": 12.0}
    assert result["positive"] == {
        "status": "DEFINED",
        "top1": pytest.approx(0.6),
        "top3": pytest.approx(1.0),
        "hhi": pytest.approx(0.46),
    }
    assert result["signed"]["status"] == "DEFINED"
    assert result["signed"]["contributions"] == pytest.approx(
        {"a": 0.75, "b": 0.375, "c": 0.125, "d": -0.25}
    )
    assert result["absolute"]["status"] == "DEFINED"
    assert result["absolute"]["hhi"] == pytest.approx(50.0 / 144.0)

    losing = contribution_concentration({"a": -2.0, "b": -1.0})
    assert losing["positive"] == {
        "status": "UNDEFINED_NO_POSITIVE_PNL",
        "top1": None,
        "top3": None,
        "hhi": None,
    }
    assert losing["signed"] == {
        "status": "UNDEFINED_NONPOSITIVE_NET_PNL",
        "contributions": None,
    }
    assert losing["absolute"]["hhi"] == pytest.approx(5.0 / 9.0)

    zero = contribution_concentration({"a": 0.0})
    assert zero["positive"]["status"] == "UNDEFINED_NO_POSITIVE_PNL"
    assert zero["signed"]["status"] == "UNDEFINED_NONPOSITIVE_NET_PNL"
    assert zero["absolute"] == {
        "status": "UNDEFINED_ZERO_ABSOLUTE_PNL",
        "contributions": None,
        "hhi": None,
    }


def test_attribution_groups_machine_identity_holding_sessions_replacement_and_turnover() -> None:
    """Catches grouping by prose, calendar days, or the SELL event identity."""
    from uquant.attribution import build_economic_attribution

    result = build_economic_attribution(
        account=_account_with_realized_and_open_lots(),
        final_prices={"leader": 12.0, "recovery": 18.0},
        sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
        economic_start="2025-01-02",
        economic_end="2025-01-05",
        final_equity=1_018.84,
    )

    assert result["by_industry"]["optical"]["total_pnl"] == pytest.approx(29.94)
    assert result["by_industry"]["storage"]["total_pnl"] == pytest.approx(-11.1)
    assert result["by_origin_lifecycle"]["CORE"]["total_pnl"] == pytest.approx(29.94)
    assert result["by_current_lifecycle"]["ADD1"]["total_pnl"] == pytest.approx(29.94)
    assert result["by_origin_subsystem"]["LEADER"]["total_pnl"] == pytest.approx(29.94)
    assert result["by_origin_subsystem"]["RECOVERY"]["total_pnl"] == pytest.approx(-11.1)
    assert result["by_origin_subsystem"]["STRATEGIC"]["total_pnl"] == 0.0
    assert result["by_mechanism"]["LEADER_ROTATION"]["total_pnl"] == pytest.approx(29.94)
    assert result["by_mechanism"]["STRATEGIC_COHORT"]["total_pnl"] == 0.0
    assert result["by_exit_subsystem"]["RISK"]["total_pnl"] == pytest.approx(18.6)
    assert result["by_exit_mechanism"]["RISK_GROSS_CAP"]["total_pnl"] == pytest.approx(18.6)
    assert result["by_exit_mechanism"]["LEADER_ROTATION"]["total_pnl"] == 0.0
    assert result["by_origin_lifecycle"]["ADD2"]["total_pnl"] == 0.0
    assert result["by_current_lifecycle"]["SATELLITE"]["total_pnl"] == 0.0
    assert result["replacements"] == {
        "linked_lot_count": 2,
        "realized_pnl": pytest.approx(18.6),
        "open_pnl": pytest.approx(11.34),
        "total_pnl": pytest.approx(29.94),
        "by_replaced_symbol": {
            "old_leader": {
                "realized_pnl": pytest.approx(18.6),
                "open_pnl": pytest.approx(11.34),
                "total_pnl": pytest.approx(29.94),
            }
        },
    }
    assert result["turnover"] == {
        "definition": "sum(fill.gross_value) / initial_cash",
        "gross_transaction_value": 260.0,
        "gross_turnover": pytest.approx(0.26),
    }
    assert result["holding_period_sessions"]["definition"] == (
        "zero-based distance between entry and exit/final session, share-weighted"
    )
    assert result["holding_period_sessions"]["all"]["weighted_average"] == pytest.approx(2.4)
    assert result["holding_period_sessions"]["realized"]["weighted_average"] == pytest.approx(2.0)
    assert result["holding_period_sessions"]["open"]["weighted_average"] == pytest.approx(28 / 11)
    assert result["symbol_concentration"] == result["concentration"]
    assert result["industry_concentration"]["positive"]["top1"] == pytest.approx(1.0)

    realized = result["lots"][0]
    assert realized["costs"] == {
        "entry_commission": pytest.approx(0.4),
        "entry_stamp_duty": 0.0,
        "entry_transfer_fee": pytest.approx(0.04),
        "entry_slippage": pytest.approx(0.8),
        "exit_commission": pytest.approx(0.6),
        "exit_stamp_duty": pytest.approx(0.3),
        "exit_transfer_fee": pytest.approx(0.06),
        "exit_slippage": pytest.approx(0.4),
        "cash_fees": pytest.approx(1.4),
        "slippage": pytest.approx(1.2),
        "all_in": pytest.approx(2.6),
    }
    assert sum(float(lot["costs"]["all_in"]) for lot in result["lots"]) == pytest.approx(6.56)
    assert result["by_mechanism"]["LEADER_ROTATION"]["all_in_costs"] == pytest.approx(4.46)


def test_sell_lot_cost_components_must_allocate_the_fill_exactly() -> None:
    """Catches a per-lot rounding residual being silently dropped or reassigned."""
    from uquant.attribution import build_economic_attribution

    account = _account_with_realized_and_open_lots()
    account.fills[-1].sold_tranches[0]["commission"] = 0.59
    with pytest.raises(ValueError, match="sold-lot commission does not reconcile"):
        build_economic_attribution(
            account=account,
            final_prices={"leader": 12.0, "recovery": 18.0},
            sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
            economic_start="2025-01-02",
            economic_end="2025-01-05",
            final_equity=1_018.84,
        )


def test_economic_attribution_builder_rejects_fractional_sold_lot_shares() -> None:
    """Catches silently truncating a fractional sold-lot allocation to an integer."""
    from uquant.attribution import build_economic_attribution

    account = _account_with_realized_and_open_lots()
    account.fills[-1].sold_tranches[0]["shares"] = 4.5
    with pytest.raises(ValueError, match="sold-lot shares must be a positive integer"):
        build_economic_attribution(
            account=account,
            final_prices={"leader": 12.0, "recovery": 18.0},
            sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
            economic_start="2025-01-02",
            economic_end="2025-01-05",
            final_equity=1_018.84,
        )


def test_raw_attribution_validator_rejects_fractional_sold_lot_shares() -> None:
    """Catches raw account evidence laundering 4.5 shares through ``int(4.5)``."""
    from uquant.attribution import (
        build_economic_attribution,
        validate_attribution_against_engine_result,
    )

    account = _account_with_realized_and_open_lots()
    sessions = ("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05")
    states = (
        (898.9, {"leader": 100.0}),
        (797.8, {"leader": 100.0, "recovery": 100.0}),
        (856.84, {"leader": 90.0, "recovery": 100.0}),
        (856.84, {"leader": 72.0, "recovery": 90.0}),
    )
    prior_equity = 1_000.0
    ledger: list[dict[str, Any]] = []
    for session, (cash, position_values) in zip(sessions, states, strict=True):
        equity = cash + sum(position_values.values())
        ledger.append(
            {
                "date": session,
                "cash": cash,
                "equity": equity,
                "gross_exposure": sum(position_values.values()) / equity,
                "net_exposure": sum(position_values.values()) / equity,
                "cash_weight": cash / equity,
                "position_weights": {
                    symbol: value / equity for symbol, value in position_values.items()
                },
                "daily_pnl": equity - prior_equity,
                "target_weights": {},
                "target_gross": 0.0,
                "caps": {"risk_gross": 1.0, "system_gross": 1.0},
                "binding_owner": "STRATEGY",
                "risk_state": "NORMAL",
                "opportunity": "CHOPPY",
            }
        )
        prior_equity = equity
    attribution = build_economic_attribution(
        account=account,
        final_prices={"leader": 12.0, "recovery": 18.0},
        sessions=sessions,
        economic_start=sessions[0],
        economic_end=sessions[-1],
        final_equity=1_018.84,
        daily_ledger=ledger,
        benchmark_close={session: 100.0 for session in sessions},
    )
    raw = {
        "attribution": attribution,
        "final_account": account.to_dict(),
        "final_equity": 1_018.84,
        "final_wealth": 1.01884,
        "start": sessions[0],
        "end": sessions[-1],
        "gross_turnover": 0.26,
        "symbol_pnl": {"leader": 29.94, "recovery": -11.1},
    }
    raw["final_account"]["fills"][-1]["sold_tranches"][0]["shares"] = 4.5

    with pytest.raises(ValueError, match="engine sold tranche shares must be a positive integer"):
        validate_attribution_against_engine_result(
            raw,
            economic_start=sessions[0],
            economic_end=sessions[-1],
        )


def test_sell_without_per_lot_allocations_and_pre_interval_lot_fail_closed() -> None:
    """Catches FIFO reconstruction or warm-up inventory entering economic attribution."""
    from uquant.attribution import build_economic_attribution

    missing = _account_with_realized_and_open_lots()
    missing.fills[-1].sold_tranches = []
    with pytest.raises(ValueError, match="per-lot sold_tranches"):
        build_economic_attribution(
            account=missing,
            final_prices={"leader": 12.0, "recovery": 18.0},
            sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
            economic_start="2025-01-02",
            economic_end="2025-01-05",
            final_equity=1_018.84,
        )

    warmup = _account_with_realized_and_open_lots()
    warmup.positions["leader"].tranches[0].entry_date = "2022-12-30"
    with pytest.raises(ValueError, match="outside the exact economic interval"):
        build_economic_attribution(
            account=warmup,
            final_prices={"leader": 12.0, "recovery": 18.0},
            sessions=("2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"),
            economic_start="2025-01-02",
            economic_end="2025-01-05",
            final_equity=1_018.84,
        )


def test_post_exit_diagnostics_never_read_after_economic_end() -> None:
    """Catches a horizon consuming a future or holdout price outside the replay."""
    from uquant.attribution import ExitRecord, post_exit_diagnostics

    index = pd.to_datetime(
        ["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]
    )
    prices = {"leader": pd.Series([10.0, 11.0, 12.0, 999.0, 1_000.0], index=index)}
    record = ExitRecord(
        symbol="leader",
        exit_date="2025-01-03",
        exit_price=11.0,
        origin_subsystem="LEADER",
        mechanism="LEADER_ROTATION",
    )

    first = post_exit_diagnostics(
        exits=(record,),
        prices=prices,
        economic_end="2025-01-06",
        horizons=(1, 2),
    )
    prices["leader"].loc[pd.Timestamp("2025-01-07")] = 2.0
    second = post_exit_diagnostics(
        exits=(record,),
        prices=prices,
        economic_end="2025-01-06",
        horizons=(1, 2),
    )

    assert first == second
    assert first[0]["horizons"] == {
        "1": {
            "absolute_return": pytest.approx(12.0 / 11.0 - 1.0),
            "avoided_loss": 0.0,
            "regret": pytest.approx(12.0 / 11.0 - 1.0),
        },
        "2": None,
    }
    assert first[0]["economic_end"] == "2025-01-06"


def test_daily_ledger_is_same_day_causal_and_labels_diagnostics_truthfully() -> None:
    """Catches a later-state cap owner or cash/counterfactual effect labeled realized PnL."""
    from uquant.attribution import attribution_diagnostics, build_daily_ledger_row

    account = AccountState.empty(1_000.0)
    account.cash = 400.0
    account.positions = {"leader": Position("leader", shares=20, avg_cost=25.0)}
    row = build_daily_ledger_row(
        date="2025-01-03",
        account=account,
        close_prices={"leader": 30.0},
        previous_equity=950.0,
        target_weights={"leader": 0.5},
        target_gross=0.5,
        risk_gross_cap=0.5,
        system_gross_cap=0.9,
        risk_state="CAUTION",
        opportunity="TREND",
    )

    assert row == {
        "date": "2025-01-03",
        "cash": 400.0,
        "equity": 1_000.0,
        "gross_exposure": pytest.approx(0.6),
        "net_exposure": pytest.approx(0.6),
        "cash_weight": pytest.approx(0.4),
        "position_weights": {"leader": pytest.approx(0.6)},
        "daily_pnl": 50.0,
        "target_weights": {"leader": 0.5},
        "target_gross": 0.5,
        "caps": {"risk_gross": 0.5, "system_gross": 0.9},
        "binding_owner": "RISK",
        "risk_state": "CAUTION",
        "opportunity": "TREND",
    }

    ledger = (
        {**row, "date": "2025-01-02", "cash": 400.0, "equity": 950.0, "daily_pnl": -50.0},
        {**row, "date": "2025-01-03", "cash": 300.0, "equity": 1_100.0, "daily_pnl": 150.0},
        {**row, "date": "2025-01-06", "cash": 200.0, "equity": 1_050.0, "daily_pnl": -50.0},
    )
    diagnostic = attribution_diagnostics(
        daily_ledger=ledger,
        benchmark_close={"2025-01-02": 100.0, "2025-01-03": 110.0, "2025-01-06": 99.0},
    )
    assert diagnostic["cash_drag"] == {
        "status": "DIAGNOSTIC",
        "value": pytest.approx(-10.0),
        "definition": "negative prior-close cash times next-session benchmark return",
        "is_accounting_pnl": False,
    }
    assert diagnostic["risk_avoidance"] == {
        "status": "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL",
        "value": None,
        "is_accounting_pnl": False,
    }
    paired = attribution_diagnostics(
        daily_ledger=ledger,
        benchmark_close={"2025-01-02": 100.0, "2025-01-03": 110.0, "2025-01-06": 99.0},
        paired_counterfactual_equity={
            "2025-01-02": 950.0,
            "2025-01-03": 1_000.0,
            "2025-01-06": 900.0,
        },
    )
    assert paired["risk_avoidance"] == {
        "status": "PAIRED_COUNTERFACTUAL",
        "value": 150.0,
        "definition": "actual final equity minus paired counterfactual final equity",
        "is_accounting_pnl": False,
    }


def test_production_backtest_attaches_reconciled_attribution_and_complete_daily_ledger() -> None:
    """Catches a research-only calculator or daily ledger detached from production replay."""
    result = ProductionEngine("data/frozen").backtest(
        symbols=("sz300308", "sz300502", "sz300394"),
        start="2023-01-03",
        end="2023-03-31",
    )

    attribution = result["attribution"]
    ledger = attribution["daily_ledger"]
    assert attribution["schema"] == "uquant.economic-attribution.v1"
    assert attribution["interval"] == {
        "economic_start": "2023-01-03",
        "economic_end": "2023-03-31",
    }
    assert attribution["accounting"]["reconciled"] is True
    assert attribution["accounting"]["expected_pnl"] == pytest.approx(
        result["final_equity"] - result["final_account"]["initial_cash"]
    )
    assert [row["date"] for row in ledger] == [row["date"] for row in result["equity_curve"]]
    replay_evidence = result["daily_replay_evidence"]
    assert [row["date"] for row in replay_evidence] == [
        row["date"] for row in result["equity_curve"]
    ]
    assert all(
        set(row) == {"date", "cash", "position_shares", "close_marks"}
        for row in replay_evidence
    )
    decision_trace = result["decision_trace"]
    assert [row["date"] for row in decision_trace] == [
        row["date"] for row in result["equity_curve"]
    ]
    assert result["decision_digests"] == [
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for row in decision_trace
    ]
    legacy_payloads = [
        {
            "date": row["date"],
            "opportunity": row["opportunity"],
            "risk": row["risk"]["state"],
            "targets": [
                {
                    name: target[name]
                    for name in (
                        "symbol",
                        "weight",
                        "lifecycle",
                        "reduction_policy",
                        "reason_code",
                        "exit_kind",
                    )
                }
                for target in row["targets"]
            ],
            "orders": [
                {
                    name: order[name]
                    for name in (
                        "order_id",
                        "symbol",
                        "side",
                        "target_weight",
                        "reduction_policy",
                        "reason_code",
                        "exit_kind",
                    )
                }
                for order in row["orders"]
            ],
        }
        for row in decision_trace
    ]
    assert result["legacy_decision_digests"] == [
        hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for row in legacy_payloads
    ]
    assert sum(float(row["daily_pnl"]) for row in ledger) == pytest.approx(
        attribution["accounting"]["total_pnl"]
    )
    assert all("2023-01-03" <= row["date"] <= "2023-03-31" for row in ledger)
    assert all(
        set(row)
        == {
            "date",
            "cash",
            "equity",
            "gross_exposure",
            "net_exposure",
            "cash_weight",
            "position_weights",
            "daily_pnl",
            "target_weights",
            "target_gross",
            "caps",
            "binding_owner",
            "risk_state",
            "opportunity",
        }
        for row in ledger
    )
    assert attribution["diagnostics"]["cash_drag"]["status"] == "DIAGNOSTIC"
    assert attribution["diagnostics"]["cash_drag"]["is_accounting_pnl"] is False
    assert attribution["diagnostics"]["risk_avoidance"] == {
        "status": "NOT_EVALUATED_REQUIRES_PAIRED_COUNTERFACTUAL",
        "value": None,
        "is_accounting_pnl": False,
    }


def test_control_plane_rejects_digest_schema_code_and_event_identity_tamper() -> None:
    """Catches v2 control evidence disappearing inside the frozen-v1 projection."""
    from uquant.validation.control_plane import validate_engine_control_plane
    from uquant.validation.manifest import verify_data_manifest
    from uquant.validation.replay_evidence import VerifiedMarketData

    result = ProductionEngine("data/frozen").backtest(
        symbols=("sz300308", "sz300502", "sz300394"),
        start="2023-01-03",
        end="2023-03-31",
    )
    market = VerifiedMarketData(
        "data/frozen",
        expected_manifest=verify_data_manifest("data/frozen"),
    )

    def validate(candidate: dict[str, Any]) -> None:
        validate_engine_control_plane(
            candidate,
            economic_start="2023-01-03",
            economic_end="2023-03-31",
            expected_sessions=market.sessions("2023-01-03", "2023-03-31"),
            expected_config=DEFAULT_CONFIG,
            expected_code_sha256=code_fingerprint(),
        )

    validate(result)
    mutations: list[tuple[str, Any]] = [
        (
            "decision digest",
            lambda candidate: candidate["decision_digests"].__setitem__(0, "0" * 64),
        ),
        (
            "account schema",
            lambda candidate: candidate["final_account"].__setitem__("schema_version", 999),
        ),
        (
            "account code hash",
            lambda candidate: candidate["final_account"].__setitem__("code_hash", "0" * 64),
        ),
        (
            "event identity",
            lambda candidate: candidate["final_account"]["fills"][0].__setitem__(
                "event_id", "evt_" + "0" * 64
            ),
        ),
    ]
    assert result["final_account"]["fills"]
    for message, mutate in mutations:
        changed = json.loads(json.dumps(result))
        mutate(changed)
        with pytest.raises(ValueError, match=message):
            validate(changed)


def test_control_plane_accepts_only_the_exact_twelve_decimal_sum_rounding_bound() -> None:
    """Catches valid multi-target rounding drift or a material target-gross forgery."""
    from uquant.validation.control_plane import _rounded_sum_matches

    weights = (0.277841961388, 0.36715709618, 0.348767640071)
    rounded_total = 0.99376669764

    assert _rounded_sum_matches(rounded_total, weights)
    assert not _rounded_sum_matches(rounded_total + 1e-10, weights)


def test_control_plane_rejects_self_signed_noncanonical_target_identity() -> None:
    """Catches a trace-only target fabricating identity while re-signing its digest."""
    from uquant.validation.control_plane import validate_engine_control_plane
    from uquant.validation.manifest import verify_data_manifest
    from uquant.validation.replay_evidence import VerifiedMarketData

    result = ProductionEngine("data/frozen").backtest(
        symbols=("sz300308", "sz300502", "sz300394"),
        start="2023-01-03",
        end="2023-03-31",
    )
    market = VerifiedMarketData(
        "data/frozen",
        expected_manifest=verify_data_manifest("data/frozen"),
    )
    trace_index = next(
        index
        for index, row in enumerate(result["decision_trace"])
        if any(
            target["origin_subsystem"] == "STRATEGIC"
            and target["mechanism"] != "STRATEGIC_RESTORATION"
            for target in row["targets"]
        )
    )

    for field, forged_identity in (
        ("event_id", "evt_" + "0" * 64),
        ("grant_id", "grant_" + "0" * 64),
        ("epoch_id", "epoch_" + "0" * 64),
    ):
        changed = json.loads(json.dumps(result))
        trace = changed["decision_trace"][trace_index]
        target = next(
            target
            for target in trace["targets"]
            if target["origin_subsystem"] == "STRATEGIC"
            and target["mechanism"] != "STRATEGIC_RESTORATION"
        )
        target[field] = forged_identity
        changed["decision_digests"][trace_index] = hashlib.sha256(
            json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        with pytest.raises(ValueError, match="target event identity"):
            validate_engine_control_plane(
                changed,
                economic_start="2023-01-03",
                economic_end="2023-03-31",
                expected_sessions=market.sessions("2023-01-03", "2023-03-31"),
                expected_config=DEFAULT_CONFIG,
                expected_code_sha256=code_fingerprint(),
            )
