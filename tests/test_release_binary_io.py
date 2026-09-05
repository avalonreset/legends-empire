#!/usr/bin/env python3
"""Release auditing must inspect exact ZIP bytes on every operating system."""

from __future__ import annotations

import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claude_obsidian.release as release_module


class ReleaseBinaryIOTests(unittest.TestCase):
    def test_audit_preserves_binary_archive_before_structure_validation(self):
        # Windows text-mode descriptors translate CRLF and stop at Ctrl-Z.
        # ZIP headers, offsets, hashes, and payloads must see neither change.
        for content in (b"first\r\nsecond", b"first\x1asecond"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("example.txt", content)
                expected = buffer.getvalue()
                artifact = Path(directory) / "example.zip"
                artifact.write_bytes(expected)

                with mock.patch.object(
                    release_module,
                    "_preflight_zip_structure",
                    wraps=release_module._preflight_zip_structure,
                ) as validate_structure:
                    report = release_module.audit_artifact(artifact)

                validate_structure.assert_called_once()
                self.assertEqual(expected, validate_structure.call_args.args[0])
                # This fixture deliberately lacks release metadata. It must
                # reach those checks, rather than fail as a corrupted ZIP.
                self.assertFalse(report["ok"])
                self.assertNotIn(
                    "invalid_archive", {error["code"] for error in report["errors"]}
                )


if __name__ == "__main__":
    unittest.main()
