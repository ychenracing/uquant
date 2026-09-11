from research.execution_stress import EXECUTION_STRESS_SPECS, run_execution_stresses


def test_execution_stresses_use_native_next_open_and_preserve_blocked_orders() -> None:
    evidence = run_execution_stresses()
    cases = {case["case_id"]: case for case in evidence["cases"]}

    assert set(cases) == {spec.case_id for spec in EXECUTION_STRESS_SPECS}
    assert all(case["same_signal_fill_count"] == 0 for case in cases.values())
    assert cases["P75"]["filled_shares"] == 7_500
    assert cases["P50"]["filled_shares"] == 5_000
    assert cases["P25"]["filled_shares"] == 2_500
    for case_id, ratio in (("P75", 0.75), ("P50", 0.50), ("P25", 0.25)):
        assert cases[case_id]["requested_shares"] == 10_000
        assert cases[case_id]["order_completion_ratio"] == ratio
    for case_id in ("B-UP", "B-DOWN", "B-SUSP", "B-CAP0"):
        assert cases[case_id]["blocked_sessions"] == 1
        assert cases[case_id]["fill_delay_sessions"] == 1
        assert cases[case_id]["pending_preserved_after_block"] is True
    assert all(all(case["invariants"].values()) for case in cases.values())
    assert evidence["scope"] == "DETERMINISTIC_ORDER_LEVEL_NATIVE_EXECUTION_STRESS"
    assert evidence["actual_broker_facts"] is False
    assert evidence["full_strategy_pnl"] is False
    assert evidence["worst_order_level_case"]["case_id"] == "S200"
    assert evidence["portfolio_level_outputs"] == {
        "stressed_wealth": None,
        "stressed_max_drawdown": None,
        "portfolio_turnover": None,
        "portfolio_opportunity_cost": None,
        "worst_key_trade": None,
        "degradation_vs_baseline": None,
        "status": "EVIDENCE GAP — ORDER_LEVEL SCOPE DOES NOT ESTABLISH PORTFOLIO PNL",
    }
