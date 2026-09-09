"""Native daily-path protection against immature ordinary capital commitments."""
import gzip
import json
from pathlib import Path

from research.cross_ai_strategy import run_production_case


def test_new_ordinary_orders_require_current_maturity(tmp_path: Path) -> None:
    output = tmp_path / "ordinary"
    result = run_production_case(
        case_id="no_optical", start="2023-01-03", end="2023-02-03", output_dir=output,
    )
    assert result["status"] == "COMPLETE", result.get("error")
    observed = 0
    with gzip.open(output / "observations.jsonl.gz", "rt") as stream:
        for line in stream:
            row = json.loads(line)
            scores = {score["symbol"]: score for score in row["observation"]["leader_scores"]}
            for order in row["decision"]["orders"]:
                if order["side"] == "BUY" and order["mechanism"] == "LEADER_SELECTION":
                    assert scores[order["symbol"]]["mature"], (row["date"], order["symbol"])
                    observed += 1
    assert observed > 0
