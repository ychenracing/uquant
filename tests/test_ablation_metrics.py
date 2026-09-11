"""Current ablation arithmetic, causal alignment, and complete evidence coverage."""
from __future__ import annotations

import pytest

from research.ablation import (
    AblationCell,
    AblationMetrics,
    DecisionPoint,
    aggregate_dimensions,
    compare_cells,
    first_decision_divergence,
    validate_complete_coverage,
)


def _metrics(
    *,
    wealth: float,
    drawdown: float,
    orders: int,
    acute: float | None,
    turnover: float,
    top1: float,
    top3: float,
    hhi: float,
) -> AblationMetrics:
    return AblationMetrics(
        final_wealth=wealth,
        max_drawdown=drawdown,
        account_orders=orders,
        acute_return=acute,
        gross_turnover=turnover,
        annual_turnover=turnover * 2.0,
        top1_concentration=top1,
        top3_concentration=top3,
        pnl_hhi=hhi,
    )


def test_comparison_emits_every_raw_materiality_dimension_without_classification() -> None:
    """Catches dropped materiality inputs or premature KEEP/DELETE classification."""
    baseline = AblationCell(
        contract="performance",
        cell_id="a/h1_2023",
        status="VALID",
        metrics=_metrics(
            wealth=2.0,
            drawdown=0.20,
            orders=10,
            acute=-0.05,
            turnover=1.0,
            top1=0.7,
            top3=0.9,
            hhi=0.5,
        ),
    )
    variant = AblationCell(
        contract="performance",
        cell_id="a/h1_2023",
        status="VALID",
        metrics=_metrics(
            wealth=1.9,
            drawdown=0.18,
            orders=8,
            acute=-0.03,
            turnover=0.8,
            top1=0.6,
            top3=0.8,
            hhi=0.4,
        ),
    )

    delta = compare_cells(baseline, variant)

    assert delta.final_wealth == pytest.approx(-0.1)
    assert delta.max_drawdown == pytest.approx(-0.02)
    assert delta.account_orders == -2
    assert delta.acute_return == pytest.approx(0.02)
    assert delta.gross_turnover == pytest.approx(-0.2)
    assert delta.annual_turnover == pytest.approx(-0.4)
    assert delta.top1_concentration == pytest.approx(-0.1)
    assert delta.top3_concentration == pytest.approx(-0.1)
    assert delta.pnl_hhi == pytest.approx(-0.1)
    assert "classification" not in delta.to_dict()
    assert "decision" not in delta.to_dict()


def test_aggregate_dimensions_preserve_tail_generalization_inputs() -> None:
    """Catches mean-only summaries that hide tail wealth, drawdown, or concentration."""
    cells = tuple(
        AblationCell(
            contract="ai_era_generalization",
            cell_id=f"h1_2023/random__05__000{index}",
            status="VALID",
            metrics=_metrics(
                wealth=wealth,
                drawdown=drawdown,
                orders=orders,
                acute=None,
                turnover=turnover,
                top1=top1,
                top3=min(1.0, top1 + 0.2),
                hhi=top1 / 2.0,
            ),
        )
        for index, (wealth, drawdown, orders, turnover, top1) in enumerate(
            (
                (0.8, 0.30, 20, 2.0, 0.8),
                (1.0, 0.20, 10, 1.0, 0.6),
                (1.2, 0.10, 5, 0.5, 0.4),
            )
        )
    )

    aggregate = aggregate_dimensions(cells)

    assert aggregate["p10_final_wealth"] == pytest.approx(0.84)
    assert aggregate["p90_max_drawdown"] == pytest.approx(0.28)
    assert aggregate["p90_account_orders"] == pytest.approx(18.0)
    assert aggregate["p90_gross_turnover"] == pytest.approx(1.8)
    assert aggregate["worst_top1_concentration"] == pytest.approx(0.8)
    assert aggregate["worst_top3_concentration"] == pytest.approx(1.0)
    assert aggregate["worst_pnl_hhi"] == pytest.approx(0.4)


def test_required_first_divergence_rejects_identical_or_misaligned_runs() -> None:
    """Catches no-op carriers and incomparable decision calendars."""
    left = (
        DecisionPoint("2023-01-03", (("orders", []), ("risk", "NORMAL"))),
        DecisionPoint("2023-01-04", (("orders", []), ("risk", "NORMAL"))),
    )
    right = (
        left[0],
        DecisionPoint(
            "2023-01-04",
            (("orders", [{"side": "SELL", "symbol": "sz300308"}]), ("risk", "NORMAL")),
        ),
    )

    divergence = first_decision_divergence(left, right, require=True)
    assert divergence is not None
    assert divergence.date == "2023-01-04"
    assert divergence.changed_fields == ("orders",)

    with pytest.raises(ValueError, match="no behavior divergence"):
        first_decision_divergence(left, left, require=True)
    with pytest.raises(ValueError, match="aligned dates"):
        first_decision_divergence(left, right[:1], require=True)


def test_complete_coverage_rejects_missing_duplicate_and_status_rewrite() -> None:
    """Catches partial runs and hiding the frozen known replay/sample statuses."""
    expected = (
        ("h1/full", "VALID"),
        ("h1/random", "REPLAY_ERROR"),
        ("h1/small", "INSUFFICIENT_SAMPLE"),
    )
    valid_metrics = _metrics(
        wealth=1.0,
        drawdown=0.0,
        orders=0,
        acute=None,
        turnover=0.0,
        top1=0.0,
        top3=0.0,
        hhi=0.0,
    )
    observed = tuple(
        AblationCell("g", name, status, valid_metrics if status == "VALID" else None)
        for name, status in expected
    )
    validate_complete_coverage(expected, observed)

    with pytest.raises(ValueError, match="coverage"):
        validate_complete_coverage(expected, observed[:-1])
    with pytest.raises(ValueError, match="duplicate"):
        validate_complete_coverage(expected, (*observed, observed[0]))
    changed = list(observed)
    changed[1] = AblationCell(
        "g",
        "h1/random",
        "VALID",
        _metrics(
            wealth=1.0,
            drawdown=0.0,
            orders=0,
            acute=None,
            turnover=0.0,
            top1=0.0,
            top3=0.0,
            hhi=0.0,
        ),
    )
    with pytest.raises(ValueError, match="status"):
        validate_complete_coverage(expected, changed)
