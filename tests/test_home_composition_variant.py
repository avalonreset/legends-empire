"""Shared-root composition accepts only the stock generated contract.

There is no drift exemption: any inserted paragraph, any recorded variant
marker, and any byte edit refuses. Text below is synthetic.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import home_profile as profile
from claude_empire.transaction import sha256_bytes

SYNTHETIC_INSERT = (
    b"Temporary example routing note for Example Owner through 2099-01-01.\n"
    b"Prefer example workers for example tasks.\n"
    b"\n"
)
INSERT_AT = 62


def inserted_bytes():
    stock = profile.composed_agents()
    return stock[:INSERT_AT] + SYNTHETIC_INSERT + stock[INSERT_AT:]


class CompositionDriftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = b"# Synthetic Home contract\n"
        self.owner = b"# Synthetic owner contract\n"
        self.pin = {"commit": "test-only", "release": "synthetic",
                    "files": {"AGENTS.md": sha256_bytes(self.home)}}
        (self.root / profile.HOME_CONTRACT).parent.mkdir(parents=True, exist_ok=True)
        (self.root / profile.HOME_CONTRACT).write_bytes(self.home)
        (self.root / profile.OWNER_CONTRACT).write_bytes(self.owner)

    def write_state(self, agents: bytes, marker: str | None = None):
        state = {"schema": profile.COMPOSITION_SCHEMA, "commit": "test-only",
                 "release": "synthetic", "home_agents_sha256": sha256_bytes(self.home),
                 "owner_agents_sha256": sha256_bytes(self.owner),
                 "generated_agents_sha256": sha256_bytes(agents)}
        if marker is not None:
            state["generated_agents_variant"] = marker
        (self.root / profile.COMPOSITION).write_bytes(profile.encoded(state))
        (self.root / "AGENTS.md").write_bytes(agents)

    def test_module_exposes_no_variant_api(self):
        for name in ("composed_agents_variant", "COMPOSITION_VARIANT_ID",
                     "COMPOSITION_VARIANT_AGENTS_SHA256"):
            self.assertFalse(hasattr(profile, name), name)

    def test_stock_composition_valid(self):
        self.write_state(profile.composed_agents())
        self.assertTrue(profile.composition_valid(self.root, self.pin))

    def test_inserted_paragraph_refused_even_with_matching_state_hash(self):
        self.write_state(inserted_bytes())
        self.assertFalse(profile.composition_valid(self.root, self.pin))

    def test_inserted_paragraph_with_variant_marker_refused(self):
        self.write_state(inserted_bytes(), "example-variant")
        self.assertFalse(profile.composition_valid(self.root, self.pin))

    def test_stock_bytes_with_variant_marker_and_foreign_hash_refused(self):
        self.write_state(profile.composed_agents(), "example-variant")
        raw = json.loads((self.root / profile.COMPOSITION).read_bytes())
        raw["generated_agents_sha256"] = sha256_bytes(inserted_bytes())
        (self.root / profile.COMPOSITION).write_bytes(profile.encoded(raw))
        self.assertFalse(profile.composition_valid(self.root, self.pin))

    def test_single_bit_flip_refused(self):
        mutated = bytearray(profile.composed_agents())
        mutated[100] ^= 0x01
        self.write_state(bytes(mutated))
        self.assertFalse(profile.composition_valid(self.root, self.pin))

    def test_trailing_owner_line_refused(self):
        self.write_state(profile.composed_agents() + b"\nOne more example line.\n")
        self.assertFalse(profile.composition_valid(self.root, self.pin))


if __name__ == "__main__":
    unittest.main()
