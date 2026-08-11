"""Fail-closed gates for data, performance, generalization, and competitors."""

from .competitor import run_competitor_gate
from .generalization import run_generalization
from .manifest import verify_data_manifest
from .promotion import run_promotion

__all__ = [
    "run_competitor_gate",
    "run_generalization",
    "run_promotion",
    "verify_data_manifest",
]
