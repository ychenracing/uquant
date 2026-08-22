"""Blocking shared/exclusive descriptor locks for POSIX and Windows."""

from __future__ import annotations

import ctypes
import importlib
import os
from collections.abc import Callable
from ctypes import wintypes
from enum import Enum
from typing import Protocol, cast


class FileLockMode(str, Enum):
    """Portable lock modes supported by the descriptor abstraction."""

    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class _PosixLockModule(Protocol):
    LOCK_EX: int
    LOCK_SH: int
    LOCK_UN: int

    def flock(self, descriptor: int, operation: int) -> None: ...


class _WindowsRuntimeModule(Protocol):
    def get_osfhandle(self, descriptor: int) -> int: ...


class _ForeignFunction(Protocol):
    argtypes: list[object]
    restype: object

    def __call__(self, *arguments: object) -> int: ...


class _WindowsKernel32(Protocol):
    LockFileEx: _ForeignFunction
    UnlockFileEx: _ForeignFunction


class _Overlapped(ctypes.Structure):
    _fields_ = (
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    )


def _posix_lock(descriptor: int, mode: FileLockMode) -> None:
    module = cast(_PosixLockModule, importlib.import_module("fcntl"))
    operation = module.LOCK_SH if mode is FileLockMode.SHARED else module.LOCK_EX
    module.flock(descriptor, operation)


def _posix_unlock(descriptor: int) -> None:
    module = cast(_PosixLockModule, importlib.import_module("fcntl"))
    module.flock(descriptor, module.LOCK_UN)


def _windows_kernel() -> _WindowsKernel32:
    loader_value = vars(ctypes).get("WinDLL")
    if not callable(loader_value):
        raise OSError("Windows file locking is unavailable")
    loader = cast(Callable[..., object], loader_value)
    return cast(_WindowsKernel32, loader("kernel32", use_last_error=True))


def _windows_error() -> OSError:
    get_last_error_value = vars(ctypes).get("get_last_error")
    if not callable(get_last_error_value):
        return OSError("Windows file locking is unavailable")
    get_last_error = cast(Callable[[], int], get_last_error_value)
    return OSError(get_last_error(), "Windows file lock operation failed")


def _windows_lock(descriptor: int, mode: FileLockMode) -> None:
    runtime = cast(_WindowsRuntimeModule, importlib.import_module("msvcrt"))
    kernel = _windows_kernel()
    function = kernel.LockFileEx
    function.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    function.restype = wintypes.BOOL
    flags = 0x00000002 if mode is FileLockMode.EXCLUSIVE else 0
    overlapped = _Overlapped()
    locked = function(
        wintypes.HANDLE(runtime.get_osfhandle(descriptor)),
        wintypes.DWORD(flags),
        wintypes.DWORD(0),
        wintypes.DWORD(0xFFFFFFFF),
        wintypes.DWORD(0xFFFFFFFF),
        ctypes.byref(overlapped),
    )
    if not locked:
        raise _windows_error()


def _windows_unlock(descriptor: int) -> None:
    runtime = cast(_WindowsRuntimeModule, importlib.import_module("msvcrt"))
    kernel = _windows_kernel()
    function = kernel.UnlockFileEx
    function.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_Overlapped),
    ]
    function.restype = wintypes.BOOL
    overlapped = _Overlapped()
    unlocked = function(
        wintypes.HANDLE(runtime.get_osfhandle(descriptor)),
        wintypes.DWORD(0),
        wintypes.DWORD(0xFFFFFFFF),
        wintypes.DWORD(0xFFFFFFFF),
        ctypes.byref(overlapped),
    )
    if not unlocked:
        raise _windows_error()


def acquire_file_lock(descriptor: int, mode: FileLockMode) -> None:
    """Block until the requested process-wide descriptor lock is acquired."""

    if not isinstance(mode, FileLockMode):
        raise ValueError("file lock mode is invalid")
    if os.name == "nt":
        _windows_lock(descriptor, mode)
    else:
        _posix_lock(descriptor, mode)


def release_file_lock(descriptor: int) -> None:
    """Release a descriptor lock, propagating every unlock failure."""

    if os.name == "nt":
        _windows_unlock(descriptor)
    else:
        _posix_unlock(descriptor)
