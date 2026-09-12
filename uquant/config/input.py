"""One strict public JSON input for production commands."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from ..contracts.strict_json import strict_json_loads
from .model import DEFAULT_CONFIG, SystemConfig


def load_public_config(path: str | Path | None, *, initial_cash: float | None = None) -> SystemConfig:
    """Read public settings only; compiled rules are never input overrides."""
    if path is None and initial_cash is None:
        return DEFAULT_CONFIG
    payload = {} if path is None else strict_json_loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configuration must be a JSON object")
    unknown = set(payload) - {field.name for field in fields(SystemConfig)}
    if unknown:
        raise ValueError(f"unknown or fixed configuration fields: {sorted(unknown)}")
    cfg = SystemConfig(**payload)
    if initial_cash is not None:
        if "initial_cash" in payload and payload["initial_cash"] != initial_cash:
            raise ValueError("--cash conflicts with initial_cash in --config")
        cfg = SystemConfig(**{**payload, "initial_cash": initial_cash})
    if cfg.risk_sentinel_mode != "FREEZE_ONLY":
        raise ValueError("production configuration requires FREEZE_ONLY")
    return cfg
