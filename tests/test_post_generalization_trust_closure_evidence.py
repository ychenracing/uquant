from __future__ import annotations

from pathlib import Path

from uquant.contracts.strict_json import (
    canonical_json_sha256,
    strict_json_loads,
)

EVIDENCE = Path("benchmarks/post_generalization_trust_closure_checkpoint_a.json")


def test_checkpoint_a_evidence_is_sealed_and_cannot_misrepresent_the_holdout() -> None:
    payload = strict_json_loads(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)

    unsigned = dict(payload)
    claimed_sha256 = unsigned.pop("canonical_sha256")
    assert claimed_sha256 == canonical_json_sha256(unsigned)
    assert payload["schema_version"] == 1

    frozen = payload["frozen_holdout"]
    assert frozen["classification"] == "FROZEN_ANCHOR_FUTURE_HOLDOUT"
    assert frozen["completed_sessions"] == 19
    assert frozen["actual_broker_execution_journal_present"] is False
    assert frozen["actual_broker_fills"] is None
    assert frozen["milestone"]["status"] == "INTERIM — MILESTONE NOT YET REACHED"
    assert "next_review_session" not in frozen["milestone"]
    assert frozen["milestone"]["next_milestone_session"] == 20
    assert frozen["milestone"]["formal_review_minimum_sessions"] == 40
    assert frozen["session_start"] == "2026-08-06"
    assert frozen["session_end"] == "2026-09-01"
    assert frozen["annualized_turnover"] == 0.0
    assert frozen["initial_owner_symbols"] == ["sz300308"]
    assert frozen["final_owner_symbols"] == ["sz300308"]
    assert frozen["owner_switches"] == 0
    assert frozen["new_grants"] == frozen["new_epochs"] == 0
    assert frozen["pending_orders"] == frozen["execution_blockages"] == 0
    assert frozen["industry_exposure"] == {}
    assert frozen["failed_grant_retry_events"] == 0
    assert frozen["recovery_events"] == frozen["rearm_events"] == []
    assert frozen["constant_daily_timeline"] == {
        "start": "2026-08-06",
        "end": "2026-09-01",
        "sessions": 19,
        "daily_return": 0.0,
        "cash_ratio": 1.0,
        "gross_exposure": 0.0,
        "opportunity": "WEAK",
        "risk": "RISK_OFF",
        "target_gross": 0.0,
    }

    bridge = payload["current_main_retrospective_bridge"]
    assert bridge["classification"] == "RETROSPECTIVE_BRIDGE_NOT_FUTURE_HOLDOUT"
    assert bridge["authoritative_future_holdout"] is False
    assert bridge["prospective_completed_sessions"] == 0
    assert bridge["model_account_orders"] == bridge["model_fills"] == 0
    assert bridge["actual_broker_execution_journal_present"] is False
    assert bridge["actual_broker_fills"] is None

    accounting = payload["historical_exact_accounting"]["reconciliation"]
    assert accounting["total_pnl"] == 47019323.60580174
    assert accounting["expected_pnl"] == 47019323.60580173
    assert accounting["residual"] == 7.450580596923828e-09
    assert accounting["reconciled"] is True

    historical = payload["historical_exact_accounting"]
    assert historical["by_symbol"]["sz300308"]["realized_lots"] == 14
    assert historical["by_symbol"]["sz300308"]["open_pnl"] == 0.0
    assert historical["by_symbol"]["sz300308"]["first_buy"] == "2023-01-05"
    assert historical["by_symbol"]["sz300308"]["last_sell"] == "2026-06-24"
    assert historical["by_symbol"]["sz300308"]["strategic_epoch_count"] == 1
    zero_symbol_facts = {
        "realized_pnl": 0.0,
        "open_pnl": 0.0,
        "total_pnl": 0.0,
        "absolute_pnl_share": 0.0,
        "realized_lots": 0,
        "buy_shares": 0,
        "sell_shares": 0,
        "buy_fills": 0,
        "sell_fills": 0,
        "weighted_holding_sessions": 0.0,
        "maximum_holding_sessions": 0,
        "mean_exposure": 0.0,
        "maximum_exposure": 0.0,
        "strategic_epoch_count": 0,
    }
    assert historical["by_symbol"]["sz300502"] == zero_symbol_facts
    assert historical["by_symbol"]["sz300394"] == zero_symbol_facts
    for industry in (
        "compute",
        "pcb",
        "semicap",
        "materials",
        "storage",
        "datacenter",
        "others",
    ):
        assert historical["by_industry"][industry]["total_pnl"] == 0.0
    for route in ("ORDINARY_LEADER", "RECOVERY", "TACTICAL", "SATELLITE", "REPLACEMENT"):
        assert historical["by_origin_route"][route]["total_pnl"] == 0.0
    for lifecycle in ("ADD1", "ADD2", "SATELLITE", "RECOVERY"):
        assert historical["by_origin_lifecycle"][lifecycle]["total_pnl"] == 0.0

    epoch = historical["strategic_epoch"]
    assert epoch["qualification_route"] == "reversal_industry"
    assert epoch["qualification_quorum"] == "FULL_COHORT"
    assert epoch["closed"] is epoch["termination_reason"] is epoch["successor"] is None
    assert epoch["total_pnl_share"] == 1.0
    assert epoch["direct_deployed_buy_gross"] == 7413046.741099999
    assert epoch["pnl_projection_limit"] == (
        "OWNER_LOT_PROJECTED_TOTAL_PNL_NOT_ALL_RISK_EXIT_FILLS_RETAIN_EPOCH_ID"
    )

    current = payload["current_identity_counterfactual"]
    assert current["provenance"]["github_workflow_run_id"] == 33539562132
    assert current["provenance"]["tree_matches_current_main"] is True
    assert current["provenance"]["current_main_tree"] == (
        "b03021c55d5c7ad6803b33b777e9db38a46a1789"
    )
    assert current["cells"]["remove_sz300308"]["canonical_cell_sha256"] == (
        "89bfaf5d21a00fb6d19dd5951209c0faca0a546687a1d3d7eb644ef7f433b0fa"
    )
    assert current["cells"]["remove_sz300308"]["owner_sequence"] == ["sh601869"]
    assert current["cells"]["remove_sz300502"]["owner_sequence"] == [
        "sz300394",
        "sz300308",
    ]
    assert current["cells"]["remove_sz300394"]["owner_sequence"] == ["sz300502"]
    assert current["cells"]["remove_sz300308"]["epochs"][0]["realized_status"] == (
        "CLOSED"
    )
    assert current["cells"]["remove_sz300308"]["epochs"][0]["closed"] == (
        "2026-05-28"
    )
    assert current["cells"]["remove_sz300308"]["capability_conclusion"] == (
        "OBSERVED_HISTORICALLY_ACTIVATED_SUCCESSOR_EPOCH_SH601869_STILL_OPTICAL"
    )
    assert current["cells"]["remove_sz300394"]["epochs"][0]["realized_status"] == (
        "CLOSED"
    )
    assert current["cells"]["remove_sz300394"]["epochs"][0]["closed"] == (
        "2026-06-25"
    )
    assert current["cells"]["remove_sz300394"]["capability_conclusion"] == (
        "OBSERVED_HISTORICALLY_ACTIVATED_OWNER_EPOCH_SZ300502"
    )
    assert [
        epoch["realized_status"]
        for epoch in current["cells"]["remove_sz300502"]["epochs"]
    ] == ["CLOSED", "CLOSED"]
    for cell_name in ("remove_sz300502", "remove_sz300394"):
        assert current["cells"][cell_name]["concentration"]["interpretation"] == (
            "RAW_SOURCE_FIELDS_INCONSISTENT_UNRELIED_UPON"
        )
    for cell in current["cells"].values():
        assert cell["accounting_reconciled"] is True
        assert cell["target_order_fill_identity_reconciled"] is True
        assert cell["duplicate_counts"] == {"epoch": 0, "grant": 0, "order": 0}
    assert "pending_bounded_replays" not in current

    bounded = current["bounded_current_diagnostics"]
    assert bounded["classification"] == "BOUNDED_CURRENT_IDENTITY_DIAGNOSTIC"
    assert bounded["authoritative_acceptance"] is False
    assert bounded["future_holdout"] is False
    assert bounded["codec_interpretation"] == {
        "raw_conflict": (
            "CURRENT_FULL_COHORT_ALLOWS_NON_OWNER_COHORT_ROWS_WITH_BLANK_GRANT_AND_SHARED_EPOCH"
        ),
        "normalization_scope": (
            "EXISTING_ABSOLUTE_HELPER_NORMALIZES_EVIDENCE_DEEP_COPY_ONLY"
        ),
        "later_cleanup": "DURABLE_CURRENT_ONLY_NATIVE_CODEC_SUPPORT_OR_SHIM_REMOVAL",
        "strategy_change": False,
    }

    cases = bounded["cases"]
    assert set(cases) == {"original_three_core", "remove_all_three_core", "no_optical"}
    expected_hashes = {
        "original_three_core": (
            "93a7308f6788c9ea5459cd20be9ddd81e6ea0c6de0d5ddbe3fcf4ab38990d663"
        ),
        "remove_all_three_core": (
            "b906bbdf6748a18b1ca9b120d385ac4569d669733150cc8b6de547657816138c"
        ),
        "no_optical": "2eca50a5418ec4011ec25042020e9ece6d52fa45ab9f60bdd249dfb637cfaff6",
    }
    for case_id, case in cases.items():
        assert "canonical_sha256" not in case
        assert case["source_result_canonical_sha256"] == expected_hashes[case_id]
        assert case["window"] == {
            "start": "2023-01-03",
            "end": "2026-08-05",
            "sessions": 869,
        }
        assert case["authoritative_acceptance"] is False
        assert case["future_holdout"] is False
        assert case["account_codec"]["raw_status"] == "ERROR"
        assert case["account_codec"]["raw_error"] == (
            "RuntimeError: account order grant identity differs from strategic epoch"
        )
        assert case["account_codec"]["normalized_status"] == "VALID"
        assert case["account_codec"]["normalization_applied_to_deep_copy"] is True
        assert case["identities"]["baseline_current_main_tree_oid"] == (
            "b03021c55d5c7ad6803b33b777e9db38a46a1789"
        )
        assert "current_tree" not in case["identities"]
        assert case["invariants"] == {
            "negative_cash": False,
            "negative_position": False,
            "duplicate_order_ids": 0,
            "duplicate_physical_fill_identities": 0,
        }

    original = cases["original_three_core"]
    assert original["metrics"]["final_wealth"] == 3.40939236400687
    assert original["metrics"]["top3_concentration"] == 1.0000000000000002
    assert original["metrics"]["pnl_hhi"] == 0.6361176665626133
    assert original["accounting"]["total_pnl"] == 4818784.72801374
    assert original["account_codec"]["identity_mismatch_count"] == 16
    assert original["by_symbol"]["sz300308"]["absolute_pnl_share"] == (
        0.7608808794858424
    )
    assert original["by_symbol"]["sz300394"]["absolute_pnl_share"] == (
        0.2391191205141576
    )
    assert original["by_symbol"]["sz300502"]["total_pnl"] == 0.0
    assert original["by_industry"]["optical"]["absolute_pnl_share"] == 1.0

    removed = cases["remove_all_three_core"]
    no_optical = cases["no_optical"]
    assert removed["metrics"] == no_optical["metrics"]
    assert removed["accounting"] == no_optical["accounting"]
    assert removed["accounting"]["total_pnl"] == 20789.285368500307
    assert removed["account_codec"]["identity_mismatch_count"] == 9
    assert no_optical["account_codec"]["identity_mismatch_count"] == 9
    for case in (removed, no_optical):
        assert case["metrics"]["final_wealth"] == 1.01039464268425
        assert case["by_symbol"]["sh688200"]["total_pnl"] == -75852.1348041599
        assert case["by_symbol"]["sz300054"]["total_pnl"] == 38732.23916298
        assert case["by_symbol"]["sz300223"]["total_pnl"] == 57909.18100968021
        assert case["by_industry"]["semicap"]["absolute_pnl_share"] == 1.0
        assert case["epochs"][0]["owner"] == "sh688200"
        for absent_core in ("sz300308", "sz300394", "sz300502"):
            assert case["by_symbol"][absent_core]["total_pnl"] == 0.0

    assert bounded["core_conclusion"] == {
        "realized_baseline_alpha": "100_PERCENT_SZ300308_OPTICAL",
        "individual_loo": "CAPABILITY_SURVIVES_BUT_REMAINS_OPTICAL",
        "no_optical": "TECHNICAL_SUCCESSOR_DISCOVERY_AT_1_01039464268425X",
        "bottom_line": (
            "CAPABILITY_DIVERSIFICATION_EXISTS_REALIZED_PNL_DIVERSIFICATION_DOES_NOT"
        ),
    }

    observation_policy = payload["observation_policy"]
    assert observation_policy["backfill_allowed"] is False
    assert observation_policy["parameter_changes_from_observation"] is False
