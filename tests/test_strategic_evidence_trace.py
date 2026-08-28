from __future__ import annotations

import pytest

from research.strategic_evidence.intervention import StrategicOwnerIntervention
from research.strategic_evidence.replay import (
    ReplayRequest,
    ReplayResult,
    common_activation_date,
    common_activation_target_gross,
    reconcile_accounting,
    run_replay,
    validate_replay_accounting,
)
from research.strategic_evidence.trace import RouteTraceRow, first_divergence, strip_intervention_provenance
from uquant.engine import ProductionEngine


def test_replay_request_rejects_future_holdout_data() -> None:
    """Catches a replay request that permits data on or after the frozen holdout."""

    with pytest.raises(ValueError, match="future holdout"):
        ReplayRequest(
            symbols=("sz300308",),
            start="2026-08-05",
            end="2026-08-06",
            future_holdout_boundary="2026-08-06",
        )
    with pytest.raises(ValueError, match="immutable"):
        ReplayRequest(
            symbols=("sz300308",),
            start="2026-08-05",
            end="2026-08-05",
            future_holdout_boundary="2026-08-07",
        )


def test_replay_request_requires_reference_roles_to_be_declared_together() -> None:
    with pytest.raises(ValueError, match="reference roles must be supplied together"):
        ReplayRequest(
            symbols=("sz300394",),
            start="2026-01-05",
            end="2026-01-06",
            qualification_reference_symbols=("sz300394",),
        )


def test_first_divergence_uses_causal_layer_order_before_economic_state() -> None:
    """Catches a divergence reporter that sorts changed layers alphabetically."""

    left = RouteTraceRow(
        date="2026-01-05",
        reference_context={"breadth20": 0.7},
        leaders=({"symbol": "sz300308"},),
        risk={"state": "NORMAL"},
        opportunity="TREND",
        targets=({"symbol": "sz300308", "weight": 0.2},),
        orders=(),
        fills=(),
        account_sha256="a" * 64,
        equity=100.0,
        target_gross=0.2,
    )
    right = RouteTraceRow(
        date="2026-01-05",
        reference_context={"breadth20": 0.6},
        leaders=left.leaders,
        risk=left.risk,
        opportunity=left.opportunity,
        targets=left.targets,
        orders=left.orders,
        fills=left.fills,
        account_sha256="b" * 64,
        equity=99.0,
        target_gross=0.1,
    )

    divergence = first_divergence((left,), (right,))

    assert divergence is not None
    assert divergence.changed_layers == ("reference_context", "targets", "account", "equity")
    assert divergence.first_layer == "reference_context"


def test_accounting_reconciliation_rejects_a_mutated_equity_value() -> None:
    """Catches a replay result whose durable cash and marked positions do not equal equity."""

    with pytest.raises(ValueError, match="accounting"):
        reconcile_accounting(
            cash=80.0,
            position_shares={"sz300308": 1},
            close_marks={"sz300308": 20.0},
            equity=99.0,
        )


def test_final_accounting_rejects_fractional_durable_shares() -> None:
    """Catches lossy integer coercion in final durable-account reconciliation."""

    row = RouteTraceRow(
        date="2026-01-05",
        reference_context={},
        leaders=(),
        risk={},
        opportunity="TREND",
        targets=(),
        orders=(),
        fills=(),
        account_sha256="a" * 64,
        equity=100.0,
        cash=80.0,
        position_shares={"sz300308": 1},
        close_marks={"sz300308": 20.0},
    )
    result = ReplayResult(
        request=ReplayRequest(symbols=("sz300308",), start="2026-01-05", end="2026-01-06"),
        metrics={"final_equity": 100.0},
        trace=(row,),
        final_account={"cash": 80.0, "positions": {"sz300308": {"shares": 1.5}}},
        intervention_provenance=None,
    )
    with pytest.raises(ValueError, match="shares"):
        validate_replay_accounting(result)


def test_short_replay_is_preserved_as_insufficient_sample_row() -> None:
    """Catches a short required replay cell being raised and discarded."""

    result = run_replay(
        "data/frozen", ReplayRequest(symbols=("sz300308",), start="2023-01-03", end="2023-01-03")
    )
    assert result.status == "INSUFFICIENT_SAMPLE"
    assert result.metrics == {}


def test_official_loop_matches_production_and_same_owner_intervention_is_economically_exact() -> None:
    """Catches a shadow loop or intervention provenance leaking into economic trace state."""

    symbols = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
    baseline_request = ReplayRequest(symbols=symbols, start="2023-01-03", end="2023-01-10")
    baseline = run_replay("data/frozen", baseline_request)
    activation_date = common_activation_date(baseline)
    target_gross = common_activation_target_gross(baseline)
    production = ProductionEngine("data/frozen").backtest(
        symbols=symbols, start="2023-01-03", end="2023-01-10"
    )
    forced = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=symbols,
            start="2023-01-03",
            end="2023-01-10",
            scenario="forced-sz300308-common-date",
            intervention_date=activation_date,
        ),
        intervention=StrategicOwnerIntervention(owner="sz300308", target_gross=target_gross),
    )
    assert baseline.metrics["total_return"] == production["total_return"]
    assert baseline.metrics["max_drawdown"] == production["max_drawdown"]
    validate_replay_accounting(baseline)
    assert activation_date == "2023-01-04"
    assert target_gross == 0.95
    assert (
        first_divergence(
            strip_intervention_provenance(baseline.trace), strip_intervention_provenance(forced.trace)
        )
        is None
    )


def test_alternate_owner_survives_activation_and_reaches_next_open_execution() -> None:
    """Catches a forced owner that production activation silently overwrites."""

    symbols = ("sz300308", "sz300502", "sz300394", "sh688008", "sh603986")
    baseline = run_replay("data/frozen", ReplayRequest(symbols=symbols, start="2023-01-03", end="2023-01-06"))
    forced = run_replay(
        "data/frozen",
        ReplayRequest(
            symbols=symbols,
            start="2023-01-03",
            end="2023-01-06",
            intervention_date=common_activation_date(baseline),
        ),
        intervention=StrategicOwnerIntervention(
            owner="sz300502", target_gross=common_activation_target_gross(baseline)
        ),
    )
    assert forced.trace[1].targets[0]["symbol"] == "sz300502"
    assert forced.trace[2].fills[0]["symbol"] == "sz300502"
