"""Sealed API evolution for the reviewed code-audit changes."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ._analysis import canonical_sha256

_DELTA_SHA256 = "2d8f970ba4b4e3b49b7306c6774060d2ca84ec1b97e9fa9fc6fe26afc7230415"
_ALLOWED_MODULES = frozenset(('uquant', 'uquant.application', 'uquant.application.backtest', 'uquant.application.decision', 'uquant.broker_contract', 'uquant.engine', 'uquant.models.ordinary_state', 'uquant.portfolio.allocator', 'uquant.portfolio.freeze', 'uquant.validation.holdout.service', 'uquant.validation.holdout_runtime'))


def code_audit_api_projection(contract):
    path = Path(__file__).resolve().parents[1] / "fixtures/code_audit_api_delta.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _DELTA_SHA256
    delta = json.loads(raw)
    assert canonical_sha256(contract) == delta["base_projected_sha256"]
    assert set(delta["modules"]) == _ALLOWED_MODULES
    assert set(delta["cli_help"]) == {"uquant account-sync"}
    expected = copy.deepcopy(contract)
    expected["modules"].update(delta["modules"])
    expected["cli_help"].update(delta["cli_help"])
    assert canonical_sha256(expected) == delta["result_sha256"]
    return expected


def followup_audit_api_projection(contract):
    """Track the exact reviewed helper addition and retired internal v1 copies.

    The public uquant.execution_journal classes retain their original contract;
    their canonical v2 owner no longer exposes the unused duplicate names.
    """
    expected = copy.deepcopy(contract)
    broker = expected["modules"]["uquant.broker_contract"]
    assert "broker_identity" not in broker["public_names"]
    broker["public_names"] = sorted([*broker["public_names"], "broker_identity"])
    broker["functions"]["broker_identity"] = {
        "parameters": [
            {"name": "payload", "kind": "POSITIONAL_OR_KEYWORD", "annotation": "dict[str, Any]", "default": {"kind": "required"}},
            {"name": "key", "kind": "POSITIONAL_OR_KEYWORD", "annotation": "str", "default": {"kind": "required"}},
        ],
        "return": "str",
    }
    models = expected["modules"]["uquant.observation.execution_journal.models"]
    for name in ("LegacyJournalCheckpoint", "LegacyJournalRecord", "LegacyJournalStatus"):
        models["public_names"].remove(name)
        del models["classes"][name]
        del models["enums" if name.endswith("Status") else "dataclasses"][name]
    return expected
