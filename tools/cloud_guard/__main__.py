"""Cloud command evidence, not a ChatGPT service hook. Linux/POSIX only."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from uuid import uuid4

from .admission import admission, reconcile_local
from .export import export_bundle, inspect_root
from .journal import NAME, atomic, checkpoint, create, emit, load, packed, resources, runtime_id
from .report import summarize
from .runner import command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("UQUANT_CLOUD_JOURNAL", str(Path.cwd() / ".cloud-task-journal")))
    subs = parser.add_subparsers(dest="action", required=True)
    run = subs.add_parser("run")
    run.add_argument("--name", required=True)
    run.add_argument("--kind", choices=("local", "read", "write"), default="local")
    run.add_argument("--cwd", default=".")
    run.add_argument("--timeout", type=float, required=True)
    run.add_argument("--heartbeat", type=float, default=5.0)
    run.add_argument("--disk-floor-mib", type=float, default=256)
    run.add_argument("--log-budget-mib", type=float, default=64)
    run.add_argument("--checkpoint")
    run.add_argument("command", nargs=argparse.REMAINDER)
    begin = subs.add_parser("begin")
    begin.add_argument("--name", required=True)
    begin.add_argument("--kind", choices=("external_read", "external_write", "phase"), required=True)
    begin.add_argument("--checkpoint")
    finish = subs.add_parser("finish")
    finish.add_argument("--id", required=True)
    finish.add_argument("--outcome", choices=("success", "error", "unknown", "verified"), required=True)
    finish.add_argument("--code", default="UNSPECIFIED")
    finish.add_argument("--receipt")
    reconcile = subs.add_parser("reconcile-local")
    reconcile.add_argument("--id", required=True)
    reconcile.add_argument("--receipt", required=True)
    subs.add_parser("inspect")
    subs.add_parser("snapshot")
    export = subs.add_parser("export")
    export.add_argument("--output", required=True)
    export.add_argument("--include-private-logs", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.action == "run":
        if not all(math.isfinite(v) and v > 0 for v in
                   (args.timeout, args.heartbeat, args.disk_floor_mib, args.log_budget_mib)):
            parser.error("limits must be positive")
        with admission(root, args.name) as lock_fd:
            args.lock_fd = lock_fd
            return command(args)
    if args.action == "begin":
        with admission(root, args.name):
            _, state = create(root, args.name, args.kind, args.checkpoint)
        print(json.dumps(summarize(state)))
    elif args.action == "finish":
        if not NAME.fullmatch(args.id) or not NAME.fullmatch(args.code):
            parser.error("invalid operation identifier or non-sensitive error code")
        directory = root / args.id
        state = load(directory)
        command_write = state["kind"] == "write"
        if command_write:
            if args.outcome != "verified" or state["status"] != "EXITED":
                parser.error("command write requires an exit record and verified readback")
            state["execution_status_before_readback"] = state["status"]
        elif state["kind"] not in {"external_read", "external_write", "phase"}:
            parser.error("finish only records external results or write readback")
        elif state["status"] not in {"STARTED", "OUTCOME_UNKNOWN", "SUCCESS_REPORTED", "ERROR_REPORTED"}:
            parser.error("operation already terminal")
        if args.outcome == "verified" and not args.receipt:
            parser.error("verified requires an existing remote-readback receipt")
        state.update(status={"success": "SUCCESS_REPORTED", "error": "ERROR_REPORTED",
                             "unknown": "OUTCOME_UNKNOWN", "verified": "REMOTE_VERIFICATION_RECORDED"}[args.outcome],
                     reported_code=args.code, receipt=checkpoint(args.receipt))
        # Receipt integrity is recorded, not semantic proof of a provider's state.
        emit(directory, state, "external_result_recorded")
        print(json.dumps(summarize(state)))
    elif args.action == "reconcile-local":
        print(json.dumps(reconcile_local(root, args.id, args.receipt)))
    elif args.action == "snapshot":
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {"scope": "current runtime, not historical failure telemetry",
                 "runtime_id": runtime_id(), "python": sys.version.split()[0], "resources": resources(root)}
        atomic(root / ("snapshot-" + uuid4().hex[:12] + ".json"), packed(value)+b"\n")
        print(json.dumps(value))
    elif args.action == "export":
        print(json.dumps(export_bundle(root, Path(args.output), args.include_private_logs)))
    else:
        print(json.dumps(inspect_root(root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        # No exception message: it could expose a command, URL or credential.
        print(json.dumps({"recorder_error_type": type(error).__name__, "status": "INCOMPLETE"}), file=sys.stderr)
        sys.exit(2)
