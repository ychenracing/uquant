"""Compatibility facade for canonical atomic-file infrastructure."""

from __future__ import annotations

from . import atomic_files as _atomic_files

atomic_write_bytes = _atomic_files.atomic_write_bytes
atomic_write_text = _atomic_files.atomic_write_text
validate_atomic_output_boundary = _atomic_files.validate_atomic_output_boundary
validate_atomic_output_path = _atomic_files.validate_atomic_output_path

_aliases = _atomic_files._aliases
_existing_destination_mode = _atomic_files._existing_destination_mode
_fsync_directory = _atomic_files._fsync_directory
_open_temporary = _atomic_files._open_temporary
_reject_symlink_path = _atomic_files._reject_symlink_path

__all__ = (
    "atomic_write_bytes",
    "atomic_write_text",
    "validate_atomic_output_boundary",
    "validate_atomic_output_path",
)
