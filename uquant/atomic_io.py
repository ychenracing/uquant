"""Compatibility facade for the shared atomic-output infrastructure."""

from __future__ import annotations

from .infrastructure.atomic_files import (
    atomic_write_bytes,
    atomic_write_text,
    validate_atomic_output_boundary,
    validate_atomic_output_path,
)

__all__ = (
    "atomic_write_bytes",
    "atomic_write_text",
    "validate_atomic_output_boundary",
    "validate_atomic_output_path",
)
