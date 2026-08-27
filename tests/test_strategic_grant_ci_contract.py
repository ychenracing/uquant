from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.run_strategic_grant_acceptance import _baseline_views

ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict[str, Any]:
    path = ROOT / ".github" / "workflows" / name
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_extended_economic_workflows_are_manual_without_weakening_commands() -> None:
    performance = _workflow("strategy-performance.yml")
    generalization = _workflow("strategy-generalization.yml")

    assert performance["name"] == "Extended Performance Matrix"
    assert generalization["name"] == "Extended Economic Matrix"
    assert performance["on"] == {"workflow_dispatch": {}}
    assert generalization["on"] == {"workflow_dispatch": {}}
    performance_text = str(performance)
    generalization_text = str(generalization)
    assert "--profile full" in performance_text
    assert "generalization-matrix" in generalization_text


def test_strategic_grant_acceptance_is_bounded_and_automatic() -> None:
    workflow = _workflow("strategic-grant-acceptance.yml")

    assert workflow["name"] == "Strategic Grant Acceptance"
    assert workflow["on"] == {
        "pull_request": "",
        "push": {"branches": ["main"]},
        "workflow_dispatch": {},
    }
    rendered = str(workflow)
    assert "scripts/run_strategic_grant_acceptance.py" in rendered
    assert "tests/test_strategic_grant_recovery.py" in rendered
    assert "tests/test_account_schema_v3_integrity.py" in rendered
    assert "tests/test_broker_sync.py" in rendered
    assert "promotion --data-dir" not in rendered
    assert "generalization-matrix" not in rendered
    assert "234" not in rendered


def test_baseline_views_compare_economic_orders_across_physical_retry_ids() -> None:
    def result(order_ids: tuple[str, str]) -> dict[str, object]:
        return {
            "decision_trace": [],
            "order_ledger": [
                {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
                {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
            ],
            "final_account": {
                "fills": [
                    {"event_id": "event-a", "order_id": order_ids[0], "shares": 100},
                    {"event_id": "event-b", "order_id": order_ids[1], "shares": 200},
                ]
            },
            "daily_replay_evidence": [],
            "equity_curve": [],
        }

    main = _baseline_views(result(("O000000001", "O000000002")), frozenset())
    candidate = _baseline_views(result(("O000000004", "O000000005")), frozenset())

    assert candidate == main
