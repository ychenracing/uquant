"""Fail-closed gates for data and 2023+ AI-era performance.

Gate implementations are deliberately imported lazily. The production engine
consumes only the calendar guard and must not create an import cycle by eagerly
loading replay adapters that themselves depend on the engine.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "run_competitor_gate",
    "run_generalization",
    "run_generalization_matrix",
    "run_promotion",
    "verify_data_manifest",
]


def __getattr__(name: str) -> Any:
    if name == "run_competitor_gate":
        from .competitor import run_competitor_gate

        return run_competitor_gate
    if name == "run_generalization":
        from .generalization import run_generalization

        return run_generalization
    if name == "run_generalization_matrix":
        from .generalization_matrix import run_generalization_matrix

        return run_generalization_matrix
    if name == "run_promotion":
        from .promotion import run_promotion

        return run_promotion
    if name == "verify_data_manifest":
        from .manifest import verify_data_manifest

        return verify_data_manifest
    raise AttributeError(name)
