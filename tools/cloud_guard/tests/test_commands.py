"""Fault injection: no network, broker writes, or genuine memory exhaustion."""
import hashlib
import os

from tools.cloud_guard import journal as guard
from tools.cloud_guard.report import summarize
from tools.cloud_guard.tests.base import Base


class GuardTests(Base):
    def test_success_and_log_integrity(self):
        result = self.run_code("print('hello')")
        self.assertEqual(result["finding"], "COMMAND_OK")
        directory = self.root / result["operation_id"]
        state = guard.load(directory)
        log = directory / "stdout.private.log"
        self.assertEqual(log.read_bytes(), b"hello\n")
        self.assertEqual(state["logs"][log.name]["sha256"], hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertEqual(os.stat(log).st_mode & 0o777, 0o600)

    def test_nonzero_exit_not_platform_error(self):
        result = self.run_code("import sys; sys.exit(7)", expected=1)
        self.assertEqual(result["returncode"], 7)
        self.assertEqual(result["finding"], "COMMAND_NONZERO_EXIT")
        self.assertEqual(result["platform_root_cause"], "NOT_OBSERVED")

    def test_missing_program(self):
        result = self.cli("run", "--name", "missing", "--timeout", "1", "--cwd", self.root,
                          "--", str(self.root / "not-a-program"), expected=1)
        self.assertEqual(result["finding"], "SPAWN_ERROR")
        self.assertEqual(result["error_type"], "FileNotFoundError")

    def test_real_supervisor_timeout(self):
        result = self.run_code("import time; time.sleep(8)", expected=1,
                               options=("--timeout", ".15"))
        self.assertEqual(result["finding"], "SUPERVISOR_TIMEOUT")

    def test_sigkill_does_not_claim_oom(self):
        result = self.run_code("import os,signal; os.kill(os.getpid(),signal.SIGKILL)", expected=1)
        self.assertEqual(result["finding"], "PROCESS_SIGNAL")
        self.assertEqual(result["signal"], 9)
        self.assertIn("not OOM proof", result["oom_attribution"])

    def test_log_budget_stops_without_discarding_generated_bytes(self):
        result = self.run_code("import os,time\nwhile True:\n os.write(1,b'x'*8192); time.sleep(.01)",
                               expected=1, options=("--log-budget-mib", ".02"))
        self.assertEqual(result["finding"], "LOG_BUDGET")
        self.assertGreater((self.root / result["operation_id"] / "stdout.private.log").stat().st_size, 20000)

    def test_disk_floor_without_filling_disk(self):
        result = self.run_code("import time; time.sleep(8)", expected=1,
                               options=("--disk-floor-mib", "1000000000000"))
        self.assertEqual(result["finding"], "DISK_FLOOR")

    def test_interruption_is_unknown_not_timeout(self):
        directory, state = guard.create(self.root, "synthetic-orphan", "local")
        state.update(status="RUNNING", supervisor_pid=1073741824, supervisor_start="not-real",
                     child_pid=1073741823, child_start="not-real")
        guard.emit(directory, state, "synthetic-crash-boundary")
        result = self.cli("inspect")["operations"][0]
        self.assertEqual(result["finding"], "INTERRUPTED_NO_EXIT_RECORD")
        self.assertFalse(result["automatic_retry"])

    def test_runtime_change_does_not_claim_cause(self):
        _, state = guard.create(self.root, "synthetic-runtime", "local")
        state.update(status="RUNNING", runtime_id="different-runtime")
        self.assertEqual(summarize(state)["finding"], "RUNTIME_CHANGED_NO_TERMINAL_RECORD")
