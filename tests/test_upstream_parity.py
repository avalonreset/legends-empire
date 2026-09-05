#!/usr/bin/env python3
"""Offline parity and attribution guard for the Legends distribution."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpstreamParityTests(unittest.TestCase):
    def test_preserved_upstream_behavior_is_byte_exact(self):
        manifest = json.loads((ROOT / 'config/upstream-parity.json').read_text(encoding='utf-8'))
        self.assertEqual('ad67087cad22ad84cc3288f915588ae42c0c2b44', manifest['commit'])
        self.assertGreater(len(manifest['files']), 70)
        patches = manifest.get('reviewed_patches', {})
        self.assertEqual({'claude_obsidian/release.py'}, set(patches))
        for record in manifest['files']:
            relative, expected = record['path'], record['sha256']
            with self.subTest(path=relative):
                if relative in patches:
                    self.assertEqual(expected, patches[relative]['upstream_sha256'])
                    self.assertTrue(patches[relative]['reason'])
                    expected = patches[relative]['distribution_sha256']
                self.assertEqual(expected, hashlib.sha256((ROOT / relative).read_bytes()).hexdigest())

    def test_original_copyright_and_prominent_credit_survive(self):
        license_text = (ROOT / 'LICENSE').read_text(encoding='utf-8')
        self.assertIn('Copyright (c) 2026 AgriciDaniel (AI Marketing Hub)', license_text)
        self.assertIn('MIT License', license_text)
        opening = (ROOT / 'README.md').read_text(encoding='utf-8')[:1800]
        self.assertIn('Daniel Agrici', opening)
        self.assertIn('https://github.com/AgriciDaniel/claude-obsidian', opening)
        self.assertIn('provider-neutral', opening)

    def test_four_majors_are_contractually_represented(self):
        contract = json.loads((ROOT / 'config/product-contract.json').read_text(encoding='utf-8'))
        hosts = {entry['id'] for entry in contract['supported_hosts']}
        self.assertTrue({'grok', 'codex-cli', 'gemini-cli', 'claude-code'} <= hosts)
        self.assertTrue((ROOT / 'skills/legends-obsidian/SKILL.md').is_file())


if __name__ == '__main__':
    unittest.main()
