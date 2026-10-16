"""Exercise the production cron wrapper against a local bare Git remote; no network/LLM."""
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CronPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.repo = self.root / 'checkout'
        self.repo.mkdir()
        self.git('init', '--bare', str(self.remote), cwd=self.root)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        for path in ('ops', 'data', 'collector', '.venv/bin', '.runtime'):
            (self.repo / path).mkdir(parents=True)
        shutil.copy(ROOT / 'ops/run-collector.sh', self.repo / 'ops/run-collector.sh')
        (self.repo / '.venv/bin/python').symlink_to(sys.executable)
        (self.repo / '.gitignore').write_text('.runtime/\n.venv/\n__pycache__/\n')
        (self.repo / 'requirements.txt').write_text('')
        (self.repo / '.runtime/requirements.sha256').write_text(hashlib.sha256(b'').hexdigest())
        (self.repo / 'data/news.json').write_text('{"value": 1}\n')
        (self.repo / 'collector/__main__.py').write_text('''import json,os,sys
from pathlib import Path
p=Path('data/news.json')
if len(sys.argv)>1 and sys.argv[1]=='validate':
 json.loads(p.read_text())
else:
 p.write_text('{"value": 2}\\n')
 if os.environ.get('SIMULATE_FAILURE'): raise SystemExit(1)
''')
        self.git('add', '.')
        self.git('commit', '-m', 'test setup')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def run_job(self, **env):
        return subprocess.run(['bash', 'ops/run-collector.sh'], cwd=self.repo, env={**os.environ, 'NEWS_BRANCH': 'main', **env}, capture_output=True, text=True)

    def test_one_commit_then_unchanged_run(self):
        first = self.run_job()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('rev-parse', 'origin/main'))
        second = self.run_job()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('No content changes', second.stdout)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')

    def test_failed_collection_restores_data_and_index(self):
        before = (self.repo / 'data/news.json').read_bytes()
        result = self.run_job(SIMULATE_FAILURE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repo / 'data/news.json').read_bytes(), before)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')

    def test_push_failure_keeps_commit_and_next_run_recovers_it(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        result = self.run_job()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')
        self.assertEqual(self.git('rev-list', '--count', 'origin/main'), '1')
        hook.unlink()
        retry = self.run_job()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertIn('Recovered the pending data commit', retry.stdout)
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('rev-parse', 'origin/main'))
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '2')

    def test_overlapping_job_skips_without_a_commit(self):
        with (self.repo / '.runtime/collector.lock').open('w') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_job()
        self.assertEqual(result.returncode, 0)
        self.assertIn('Another collection is running', result.stdout)
        self.assertEqual(self.git('rev-list', '--count', 'HEAD'), '1')

    def test_dirty_checkout_is_not_overwritten(self):
        (self.repo / 'data/news.json').write_text('operator edit')
        result = self.run_job()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.repo / 'data/news.json').read_text(), 'operator edit')
