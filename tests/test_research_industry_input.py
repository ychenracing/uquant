from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from uquant.contracts.universe import (
    decision_ai_universe,
    default_ai_universe,
    load_ai_universe,
    research_industry_input,
)
from uquant.industry import decision_industries

REVIEW = Path(__file__).resolve().parents[1] / "benchmarks/industry_input_v2/taxonomy.json"
REVIEW_SHA = hashlib.sha256(REVIEW.read_bytes()).hexdigest()


def test_research_taxonomy_is_causal_and_does_not_change_membership_or_default() -> None:
    frozen = default_ai_universe()
    with research_industry_input(REVIEW, expected_sha256=REVIEW_SHA) as revised:
        assert decision_ai_universe() is revised
        assert load_ai_universe() is not revised
        assert revised.members == frozen.members
        assert revised.sha256 != frozen.sha256
        assert revised.industry_of("sz002371", "2022-04-28") == "pcb"
        assert revised.industry_of("sz002371", "2022-04-29") == "semicap"
        assert decision_industries("2023-01-03")["sz002371"] == "equipment"
        for day in ("2023-01-03", "2023-05-01", "2023-09-01"):
            assert revised.symbols_as_of(day) == frozen.symbols_as_of(day)
        with pytest.raises(RuntimeError, match="nested"), research_industry_input(REVIEW, expected_sha256=REVIEW_SHA):
            pass
    assert decision_ai_universe() is frozen
    assert decision_industries("2023-01-03")["sz002371"] == "pcb"


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "foreign", "date", "industry"])
def test_research_input_rejects_unpinned_or_invalid_review(tmp_path: Path, mutation: str) -> None:
    payload = json.loads(REVIEW.read_bytes())
    if mutation == "duplicate":
        payload["members"][0] = payload["members"][1]
    elif mutation == "foreign":
        payload["members"][0]["symbol"] = "sh999999"
    elif mutation == "date":
        payload["members"][0]["conservative_known_by"] = "2026-08-06"
    elif mutation == "industry":
        payload["members"][0]["research_industry"] = "invented"
    path = tmp_path / "review.json"
    path.write_text(json.dumps(payload))
    digest = "0" * 64 if mutation == "hash" else hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError), research_industry_input(path, expected_sha256=digest):
        pass
    assert decision_ai_universe() is default_ai_universe()


def test_research_native_account_readback_requires_exact_input(tmp_path: Path) -> None:
    from research.cross_ai_acceptance import read_case
    from research.cross_ai_strategy import run_production_case
    from uquant.account import load_account
    from uquant.engine import code_fingerprint

    output = tmp_path / "native"
    with research_industry_input(REVIEW, expected_sha256=REVIEW_SHA) as revised:
        result = run_production_case(case_id="full", start="2023-01-03", end="2023-01-10",
                                     output_dir=output)
        assert result["status"] == "COMPLETE", result["error"]
        read_case(output, case="full", interval=["2023-01-03", "2023-01-10"],
                  source=code_fingerprint())

        account = load_account(output / "final_account.json")
        assert account.fills
        for order in account.order_ledger:
            assert order.industry_manifest_sha256 == revised.sha256
            assert order.industry_at_entry == revised.industry_of(order.symbol, order.signal_date)
    with pytest.raises(RuntimeError, match="industry manifest"):
        load_account(output / "final_account.json")
    with pytest.raises(ValueError, match="research universe"):
        read_case(output, case="full", interval=["2023-01-03", "2023-01-10"],
                  source=code_fingerprint())


def test_shared_score_cache_separates_inputs_and_restores_production(data_dir: Path) -> None:
    import pandas as pd

    from uquant.config import DEFAULT_CONFIG
    from uquant.engine import ProductionEngine
    from uquant.leader import compute_structural_leaders

    engine = ProductionEngine(data_dir)
    engine._load((*default_ai_universe().symbols, "sh000682"))
    cache = {}
    kwargs = dict(as_of=pd.Timestamp("2023-01-03"), tech=engine._features["sh000682"],
                  cfg=DEFAULT_CONFIG, score_cache=cache)
    old = compute_structural_leaders(engine._features, **kwargs)
    with research_industry_input(REVIEW, expected_sha256=REVIEW_SHA):
        new = compute_structural_leaders(engine._features, **kwargs)
        assert new is not old
        assert new["sz002371"].industry == "equipment"
        assert old["sz002371"].industry == "pcb"
    assert compute_structural_leaders(engine._features, **kwargs) is old
