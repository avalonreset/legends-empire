#!/usr/bin/env python3
"""Hermetic safety and host-layout tests for both installer entry points."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'bin/setup_multi_agent.py'
spec = importlib.util.spec_from_file_location('installer', SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SetupMultiAgentTests(unittest.TestCase):
    def invoke(self, home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, '-B', str(SCRIPT), '--home', str(home), *arguments],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_default_is_no_write_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            result = self.invoke(home)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('Dry run only', result.stdout)
            self.assertEqual([], list(home.iterdir()))
            for host in installer.DEFAULT_HOSTS:
                self.assertIn(f'PLANNED {host} ', result.stdout)

    def test_apply_is_idempotent_for_global_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(1, self.invoke(home, '--check').returncode)
            first = self.invoke(home, '--apply')
            self.assertEqual(0, first.returncode, first.stderr)
            expected = sorted(path.parent.name for path in (ROOT / 'skills').glob('*/SKILL.md'))
            for host in installer.DEFAULT_HOSTS:
                root = home / installer.HOST_PATHS[host]
                self.assertEqual(expected, sorted(path.parent.name for path in root.glob('*/SKILL.md')), host)
                for skill in expected:
                    self.assertTrue(installer.linked(root / skill))
                    self.assertEqual(ROOT / 'skills' / skill, (root / skill).resolve())
            second = self.invoke(home, '--apply')
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(len(expected) * len(installer.DEFAULT_HOSTS), second.stdout.count('READY'))
            self.assertEqual(0, self.invoke(home, '--check').returncode)
            self.assertFalse((home / '.codex').exists())

    def test_conflict_is_preserved_and_nothing_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            conflict = home / '.agents/skills/wiki'
            conflict.mkdir(parents=True)
            marker = conflict / 'mine'
            marker.write_text('preserve', encoding='utf-8')
            result = self.invoke(home, '--apply')
            self.assertEqual(2, result.returncode)
            self.assertEqual('preserve', marker.read_text(encoding='utf-8'))
            self.assertFalse((home / '.grok').exists())
            self.assertFalse((home / '.agents/skills/save').exists())

    def test_workspace_all_is_explicit_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            missing = self.invoke(home, '--apply', '--host', 'all')
            self.assertEqual(2, missing.returncode)
            self.assertEqual([], list(home.iterdir()))
            workspace = home / 'work space [brackets]'
            workspace.mkdir()
            applied = self.invoke(home, '--apply', '--host', 'all', '--host', 'grok',
                                  '--scope', 'workspace', '--workspace', str(workspace))
            self.assertEqual(0, applied.returncode, applied.stderr)
            expected = len(list((ROOT / 'skills').glob('*/SKILL.md')))
            self.assertEqual(7 * expected, applied.stdout.count('CREATED'))
            for host, relative in installer.HOST_PATHS.items():
                relative = '.opencode/skills' if host == 'opencode' else relative
                self.assertEqual(expected, len(list((workspace / relative).glob('*/SKILL.md'))), host)
            self.assertFalse((home / '.grok').exists())

    def test_parent_links_cannot_redirect_apply(self):
        for relative in ('.agents', '.agents/skills', '.cursor', '.cursor/skills'):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                workspace = base / 'workspace'
                outside = base / 'outside'
                workspace.mkdir()
                outside.mkdir()
                link = workspace / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                installer.create_link(outside, link)
                host = 'codex' if relative.startswith('.agents') else 'cursor'
                for mode in ('--dry-run', '--apply'):
                    result = self.invoke(workspace, mode, '--host', host, '--workspace', str(workspace))
                    self.assertEqual(2, result.returncode, result.stdout)
                    self.assertIn('parent is a symlink or junction', result.stderr)
                self.assertEqual([], list(outside.iterdir()))

    def test_foreign_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            outside = base / 'outside'
            outside.mkdir()
            destination = base / '.grok/skills/wiki'
            destination.parent.mkdir(parents=True)
            installer.create_link(outside, destination)
            result = self.invoke(base, '--apply', '--host', 'grok')
            self.assertEqual(2, result.returncode)
            self.assertEqual(outside, destination.resolve())
            self.assertFalse((destination.parent / 'save').exists())

    def test_bad_arguments_do_not_write(self):
        for args in (('--host', 'unknown'), ('--apply', '--check'),
                     ('--scope', 'workspace'), ('--workspace', 'nonexistent-workspace')):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                self.assertEqual(2, self.invoke(base, *args).returncode)
                self.assertEqual([], list(base.iterdir()))

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink-specific case')
    def test_dangling_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            link = base / '.grok/skills/wiki'
            link.parent.mkdir(parents=True)
            link.symlink_to(base / 'missing', target_is_directory=True)
            self.assertEqual(2, self.invoke(base, '--apply', '--host', 'grok').returncode)
            self.assertTrue(link.is_symlink())

    @unittest.skipIf(os.name == 'nt', 'Bash wrapper uses native POSIX Python')
    def test_bash_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['bash', str(ROOT / 'bin/setup-multi-agent.sh'), '--home', directory,
                                     '--check', '--host', 'claude'], text=True, capture_output=True)
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertIn('PLANNED claude', result.stdout)
            self.assertEqual([], list(Path(directory).iterdir()))

    @unittest.skipUnless(shutil.which('pwsh'), 'PowerShell not installed')
    def test_powershell_entry_point_preserves_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['pwsh', '-NoProfile', '-File', str(ROOT / 'bin/setup-multi-agent.ps1'),
                                     '-HomeDirectory', directory, '-Mode', 'check', '-Hosts', 'gemini'],
                                    text=True, capture_output=True)
            self.assertEqual(1, result.returncode, result.stderr)
            self.assertIn('PLANNED gemini', result.stdout)
            self.assertEqual([], list(Path(directory).iterdir()))


if __name__ == '__main__':
    unittest.main()
