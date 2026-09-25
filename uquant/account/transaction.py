"""Exclusive, compare-and-swap account write transactions."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..infrastructure.file_lock import FileLockMode, acquire_file_lock, release_file_lock
from ..types import AccountState
from .codec import load_account
from .store import save_account


class AccountConflictError(RuntimeError):
    """The account file changed after it was read by this transaction."""


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


class AccountTransaction:
    """One locked read-modify-write cycle over a single account file."""

    def __init__(self, path: Path, *, create: bool) -> None:
        self.path = path
        self.create = create
        self.base_sha256: str | None = _file_sha256(path)
        if create and self.base_sha256 is not None:
            raise FileExistsError(f"account already exists; refusing to overwrite: {path}")
        if not create and self.base_sha256 is None:
            raise FileNotFoundError(f"account not found: {path}")

    def load(self, **kwargs: bool) -> AccountState:
        state = load_account(self.path, **kwargs)
        if _file_sha256(self.path) != self.base_sha256:
            raise AccountConflictError(f"account changed while being read: {self.path}")
        return state

    def save(self, state: AccountState) -> None:
        if _file_sha256(self.path) != self.base_sha256:
            raise AccountConflictError(
                f"account changed since it was read; refusing stale overwrite: {self.path}"
            )
        state.account_revision += 1
        save_account(state, self.path)
        self.base_sha256 = _file_sha256(self.path)


@contextmanager
def account_transaction(path: str | Path, *, create: bool = False) -> Iterator[AccountTransaction]:
    """Hold an exclusive sibling lock for one account read-modify-write cycle."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(target.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        acquire_file_lock(descriptor, FileLockMode.EXCLUSIVE)
        try:
            yield AccountTransaction(target, create=create)
        finally:
            release_file_lock(descriptor)
    finally:
        os.close(descriptor)
