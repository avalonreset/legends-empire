"""Qualified pins follow the inspected release; finish batches stay bounded."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import home_adapter as adapter, home_install as installer, home_profile as profile, home_upgrade as upgrade
from claude_empire.transaction import apply_bundle
import test_home_profile as fixture
from test_home_profile import STAMP


class QualifiedPinTests(unittest.TestCase):
    setUpBase = fixture.ProfileTests.setUp
    apply = fixture.ProfileTests.apply
    prepare = fixture.ProfileTests.prepare
    install = fixture.ProfileTests.install

    def setUp(self):
        self.setUpBase()
        self.prepare()

    def test_every_install_bundle_records_the_pin_commit(self):
        seen = []

        def spy(root, operation, **kwargs):
            seen.append(operation)
            return apply_bundle(root, operation, **kwargs)

        with patch.object(installer, "apply_bundle", side_effect=spy):
            self.install()
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual({op.get("home_pin_commit") for op in seen}, {"test-only"})

    def test_compose_uses_the_pin_of_the_inspected_release(self):
        self.install()
        report = adapter.inspect_home(self.source)
        archived = json.loads(self.pin.read_text(encoding="utf-8"))
        (self.base / "home-compatibility-v0.0.1.json").write_bytes(profile.encoded(archived))
        newer = {**archived, "release": "v0.0.2", "commit": "newer-test-only",
                 "files": {**archived["files"], "AGENTS.md": "0" * 64}}
        self.pin.write_bytes(profile.encoded(newer))
        with patch.object(adapter, "inspect_home", return_value=report):
            bundle = profile.compose_bundle(self.root, self.source, "compose", STAMP)
        self.assertTrue(bundle["writes"])
        writes = {w["path"]: w["content"] for w in bundle["writes"]}
        self.assertEqual(json.loads(writes[profile.COMPOSITION])["commit"], "test-only")


class FinishSliceTests(unittest.TestCase):
    def meta(self, count):
        data = {f"Workspaces/P{i}/binding.json": b"{}" for i in range(count)}
        data[installer.STATE] = b"{}"
        return data

    def test_small_finish_is_one_transaction(self):
        chunks = upgrade.finish_slices(self.meta(130))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 131)

    def test_large_finish_is_bounded_and_state_is_last(self):
        for count in (149, 150, 151, 299, 300, 450):
            metadata = self.meta(count)
            chunks = upgrade.finish_slices(metadata)
            self.assertTrue(all(0 < len(c) <= installer.BATCH_SIZE for c in chunks), count)
            self.assertEqual(sum(len(c) for c in chunks), len(metadata))
            self.assertIn(installer.STATE, chunks[-1])
            self.assertFalse(any(installer.STATE in c for c in chunks[:-1]))

    def test_state_only_and_no_state(self):
        self.assertEqual(upgrade.finish_slices({installer.STATE: b"{}"}), [{installer.STATE: b"{}"}])
        self.assertEqual(len(upgrade.finish_slices({"a": b"1", "b": b"2"}, size=1)), 2)


@unittest.skipIf(os.name == "nt" or not Path("/tmp").is_dir(), "POSIX /tmp required")
class DarwinSymlinkDirTests(unittest.TestCase):
    def setUp(self):
        st = os.lstat("/tmp")
        self.key = (st.st_dev, st.st_ino)
        self.fake = lambda s: (s.st_dev, s.st_ino) == self.key

    def test_darwin_system_link_is_allowed(self):
        with patch.object(adapter, "is_name_surrogate", side_effect=self.fake), \
                patch.object(adapter.sys, "platform", "darwin"):
            self.assertTrue(adapter.safe_root("/tmp").is_dir())

    def test_other_platforms_still_refuse(self):
        with patch.object(adapter, "is_name_surrogate", side_effect=self.fake), \
                patch.object(adapter.sys, "platform", "linux"):
            with self.assertRaises(Exception):
                adapter.safe_root("/tmp")


if __name__ == "__main__":
    unittest.main()
