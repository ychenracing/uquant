"""Serialize a logical operation; never automatically retry ambiguous writes."""
from contextlib import contextmanager
from pathlib import Path

from .journal import NAME, checkpoint, emit, load, runtime_id
from .report import summarize


@contextmanager
def operation_lock(root, name):
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
        yield stream.fileno()


@contextmanager
def admission(root, name):
    root = Path(root).resolve()
    with operation_lock(root, name) as lock_fd:
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
                "LOCAL_INTERRUPTION_RECONCILED",
            }
            if not completed or result["remote_reconciliation_required"]:
                raise ValueError("prior operation unresolved; inspect and reconcile")
        # The child inherits this descriptor, so killing the supervisor cannot
        # admit another instance while the original child is still running.
        yield lock_fd


def reconcile_local(root, ident, receipt):
    """Explicitly close an old-runtime local command without inventing an exit."""
    if not NAME.fullmatch(ident):
        raise ValueError("invalid operation identifier")
    directory = Path(root).resolve() / ident
    initial = load(directory)
    with operation_lock(root, initial["name"]):
        state = load(directory)
        if (state["name"] != initial["name"] or state["kind"] != "local"
                or state["status"] not in {"STARTED", "RUNNING"}
                or state["runtime_id"] == runtime_id()):
            raise ValueError("requires an unfinished local command from a different runtime")
        state.update(status_before_reconciliation=state["status"],
                     status="LOCAL_INTERRUPTION_RECONCILED",
                     reconciliation_runtime_id=runtime_id(),
                     reconciliation_receipt=checkpoint(receipt))
        emit(directory, state, "local_interruption_reconciled")
        return summarize(state)
