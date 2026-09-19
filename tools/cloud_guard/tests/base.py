"""Fault injection: no network, broker writes, or genuine memory exhaustion."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-m", "tools.cloud_guard",
                                 "--root", str(self.root), *map(str, args)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stderr.decode())
        return json.loads(result.stdout) if result.stdout else None

    def run_code(self, source, expected=0, options=()):
        return self.cli("run", "--name", "sentinel", "--timeout", "3", "--heartbeat", ".02",
                        "--cwd", self.root, *options, "--", sys.executable, "-c", source,
                        expected=expected)
