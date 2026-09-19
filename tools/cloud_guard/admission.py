"""Serialize a logical operation; never automatically retry ambiguous writes."""
from contextlib import contextmanager
from pathlib import Path

from .journal import NAME, load
from .report import summarize


@contextmanager
def admission(root, name):
    import fcntl

    if not NAME.fullmatch(name):
        raise ValueError("invalid non-sensitive operation name")
    root = Path(root).resolve()
    locks = root / "_locks"
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (locks / name).open("a+b") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("operation still active; inspect before continuing") from exc
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("_"):
                continue
            # An unreadable journal may belong to this operation. Fail closed.
            state = load(directory)
            if state["name"] != name:
                continue
            result = summarize(state)
            completed = state["status"] in {
                "EXITED", "SPAWN_ERROR", "SUCCESS_REPORTED", "ERROR_REPORTED",
                "REMOTE_VERIFICATION_RECORDED",
            }
            if not completed or result["remote_reconciliation_required"]:
                raise ValueError("prior operation unresolved; inspect and reconcile")
        # The child inherits this descriptor, so killing the supervisor cannot
        # admit another instance while the original child is still running.
        yield stream.fileno()
