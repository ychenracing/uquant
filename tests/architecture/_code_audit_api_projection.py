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
