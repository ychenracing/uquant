"""Fault injection: no network, broker writes, or genuine memory exhaustion."""
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress

from tools.cloud_guard import journal as guard
from tools.cloud_guard.tests.base import Base


class GuardTests(Base):
    def test_journal_wins_over_stale_latest(self):
        directory, state = guard.create(self.root, "atomic-boundary", "phase")
        old = (directory / "latest.json").read_bytes()
        state["status"] = "SUCCESS_REPORTED"
        guard.emit(directory, state, "finish")
        (directory / "latest.json").write_bytes(old)
        self.assertEqual(guard.load(directory)["status"], "SUCCESS_REPORTED")

    def test_partial_journal_is_not_silently_repaired(self):
        directory, _ = guard.create(self.root, "partial", "phase")
        with (directory / "events.jsonl").open("ab") as stream:
            stream.write(b'{"partial":')
        result = self.cli("inspect")["operations"][0]
        self.assertEqual(result["finding"], "JOURNAL_UNREADABLE_OR_INCOMPLETE_REVIEW_REQUIRED")

    def test_external_write_requires_readback(self):
        result = self.cli("begin", "--name", "github-small-write", "--kind", "external_write")
        ident = result["operation_id"]
        self.assertTrue(result["remote_reconciliation_required"])
        after = self.cli("finish", "--id", ident, "--outcome", "success")
        self.assertTrue(after["remote_reconciliation_required"])
        self.cli("finish", "--id", ident, "--outcome", "verified", expected=2)
        receipt = self.root / "readback-receipt.json"
        receipt.write_text('{"synthetic":true,"not_a_real_remote_verification":true}')
        verified = self.cli("finish", "--id", ident, "--outcome", "verified", "--receipt", receipt)
        self.assertEqual(verified["finding"], "REMOTE_VERIFICATION_RECORDED")
        # The test checks receipt recording, not the truth of a provider response.

    def test_metadata_omits_argv_environment_and_output_body(self):
        result = self.run_code("print('SYNTHETIC_PRIVATE_BODY')")
        directory = self.root / result["operation_id"]
        state_text = (directory / "latest.json").read_text()
        self.assertNotIn("SYNTHETIC_PRIVATE_BODY", state_text)
        self.assertNotIn("SYNTHETIC_PRIVATE_BODY", json.dumps(result))
        self.assertNotIn('"environment"', state_text)

    def test_killed_supervisor_leaves_durable_ambiguous_boundary(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "tools.cloud_guard", "--root", str(self.root),
             "run", "--name", "supervisor-killed", "--timeout", "5", "--heartbeat", ".02",
             "--cwd", str(self.root), "--", sys.executable, "-c", "import time; time.sleep(4)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        child = None
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                files = list(self.root.glob("*/latest.json"))
                if files:
                    state = json.loads(files[0].read_text())
                    if state.get("child_pid"):
                        child = state["child_pid"]
                        break
                time.sleep(.02)
            self.assertIsNotNone(child)
            process.kill()
            process.wait(timeout=2)
            result = self.cli("inspect")["operations"][0]
            self.assertEqual(result["finding"], "CHILD_STILL_RUNNING_DO_NOT_RETRY")
            os.killpg(child, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while guard.proc_start(child) and time.monotonic() < deadline:
                time.sleep(.02)
            result = self.cli("inspect")["operations"][0]
            self.assertEqual(result["finding"], "INTERRUPTED_NO_EXIT_RECORD")
            self.assertFalse(result["automatic_retry"])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            if child is not None:
                with suppress(ProcessLookupError):
                    os.killpg(child, signal.SIGKILL)

    def test_command_write_readback_preserves_execution_facts(self):
        result = self.run_code("print('synthetic write sentinel; no remote change')",
                               options=("--kind", "write"))
        self.assertTrue(result["remote_reconciliation_required"])
        receipt = self.root / "readback.json"
        receipt.write_text('{"synthetic":true}')
        verified = self.cli("finish", "--id", result["operation_id"], "--outcome", "verified",
                            "--receipt", receipt)
        self.assertFalse(verified["remote_reconciliation_required"])
        state = guard.load(self.root / result["operation_id"])
        self.assertEqual(state["returncode"], 0)
        self.assertEqual(state["execution_status_before_readback"], "EXITED")

    def test_nonfinite_timeout_is_rejected(self):
        self.cli("run", "--name", "bad-limit", "--timeout", "nan", "--",
                 sys.executable, "-c", "pass", expected=2)
        self.assertFalse(list(self.root.iterdir()))

    def test_operation_identifier_rejects_traversal(self):
        self.cli("finish", "--id", "../../escape", "--outcome", "unknown", expected=2)

    def test_snapshot_labels_present_not_historical(self):
        result = self.cli("snapshot")
        self.assertIn("not historical", result["scope"])
        self.assertIn("observed_at_utc", result["resources"])
