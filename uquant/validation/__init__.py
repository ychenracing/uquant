"""Release gates for frozen data and strategy-performance promotion."""

from .manifest import verify_data_manifest
from .promotion import run_promotion

__all__ = ["run_promotion", "verify_data_manifest"]
