"""One real post-close decision; no broker, simulated fills, or public report upload."""
from __future__ import annotations

# Chinese punctuation is intentional in user-facing reports.
# ruff: noqa: RUF001

import argparse
import contextlib
import os
import re
import shutil
import subprocess
import tempfile
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.daily_scan_market import SHANGHAI, SYMBOLS, calendar_now, refresh_inputs
from scripts.daily_scan_report import comparison, json_safe, project_signals, render_report
from scripts.daily_scan_store import (
    REPORT_BRANCH,
    REPORT_REPOSITORY,
    GitStore,
    fingerprint,
    open_private_store,
    put_json,
    read_json,
    verify_manifest,
)

OBSERVER_ID = "uquant-13-continuous-no-execution-v1"


def prior_result(root: Path, context: dict[str, Any]) -> dict[str, Any] | None:
    """Do not turn lost continuity or an unknown engine invocation into a new account."""
    for path in (root / "claims").glob("*.json"):
        claim = read_json(root, path.relative_to(root).as_posix())
        if claim.get("status") != "COMPLETE":
            raise RuntimeError("unreconciled prior decision claim: " + path.stem)
    if not (root / "latest.json").exists():
        if (root / "account.json").exists() or list((root / "reports").glob("*/result.json")):
            raise RuntimeError("missing latest receipt; refusing account reset")
        return None
    receipt = read_json(root, "latest.json")
    verify_manifest(root, receipt["files"])
    result = read_json(root, receipt["result_path"])
    if result["target_date"] not in (context["target_date"], context["previous_session"]):
        raise RuntimeError("observer has a trading-session gap; reconcile before continuing")
    if result.get("observer_id") != OBSERVER_ID:
        raise RuntimeError("different observer identity")
    return result


def compute_outputs(root: Path, work: Path, context: dict[str, Any], source_sha: str,
                    run_url: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Call the unchanged production engine exactly once after the durable claim."""
    from uquant.account import load_account, save_account
    from uquant.config import DEFAULT_CONFIG, config_fingerprint
    from uquant.engine import ProductionEngine
    from uquant.report import render_daily_report
    from uquant.types import AccountState

    day = context["target_date"]
    engine = ProductionEngine(work / "inputs")
    if previous is None:
        account = AccountState.empty(DEFAULT_CONFIG.initial_cash)
        account.account_migrations.append({"migration_type": "configuration_binding",
                                           "effective_config_sha256": config_fingerprint(DEFAULT_CONFIG)})
    else:
        account = load_account(root / "account.json")
        if previous["config_sha256"] != config_fingerprint(DEFAULT_CONFIG):
            raise RuntimeError("observer configuration changed; no automatic rebinding")
    save_account(account, work / "account_before.json")
    decision = engine.decide(symbols=SYMBOLS, as_of=day, account=account)
    account.pending_orders = list(decision.pending_orders)
    save_account(account, work / "account_after.json")
    raw = json_safe(asdict(decision))
    audit = read_json(work, "inputs/input_audit.json")
    coverage = raw["risk_summary"].get("sentinel_causal_coverage_status")
    result = {"schema": "uquant.daily-observer.v1", "observer_id": OBSERVER_ID,
              "observer_start": previous["observer_start"] if previous else day,
              "initial_cash": previous["initial_cash"] if previous else DEFAULT_CONFIG.initial_cash,
              "status": "COMPLETE" if coverage == "READY" else "PARTIAL",
              "target_date": day, "actual_market_date": day,
              "previous_session": context["previous_session"],
              "source_sha": source_sha, "run_url": run_url,
              "started_at": context["checked_at"], "computed_at": datetime.now(SHANGHAI).isoformat(),
              "economic_code_hash": account.code_hash,
              "config_sha256": config_fingerprint(DEFAULT_CONFIG),
              "data_manifest": engine.data.manifest(account.data_hash_symbols, source="live-akshare", as_of=day).to_dict(),
              "warnings": [] if coverage == "READY" else ["Risk Sentinel 资料覆盖未就绪；不表示市场安全"],
              "signals": project_signals(raw, audit["quotes"])}
    result["comparison"] = comparison(result, previous)
    put_json(work, "decision.json", raw)
    put_json(work, "result.json", result)
    (work / "report.md").write_text(render_report(result, render_daily_report(decision, account)), encoding="utf-8")
    return result


def run_once(store: GitStore, work: Path, context: dict[str, Any], source_sha: str,
             run_url: str) -> dict[str, Any]:
    root = store.root
    day = context["target_date"]
    if context["status"] != "READY":
        path = f"status/{day}.json"
        put_json(root, path, {**context, "run_url": run_url, "source_sha": source_sha,
                             "signals_generated": False})
        commit = store.publish([path], f"Record observer session status {day}")
        return {"status": context["status"], "target_date": day, "commit": commit}
    previous = prior_result(root, context)
    if previous is not None and previous["target_date"] == day:
        return {"status": "REUSED", "target_date": day, "source_sha": previous["source_sha"],
                "original_run_url": previous["run_url"]}
    log = work / "execution.log"
    claim_path = f"claims/{day}.json"
    claimed = False
    try:
        with log.open("w", encoding="utf-8") as stream, contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            refresh_inputs(work / "inputs", day, context["previous_session"])
            put_json(work, "session.json", context)
            # All contenders may fetch inputs, but only one non-force claim push can win.
            put_json(root, claim_path, {"status": "STARTED", "target_date": day,
                                       "run_url": run_url, "source_sha": source_sha,
                                       "started_at": datetime.now(SHANGHAI).isoformat()})
            store.publish([claim_path], f"Claim observer decision {day}")
            claimed = True
            result = compute_outputs(root, work, context, source_sha, run_url, previous)
    except Exception:
        with log.open("a", encoding="utf-8") as stream:
            traceback.print_exc(file=stream)
        # A failed/unknown claim push must never be followed by another write.
        if (root / claim_path).exists() and not claimed:
            raise RuntimeError("CLAIM_UNCONFIRMED; inspect remote state before retrying") from None
        failure = f"failures/{day}-{os.environ.get('GITHUB_RUN_ID', 'local')}"
        copied = []
        for origin in sorted(work.rglob("*")):
            if origin.is_file():
                destination = f"{failure}/{origin.relative_to(work).as_posix()}"
                (root / destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, root / destination)
                copied.append(destination)
        status = f"{failure}/status.json"
        put_json(root, status, {"status": "FAILED", "target_date": day, "run_url": run_url,
                               "source_sha": source_sha, "decision_claimed": claimed,
                               "finished_at": datetime.now(SHANGHAI).isoformat()})
        store.publish([*copied, status], f"Preserve observer failure {day}")
        raise RuntimeError("SCAN_FAILED; see private failure record; no valid signals published") from None
    dated = f"reports/{day}"
    paths = []
    for origin in sorted(work.rglob("*")):
        if origin.is_file():
            relative = origin.relative_to(work).as_posix()
            destination = relative if relative.startswith("inputs/") else f"{dated}/{relative}"
            (root / destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, root / destination)
            paths.append(destination)
    shutil.copyfile(work / "account_after.json", root / "account.json")
    paths.append("account.json")
    put_json(root, claim_path, {"status": "COMPLETE", "target_date": day, "source_sha": source_sha,
                               "run_url": run_url, "decision_digest": read_json(work, "decision.json")["decision_digest"]})
    paths.append(claim_path)
    put_json(root, "latest.json", {"result_path": f"{dated}/result.json",
                                   "files": {path: fingerprint(root / path) for path in paths}})
    # One ref advance publishes the account, original decision, input bytes and report together.
    commit = store.publish([*paths, "latest.json"], f"Publish observer report {day}")
    return {"status": result["status"], "target_date": day, "source_sha": source_sha, "commit": commit}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    args = parser.parse_args()
    args.work_root.mkdir(parents=True, exist_ok=False)
    try:
        source_sha = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        if re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
            raise ValueError("invalid source revision")
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if repository != "geniusgrok/uquant-cli" or not run_id.isdigit():
            raise ValueError("live scanning requires the approved public Actions runner")
        run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
        store = open_private_store(args.work_root / "state")
        context, dates = calendar_now(datetime.now(SHANGHAI))
        work = Path(tempfile.mkdtemp(dir=args.work_root, prefix="candidate-"))
        put_json(work, "calendar.json", dates)
        outcome = run_once(store, work, context, source_sha, run_url)
        put_json(args.work_root, "public_status.json", {key: outcome[key] for key in ("status", "target_date")})
        # Explicit allowlisted metadata only; no signal, account, traceback or provider body.
        print("UQUANT_SCAN_STATUS=" + outcome["status"])
        print("TARGET_DATE=" + outcome["target_date"])
        print("PRIVATE_REPORT_BRANCH=" + REPORT_REPOSITORY + "/" + REPORT_BRANCH)
        return 0
    except Exception:
        with (args.work_root / "failure.log").open("a", encoding="utf-8") as stream:
            traceback.print_exc(file=stream)
        print("UQUANT_SCAN_STATUS=FAILED_OR_BLOCKED; no successful daily scan is asserted")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
