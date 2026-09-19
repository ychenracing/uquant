"""Cloud command evidence, not a ChatGPT service hook. Linux/POSIX only."""
from __future__ import annotations

from .journal import proc_start, runtime_id


def summarize(state):
    kind, status = state["kind"], state["status"]
    write = kind in {"write", "external_write"}
    result = {k: state.get(k) for k in ("operation_id", "name", "kind", "status", "started_at_utc", "updated_at_utc")}
    if status == "EXITED":
        code, stop = state.get("returncode"), state.get("stop_reason")
        finding = stop or ("COMMAND_OK" if code == 0 else "PROCESS_SIGNAL" if code < 0 else "COMMAND_NONZERO_EXIT")
        result.update(finding=finding, returncode=code, signal=(-code if code < 0 else None))
    elif status == "SPAWN_ERROR":
        result.update(finding=status, error_type=state.get("error_type"), errno=state.get("errno"))
    elif status in {"SUCCESS_REPORTED", "ERROR_REPORTED", "REMOTE_VERIFICATION_RECORDED", "OUTCOME_UNKNOWN"}:
        result.update(finding=status, reported_code=state.get("reported_code"))
    elif kind in {"external_read", "external_write", "phase"}:
        result["finding"] = "EXTERNAL_RESULT_UNKNOWN"  # Not proof of provider failure or dispatch.
    elif state["runtime_id"] != runtime_id():
        result["finding"] = "RUNTIME_CHANGED_NO_TERMINAL_RECORD"
    elif state.get("supervisor_start") and proc_start(state["supervisor_pid"]) == state["supervisor_start"]:
        result["finding"] = "SUPERVISOR_PRESENT_VERIFY_PROGRESS"
    elif state.get("child_start") and proc_start(state["child_pid"]) == state["child_start"]:
        result["finding"] = "CHILD_STILL_RUNNING_DO_NOT_RETRY"
    else:
        result["finding"] = "INTERRUPTED_NO_EXIT_RECORD"
    before = state["resource_start"]["memory_events"].get("oom_kill")
    after = state.get("resource_end", {}).get("memory_events", {}).get("oom_kill")
    result["cgroup_oom_kill_delta"] = after-before if after is not None and before is not None else None
    result["oom_attribution"] = "cgroup correlation only; SIGKILL alone is not OOM proof"
    result["remote_reconciliation_required"] = write and status != "REMOTE_VERIFICATION_RECORDED"
    result["automatic_retry"] = False
    result["platform_root_cause"] = "NOT_OBSERVED"
    return result
