"""Recorded native champion codec tests, isolated from real checkout acceptance.

Only the in-memory candidate used by codec tests is the recorded producer.
The raw fixture bytes and independent current policy are never rewritten.
Real CLI and checkout tests do not import this fixture.
"""
import importlib
from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def native_champion_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("uquant.validation.absolute_generalization.contract")
    candidate = module._candidate_identity()
    recorded = replace(candidate, production_source_sha256=
        "73969473f9e53731ed2ee5a5ba957f7f63ce7a7e6cf56f8b88ccad7288f44e3b")
    monkeypatch.setattr(module, "_candidate_identity", lambda: recorded)
