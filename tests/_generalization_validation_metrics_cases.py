from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_generalization import (
    _industries,
    _matrix,
    _observation,
    _prices,
    _provenance,
    _reference_payload,
    _universe,
)

from uquant.validation.generalization import (
    GeneralizationObservation,
    GeneralizationScenario,
    aggregate_metrics,
    build_generalization_scenarios,
    compute_pre_window_evidence,
    industry_pnl_shares,
    load_generalization_baseline,
    observation_from_result,
    prior_dependence,
    run_generalization,
    scenario_fingerprint,
    symbol_pnl_concentration,
    symbol_pnl_from_result,
)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda mapping: mapping.pop("s29"), "missing=\\['s29'\\]"),
        (lambda mapping: mapping.update({"outside": "memory"}), "extra=\\['outside'\\]"),
    ],
)
def test_industry_map_must_exactly_cover_the_replay_universe(
    mutate: Any,
    message: str,
) -> None:
    prices = _prices()
    evidence = compute_pre_window_evidence(
        prices,
        _universe(),
        window_start="2026-01-05",
        lookback_sessions=120,
    )
    industries = _industries()
    mutate(industries)

    with pytest.raises(ValueError, match=message):
        build_generalization_scenarios(
            _universe(),
            industries,
            _universe()[:3],
            window_start="2026-01-05",
            pre_window_evidence=evidence,
            random_sizes=(6, 12, 24),
            random_seeds=(0,),
            leave_top_k=(1, 2, 3, 5),
        )

def test_run_rejects_industry_coverage_before_evidence_baseline_or_replay(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="missing=\\['b'\\]"):
        run_generalization(
            data_dir="unused",
            universe=("a", "b"),
            industries={"a": "memory"},
            prior_symbols=("a",),
            start="2026-01-05",
            end="2026-07-20",
            baseline_path=tmp_path / "does-not-exist.json",
            pre_window_prices={},
            runner=lambda _: pytest.fail("replay must not start"),
            random_sizes=(1,),
            random_seeds=(0,),
            leave_top_k=(1,),
        )

def test_aggregate_pdi_and_signed_industry_pnl_share() -> None:
    base = GeneralizationScenario("base", "baseline", ("memory", "optical"))
    one_a = GeneralizationScenario("remove_one__a", "remove_one", ("memory",))
    one_b = GeneralizationScenario("remove_one__b", "remove_one", ("optical",))
    all_priors = GeneralizationScenario("remove_all_priors", "remove_all", ("other",))
    observations = (
        _observation(base, wealth=10.0, drawdown=0.10, orders=4, pnl=(("optical", 70.0), ("memory", 30.0))),
        _observation(one_a, wealth=8.0, drawdown=0.20, orders=8),
        _observation(one_b, wealth=9.0, drawdown=0.30, orders=6),
        _observation(all_priors, wealth=7.0, drawdown=0.40, orders=10),
    )

    dependency = prior_dependence(observations)
    assert dependency["PDI_1"] == pytest.approx(0.20)
    assert dependency["PDI_3"] == pytest.approx(0.30)
    assert dependency["PDI_1_worst_case"] == "remove_one__a"

    aggregate = aggregate_metrics(observations[1:])
    assert aggregate == pytest.approx(
        {
            "p10_wealth": 7.2,
            "median_wealth": 8.0,
            "p90_drawdown": 0.38,
            "worst_drawdown": 0.40,
            "median_orders": 8.0,
            "p90_orders": 9.6,
        }
    )
    shares = industry_pnl_shares(
        observations[0],
        {"optical": "optical", "memory": "memory"},
    )
    assert shares["optical"]["share_of_net_pnl"] == pytest.approx(0.70)
    assert shares["memory"]["share_of_net_pnl"] == pytest.approx(0.30)

    signed = GeneralizationObservation(
        "signed",
        "baseline",
        2.0,
        0.1,
        1,
        (("winner", 120.0), ("loser", -20.0)),
    )
    signed_shares = industry_pnl_shares(
        signed,
        {"winner": "optical", "loser": "memory"},
    )
    assert signed_shares["optical"]["share_of_net_pnl"] == pytest.approx(1.20)
    assert signed_shares["memory"]["share_of_net_pnl"] == pytest.approx(-0.20)

def test_symbol_pnl_reconciles_fills_and_open_positions_to_total_profit() -> None:
    result: dict[str, Any] = {
        "final_equity": 1092.0,
        "final_account": {
            "initial_cash": 1000.0,
            "fills": [
                {
                    "symbol": "a",
                    "side": "BUY",
                    "gross_value": 400.0,
                    "commission": 4.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                },
                {
                    "symbol": "a",
                    "side": "SELL",
                    "gross_value": 250.0,
                    "commission": 1.0,
                    "stamp_duty": 1.0,
                    "transfer_fee": 0.0,
                },
                {
                    "symbol": "b",
                    "side": "BUY",
                    "gross_value": 200.0,
                    "commission": 2.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.0,
                },
            ],
            "positions": {
                "a": {"shares": 10},
                "b": {"shares": 10},
            },
        },
    }

    pnl = symbol_pnl_from_result(result, {"a": 20.0, "b": 25.0})
    assert pnl == pytest.approx({"a": 44.0, "b": 48.0})
    assert sum(pnl.values()) == pytest.approx(92.0)

    result["final_equity"] = 1093.0
    with pytest.raises(ValueError, match="does not reconcile"):
        symbol_pnl_from_result(result, {"a": 20.0, "b": 25.0})

def test_symbol_pnl_concentration_uses_exact_absolute_contributions() -> None:
    """Catches fabricated attribution or signed cancellation in concentration metrics."""
    assert symbol_pnl_concentration({"a": 3.0, "b": -1.0}) == pytest.approx(
        {
            "top1_concentration": 0.75,
            "top3_concentration": 1.0,
            "pnl_hhi": 0.625,
        }
    )
    assert symbol_pnl_concentration({}) == {
        "top1_concentration": 0.0,
        "top3_concentration": 0.0,
        "pnl_hhi": 0.0,
    }
    with pytest.raises(ValueError, match="invalid symbol PnL"):
        symbol_pnl_concentration({"a": float("nan")})

def test_observation_rejects_inexact_orders_and_out_of_universe_attribution() -> None:
    case = GeneralizationScenario("base", "baseline", ("a",))
    with pytest.raises(ValueError, match="non-integer order count"):
        observation_from_result(
            case,
            {
                "final_wealth": 1.1,
                "max_drawdown": 0.1,
                "account_orders": 1.5,
                "symbol_pnl": {"a": 0.1},
            },
        )
    with pytest.raises(ValueError, match="outside its universe"):
        observation_from_result(
            case,
            {
                "final_wealth": 1.1,
                "max_drawdown": 0.1,
                "account_orders": 1,
                "symbol_pnl": {"invented": 0.1},
            },
        )

    observed = observation_from_result(
        case,
        {
            "final_wealth": 1.1,
            "max_drawdown": 0.1,
            "account_orders": 1,
            "symbol_pnl": {"a": 0.1},
            "final_account": {
                "fills": [
                    {
                        "symbol": "a",
                        "side": "BUY",
                        "shares": 100,
                        "lifecycle": "CORE",
                        "reason_code": "strategic_cohort",
                    }
                ]
            },
        },
    )
    assert observed.deployed_exposure == (("a", "CORE"), ("a", "STRATEGIC"))

def test_baseline_rejects_duplicate_missing_and_unexpected_references(tmp_path: Path) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    payload = _reference_payload(cases, observations)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_generalization_baseline(path, cases)
    assert loaded.case_fingerprint == scenario_fingerprint(cases)

    missing = json.loads(json.dumps(payload))
    missing["references"].pop(cases[-1].name)
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing references"):
        load_generalization_baseline(path, cases)

    unexpected = json.loads(json.dumps(payload))
    unexpected["references"]["invented"] = unexpected["references"]["base"]
    path.write_text(json.dumps(unexpected), encoding="utf-8")
    with pytest.raises(RuntimeError, match="unexpected references"):
        load_generalization_baseline(path, cases)

    duplicate = (
        '{"schema_version":1,"case_fingerprint":"x","references":{'
        '"base":{"final_wealth":1,"max_drawdown":0,"account_orders":0},'
        '"base":{"final_wealth":1,"max_drawdown":0,"account_orders":0}}}'
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate key: base"):
        load_generalization_baseline(path, (cases[0],))

def test_baseline_binds_manifest_dataset_execution_and_production_provenance(
    tmp_path: Path,
) -> None:
    cases, _ = _matrix(random_seeds=range(1))
    observations = tuple(_observation(case) for case in cases)
    expected = _provenance()
    payload = _reference_payload(cases, observations)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_generalization_baseline(path, cases, expected_provenance=expected)
    assert loaded.validation_fingerprint == payload["validation_fingerprint"]
    assert loaded.provenance == expected

    changed = json.loads(json.dumps(expected))
    changed["data"]["manifest_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="provenance does not match"):
        load_generalization_baseline(path, cases, expected_provenance=changed)

    stale = json.loads(json.dumps(payload))
    stale["provenance"]["production"]["source_sha256"] = "1" * 64
    path.write_text(json.dumps(stale), encoding="utf-8")
    with pytest.raises(RuntimeError, match="validation fingerprint is stale"):
        load_generalization_baseline(path, cases)

    missing = json.loads(json.dumps(payload))
    missing.pop("provenance")
    path.write_text(json.dumps(missing), encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"missing sections.*provenance"):
        load_generalization_baseline(path, cases)
