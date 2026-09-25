"""Sealed API evolution for the reviewed account, data, execution and classification remediation."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ._analysis import canonical_sha256

_DELTA_SHA256 = "d57dfd35e095172762b135366fa9b00471af9b7b30dc0a587a4c8aa302a58acb"
_NEW_MODULES = frozenset((
    "uquant.account.corporate_actions", "uquant.account.schema_migration", "uquant.account.transaction",
    "uquant.broker_facts", "uquant.data_check", "uquant.data_update", "uquant.market.valuation",
))
_SECTIONS = frozenset(("account_state_schema", "flat_config_serialization", "typical_exceptions"))
_CLI_HELP = frozenset(("uquant", "uquant account-schema-migrate", "uquant data-check", "uquant data-update"))


def remediation_api_projection(contract):
    path = Path(__file__).resolve().parents[1] / "fixtures/remediation_api_delta.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _DELTA_SHA256
    delta = json.loads(raw)
    assert canonical_sha256(contract) == delta["base_projected_sha256"]
    assert set(delta["sections"]) == _SECTIONS
    assert set(delta["cli_help"]) == _CLI_HELP
    assert set(delta["new_modules"]) == _NEW_MODULES
    expected = copy.deepcopy(contract)
    expected.update(delta["sections"])
    expected["cli_help"].update(delta["cli_help"])
    modules = expected["modules"]
    assert _NEW_MODULES.isdisjoint(modules)
    modules.update(delta["new_modules"])
    for name, sections in delta["module_items"].items():
        module = modules[name]
        for section, items in sections.items():
            if "__replace__" in items:
                module[section] = items["__replace__"]
                continue
            target = module.setdefault(section, {})
            for item, value in items.items():
                if value is None:
                    del target[item]
                else:
                    target[item] = value
    assert canonical_sha256(expected) == delta["result_sha256"]
    return expected
