"""Integration checks for automatic admission and byte-exact preservation."""
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from tools.cloud_guard.tests.base import Base


class IntegrationTests(Base):
    def test_run_creates_verified_private_checkpoint_automatically(self):
        result = self.run_code("print('private-marker')")
        receipt = result["local_checkpoint"]
        self.assertFalse(receipt["remote_saved"])
        with zipfile.ZipFile(receipt["bundle"]) as archive:
            name = result["operation_id"] + "/stdout.private.log"
            self.assertEqual(archive.read(name), b"private-marker\n")
            self.assertTrue(json.loads(archive.read("MANIFEST.json"))["private_logs_included"])

    def test_metadata_export_does_not_publish_private_logs(self):
        self.run_code("print('PRIVATE-TEST-ONLY')")
        path = self.root.parent / (self.root.name + '-metadata.zip')
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.addCleanup(lambda: path.with_suffix('.zip.receipt.json').unlink(missing_ok=True))
        self.cli('export', '--output', path)
        with zipfile.ZipFile(path) as archive:
            self.assertFalse(any('private.log' in name for name in archive.namelist()))
            self.assertNotIn(b'PRIVATE-TEST-ONLY', b''.join(archive.read(n) for n in archive.namelist()))
        self.cli('export', '--output', path, expected=2)

    def test_same_write_name_is_blocked_until_readback(self):
        started = self.cli('begin', '--name', 'same-write', '--kind', 'external_write')
        self.cli('finish', '--id', started['operation_id'], '--outcome', 'success')
        self.cli('begin', '--name', 'same-write', '--kind', 'external_write', expected=2)
        receipt = self.root / 'synthetic-readback.json'
        receipt.write_text('{"synthetic":true}')
        self.cli('finish', '--id', started['operation_id'], '--outcome', 'verified', '--receipt', receipt)
        self.cli('begin', '--name', 'same-write', '--kind', 'external_write')

    def test_active_operation_blocks_duplicate_command(self):
        proc = subprocess.Popen([sys.executable, '-m', 'tools.cloud_guard', '--root', str(self.root),
            'run', '--name', 'same-job', '--timeout', '15', '--heartbeat', '.03', '--',
            sys.executable, '-c', 'import time;time.sleep(10)'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 4
            while not list(self.root.glob('*/latest.json')) and time.monotonic() < deadline:
                time.sleep(.03)
            self.cli('run', '--name', 'same-job', '--timeout', '2', '--',
                     sys.executable, '-c', 'pass', expected=2)
        finally:
            proc.terminate()
            proc.wait(timeout=6)

    def test_github_outputs_survive_command_wrapper(self):
        target = self.root / 'github-output'
        source = 'import os;open(os.environ["GITHUB_OUTPUT"],"a").write("artifact_path=sentinel\\n")'
        previous = os.environ.get('GITHUB_OUTPUT')
        os.environ['GITHUB_OUTPUT'] = str(target)
        try:
            self.run_code(source)
        finally:
            if previous is None:
                os.environ.pop('GITHUB_OUTPUT', None)
            else:
                os.environ['GITHUB_OUTPUT'] = previous
        self.assertEqual(target.read_text(), 'artifact_path=sentinel\n')

    def test_export_rejects_symlink(self):
        started = self.cli('begin', '--name', 'symlink-check', '--kind', 'phase')
        directory = self.root / started['operation_id']
        (directory / 'events.jsonl').unlink()
        (directory / 'events.jsonl').symlink_to(Path(__file__))
        self.cli('export', '--output', self.root / 'bad.zip', expected=2)
