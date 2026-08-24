"""Compatibility facade for the canonical strict JSON contract."""

from __future__ import annotations

from . import strict_json as _strict_json

canonical_json_bytes = _strict_json.canonical_json_bytes
canonical_json_sha256 = _strict_json.canonical_json_sha256
strict_json_loads = _strict_json.strict_json_loads

_reject_duplicate_json_keys = _strict_json.reject_duplicate_json_keys
_reject_contract_json_constant = _strict_json.reject_contract_json_constant
