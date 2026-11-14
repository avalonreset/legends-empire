#!/usr/bin/env python3
"""Ontology bundle validator tests (operation_type "ontology", spec 5.5).

Every rule has at least one accepted and one rejected case. Secret-like
fixtures are assembled at runtime so the repository never contains a literal
credential pattern. All vault data is invented.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claude_empire.transaction as transaction_module
from claude_empire import ontology_grammar as rules
from claude_empire.home_adapter import PIN
from claude_empire.transaction import (
    BUNDLE_SCHEMA,
    TransactionValidationError,
    apply_bundle,
    inspect_bundle,
)

NOTE = "wiki/entities/acme-widgets.md"
BODY = b"# Acme Widgets\n\nA fictional supplier used by tests.\n"
BASE = b"---\ntitle: Acme Widgets\ntags: [vendor]\n---\n" + BODY
EVIDENCE_FIXTURE = ROOT / "tests" / "fixtures" / "research-evidence" / "dataforseo-completed"
EVIDENCE_WORKSPACE = "synthetic-client"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fm(*lines: str, body: bytes = BODY, nl: str = "\n", bom: bool = False) -> bytes:
    text = nl.join(("---", *lines, "---")) + nl
    data = text.encode("utf-8") + body
    return b"\xef\xbb\xbf" + data if bom else data


def base_lines(*extra: str) -> tuple[str, ...]:
    return ("title: Acme Widgets", "tags: [vendor]", *extra)


def secret_openai() -> str:
    return "sk" + "-" + "Q7" * 12


def secret_aws() -> str:
    return "AK" + "IA" + "Z" * 16


def secret_github() -> str:
    return "gh" + "p_" + "x9" * 18


def secret_pem() -> str:
    return "-" * 5 + "BEGIN" + " PRIVATE KEY" + "-" * 5


class OntologyTestCase(unittest.TestCase):
    counter = 0

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self._tmp.name) / "vault"
        self.vault.mkdir()
        for directory in (".obsidian", "wiki", ".raw"):
            (self.vault / directory).mkdir()
        self.put(NOTE, BASE)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def put(self, relative: str, data: bytes) -> None:
        target = self.vault / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read(self, relative: str) -> bytes:
        return (self.vault / relative).read_bytes()

    def op(
        self,
        writes: dict[str, bytes],
        *,
        preconditions: dict[str, str | None] | None = None,
        certification: object = None,
        mode: str = "replace",
    ) -> dict:
        OntologyTestCase.counter += 1
        expected = {
            path: (sha(self.read(path)) if (self.vault / path).exists() else None)
            for path in writes
        }
        bundle = {
            "schema": BUNDLE_SCHEMA,
            "operation_id": f"ontology-test-{OntologyTestCase.counter:04d}",
            "operation_type": "ontology",
            "expected_hashes": expected,
            "read_preconditions": dict(preconditions or {}),
            "writes": [
                {"path": path, "mode": mode, "content": content.decode("utf-8")}
                for path, content in writes.items()
            ],
        }
        if certification is not None:
            bundle["certification"] = certification
        return bundle

    def pin(self, *paths: str) -> dict[str, str]:
        return {path: sha(self.read(path)) for path in paths}

    def accept(self, writes: dict[str, bytes], **kwargs) -> dict:
        return inspect_bundle(self.vault, self.op(writes, **kwargs))

    def reject(self, code: str, writes: dict[str, bytes], **kwargs) -> TransactionValidationError:
        with self.assertRaises(TransactionValidationError) as caught:
            inspect_bundle(self.vault, self.op(writes, **kwargs))
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        return caught.exception


@contextmanager
def hidden_from_listing(*names: str):
    """Simulate a filesystem alias: the name resolves but is not enumerated."""

    real = transaction_module.os.scandir

    class Listing:
        def __init__(self, target):
            self.inner = real(target)

        def __enter__(self):
            entries = self.inner.__enter__()
            return (entry for entry in entries if entry.name not in names)

        def __exit__(self, *exc):
            return self.inner.__exit__(*exc)

    with patch.object(transaction_module.os, "scandir", Listing):
        yield


class ScopeTests(OntologyTestCase):
    def test_replace_of_wiki_note_is_accepted_and_applies_exact_bytes(self) -> None:
        new = fm(*base_lines("entity_id: acme-widgets", "fact.city: Springfield"))
        plan = self.accept({NOTE: new})
        self.assertTrue(plan["valid"])
        self.assertEqual(plan["ontology"]["approval"], "standard")
        apply_bundle(self.vault, self.op({NOTE: new}))
        self.assertEqual(self.read(NOTE), new)

    def test_ontology_type_is_registered(self) -> None:
        self.assertIn("ontology", transaction_module.OPERATION_TYPES)

    def test_create_mode_is_rejected(self) -> None:
        self.reject(
            "ONTOLOGY_WRITE_MODE",
            {"wiki/entities/new-note.md": fm("entity_id: new-note")},
            mode="create",
        )

    def test_non_markdown_target_is_rejected(self) -> None:
        self.put("wiki/data.json", b"{}\n")
        self.reject("ONTOLOGY_TARGET_SCOPE", {"wiki/data.json": b'{"a": 1}\n'})

    def test_lexical_exclusions_are_casefolded(self) -> None:
        for target in (
            "AGENTS.md",
            "Agents.MD",
            "claude.md",
            "EMPIRE-ENTRY.MD",
            ".OBSIDIAN/notes.md",
            ".Raw/source.md",
            ".vault-meta/x.md",
            "wiki/.Git/x.md",
            "wiki/Node_Modules/x.md",
            "wiki/x.txt",
        ):
            with self.subTest(target=target):
                with self.assertRaises(rules.OntologyRuleError) as caught:
                    rules.check_target_lexical(target, "replace")
                self.assertEqual(caught.exception.code, "ONTOLOGY_TARGET_SCOPE")
        rules.check_target_lexical("wiki/People/Someone.MD", "replace")

    def test_standalone_profile_rejects_targets_outside_wiki(self) -> None:
        target = "Projects/Active/launch.md"
        self.put(target, BASE)
        self.reject("ONTOLOGY_TARGET_SCOPE", {target: fm(*base_lines("entity_id: launch"))})

    def test_configured_roots_allow_their_targets(self) -> None:
        target = "Projects/Active/launch.md"
        self.put(target, BASE)
        self.put(".claude-empire.json", json.dumps({"ontology": {"roots": ["Projects/Active"]}}).encode())
        self.accept({target: fm(*base_lines("entity_id: launch"))})
        self.reject("ONTOLOGY_TARGET_SCOPE", {NOTE: fm(*base_lines("entity_id: acme-widgets"))})

    def test_configured_exclude_cannot_be_bypassed_by_case(self) -> None:
        target = "wiki/Private/plan.md"
        self.put(target, BASE)
        self.put(
            ".claude-empire.json",
            json.dumps({"ontology": {"roots": ["wiki"], "exclude": ["wiki/private"]}}).encode(),
        )
        self.reject("ONTOLOGY_TARGET_SCOPE", {target: fm(*base_lines("entity_id: plan"))})
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets"))})

    def test_malformed_configuration_fails_closed(self) -> None:
        for raw in (b"{not json", b'{"ontology": {"roots": "wiki"}}', b'{"ontology": {"roots": ["../x"]}}'):
            with self.subTest(raw=raw):
                self.put(".claude-empire.json", raw)
                self.reject("ONTOLOGY_CONFIG_INVALID", {NOTE: fm(*base_lines("entity_id: acme-widgets"))})

    def _install_home(self, *, status: str = "installed", managed: str = "wiki/home-managed.md") -> None:
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        state = {"schema": "test", "status": status, "commit": pin["commit"], "files": {managed: "0" * 64}}
        self.put(rules.HOME_STATE_PATH, json.dumps(state).encode())

    def test_shared_root_profile_extends_roots_and_protects_home_files(self) -> None:
        target = "Projects/Active/launch.md"
        self.put(target, BASE)
        self.put("wiki/home-managed.md", BASE)
        self._install_home()
        self.accept({target: fm(*base_lines("entity_id: launch"))})
        self.reject(
            "ONTOLOGY_TARGET_SCOPE",
            {"wiki/home-managed.md": fm(*base_lines("entity_id: home"))},
        )

    def test_unfinished_home_install_keeps_standalone_roots(self) -> None:
        target = "Projects/Active/launch.md"
        self.put(target, BASE)
        self._install_home(status="installing")
        self.reject("ONTOLOGY_TARGET_SCOPE", {target: fm(*base_lines("entity_id: launch"))})

    def test_malformed_home_state_fails_closed(self) -> None:
        self.put(rules.HOME_STATE_PATH, b"[1, 2")
        self.reject("ONTOLOGY_CONFIG_INVALID", {NOTE: fm(*base_lines("entity_id: acme-widgets"))})

    def test_casefold_alias_of_existing_directory_is_rejected(self) -> None:
        self.put("wiki/Vendors/acme.md", BASE)
        self.reject("CASEFOLD_PATH_ALIAS", {"wiki/vendors/acme.md": fm(*base_lines("entity_id: acme"))})

    def test_unlisted_but_resolvable_name_is_rejected(self) -> None:
        new = fm(*base_lines("entity_id: acme-widgets"))
        with hidden_from_listing("acme-widgets.md"):
            with self.assertRaises(TransactionValidationError) as caught:
                inspect_bundle(self.vault, self.op({NOTE: new}))
        self.assertEqual(caught.exception.code, "PORTABLE_PATH_ALIAS")
        with hidden_from_listing("entities"):
            with self.assertRaises(TransactionValidationError) as caught:
                inspect_bundle(self.vault, self.op({NOTE: new}))
        self.assertEqual(caught.exception.code, "PORTABLE_PATH_ALIAS")
        self.accept({NOTE: new})

    def test_bundle_limits(self) -> None:
        writes = [(f"wiki/n{index}.md", b"", b"") for index in range(rules.MAX_BUNDLE_TARGETS + 1)]
        with self.assertRaises(rules.OntologyRuleError) as caught:
            rules.validate_bundle({}, writes, vault_root=self.vault, read=lambda _: None, preconditions={})
        self.assertEqual(caught.exception.code, "ONTOLOGY_BUNDLE_LIMIT")
        big = [("wiki/n.md", b"", b"x" * (rules.MAX_BUNDLE_BYTES + 1))]
        with self.assertRaises(rules.OntologyRuleError) as caught:
            rules.validate_bundle({}, big, vault_root=self.vault, read=lambda _: None, preconditions={})
        self.assertEqual(caught.exception.code, "ONTOLOGY_BUNDLE_LIMIT")

    def test_generic_operations_have_no_ontology_summary(self) -> None:
        bundle = self.op({NOTE: BASE + b"more\n"})
        bundle["operation_type"] = "generic"
        self.assertNotIn("ontology", inspect_bundle(self.vault, bundle))


class ByteIdentityTests(OntologyTestCase):
    def test_bom_is_preserved_when_present(self) -> None:
        self.put(NOTE, fm(*base_lines(), bom=True))
        new = fm(*base_lines("entity_id: acme-widgets"), bom=True)
        self.accept({NOTE: new})
        apply_bundle(self.vault, self.op({NOTE: new}))
        self.assertEqual(self.read(NOTE), new)

    def test_bom_added_removed_or_doubled_is_rejected(self) -> None:
        self.reject("ONTOLOGY_BOM_MISMATCH", {NOTE: fm(*base_lines("entity_id: acme-widgets"), bom=True)})
        self.put(NOTE, fm(*base_lines(), bom=True))
        self.reject("ONTOLOGY_BOM_MISMATCH", {NOTE: fm(*base_lines("entity_id: acme-widgets"))})
        doubled = b"\xef\xbb\xbf" + fm(*base_lines("entity_id: acme-widgets"), bom=True)
        self.reject("ONTOLOGY_BOM_MISMATCH", {NOTE: doubled})

    def test_crlf_note_keeps_crlf(self) -> None:
        crlf_body = BODY.replace(b"\n", b"\r\n")
        self.put(NOTE, fm(*base_lines(), nl="\r\n", body=crlf_body))
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets"), nl="\r\n", body=crlf_body)})
        self.reject("ONTOLOGY_LINE_ENDING", {NOTE: fm(*base_lines("entity_id: acme-widgets"), body=crlf_body)})

    def test_mixed_line_endings_are_rejected(self) -> None:
        crlf = fm(*base_lines(), nl="\r\n")
        self.put(NOTE, crlf)
        mixed = crlf.replace(b"tags: [vendor]\r\n", b"tags: [vendor]\nentity_id: acme-widgets\r\n")
        self.reject("ONTOLOGY_LINE_ENDING", {NOTE: mixed})

    def test_splitter_differential_characters_are_rejected(self) -> None:
        for separator in ("\r", "\x85", " ", " ", "\x0b", "﻿"):
            with self.subTest(separator=repr(separator)):
                smuggled = fm(*base_lines(f"entity_id: acme-widgets{separator}certified_owner: [fact.city]"))
                self.reject("ONTOLOGY_LINE_ENDING", {NOTE: smuggled})

    def test_nel_and_line_separator_are_allowed_in_the_unchanged_body(self) -> None:
        body = "# Acme\n\nline\x85one two\n".encode("utf-8")
        self.put(NOTE, fm(*base_lines(), body=body))
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets"), body=body)})

    def test_body_bytes_must_not_change(self) -> None:
        self.reject("ONTOLOGY_BODY_CHANGED", {NOTE: fm(*base_lines("entity_id: acme-widgets"), body=BODY + b"x")})
        self.reject(
            "ONTOLOGY_BODY_CHANGED",
            {NOTE: fm(*base_lines("entity_id: acme-widgets"), body=BODY.rstrip(b"\n"))},
        )

    def test_note_without_frontmatter_can_gain_one(self) -> None:
        self.put(NOTE, BODY)
        self.accept({NOTE: fm("entity_id: acme-widgets")})
        crlf_body = BODY.replace(b"\n", b"\r\n")
        self.put(NOTE, crlf_body)
        self.reject("ONTOLOGY_LINE_ENDING", {NOTE: fm("entity_id: acme-widgets", body=crlf_body)})
        self.accept({NOTE: fm("entity_id: acme-widgets", body=crlf_body, nl="\r\n")})

    def test_removing_frontmatter_is_rejected(self) -> None:
        self.reject("ONTOLOGY_FRONTMATTER_INVALID", {NOTE: BODY})

    def test_empty_frontmatter_is_supported(self) -> None:
        self.put(NOTE, b"---\n---\n" + BODY)
        self.accept({NOTE: fm("entity_id: acme-widgets")})

    def test_unterminated_or_fake_close_is_rejected_by_strict_splitter(self) -> None:
        for data in (b"---\nentity_id: acme\n---", b"---\nentity_id: acme\n--- \nbody\n", b"---\nentity_id: acme\n"):
            with self.subTest(data=data):
                with self.assertRaises(rules.OntologyRuleError) as caught:
                    rules.parse_note(data, side="new", path=NOTE)
                self.assertEqual(caught.exception.code, "ONTOLOGY_FRONTMATTER_INVALID")
        old = rules.parse_note(b"---\ntitle: x\n---", side="old", path=NOTE)
        self.assertTrue(old.has_frontmatter)
        thematic = rules.parse_note(b"---\nnot frontmatter\n", side="old", path=NOTE)
        self.assertFalse(thematic.has_frontmatter)

    def test_invalid_utf8_frontmatter_is_an_encoding_error(self) -> None:
        with self.assertRaises(rules.OntologyRuleError) as caught:
            rules.parse_note(b"---\ntitle: \xff\n---\n", side="new", path=NOTE)
        self.assertEqual(caught.exception.code, "ONTOLOGY_ENCODING")


class BlockIdentityTests(OntologyTestCase):
    def test_ontology_keys_may_be_interleaved(self) -> None:
        self.accept({NOTE: fm("entity_id: acme-widgets", "title: Acme Widgets", "fact.city: Springfield", "tags: [vendor]")})

    def test_protected_key_changes_are_rejected(self) -> None:
        self.reject("ONTOLOGY_PROTECTED_KEYS_CHANGED", {NOTE: fm("title: Acme Widgets Inc", "tags: [vendor]")})
        self.reject("ONTOLOGY_PROTECTED_KEYS_CHANGED", {NOTE: fm("tags: [vendor]", "title: Acme Widgets")})
        self.reject("ONTOLOGY_PROTECTED_KEYS_CHANGED", {NOTE: fm(*base_lines("# reviewed"))})
        self.reject("ONTOLOGY_PROTECTED_KEYS_CHANGED", {NOTE: fm(*base_lines(""))})
        self.reject("ONTOLOGY_PROTECTED_KEYS_CHANGED", {NOTE: fm("title: Acme Widgets", "tags:  [vendor]")})

    def test_nested_and_multiline_yaml_is_unsupported(self) -> None:
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.city:", "  name: Springfield"))})
        self.put(NOTE, fm("title: Acme", "meta:", "  owner: someone"))
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm("title: Acme", "meta:", "  owner: someone", "entity_id: acme")})

    def test_block_scalars_in_protected_keys_are_preserved(self) -> None:
        self.put(NOTE, fm("summary: |", "  line one", "", "  line two", "title: Acme"))
        self.accept({NOTE: fm("summary: |", "  line one", "", "  line two", "title: Acme", "entity_id: acme")})
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm("summary: |", "  line one", "", "  line two", "title: Acme", "fact_source: |", "  x")})

    def test_canonical_keys(self) -> None:
        self.reject("ONTOLOGY_KEY_NOT_CANONICAL", {NOTE: fm(*base_lines("Entity_ID: acme-widgets"))})
        self.reject("ONTOLOGY_KEY_NOT_CANONICAL", {NOTE: fm(*base_lines("alias: [Acme]"))})
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm(*base_lines("ｅntity_id: acme-widgets"))})
        self.put(NOTE, fm(*base_lines("Entity_ID: acme-widgets", "alias: [Acme]")))
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", "aliases: [Acme]"))})

    def test_duplicate_keys_are_rejected(self) -> None:
        self.reject("ONTOLOGY_DUPLICATE_KEY", {NOTE: fm(*base_lines("entity_id: acme-widgets", "entity_id: other"))})
        self.reject("ONTOLOGY_DUPLICATE_KEY", {NOTE: fm(*base_lines("fact.city: A", "fact.city: B", "entity_id: acme-widgets"))})


class ValueTests(OntologyTestCase):
    def test_entity_rules(self) -> None:
        self.accept({NOTE: fm(*base_lines("about: acme-widgets", "entity_type: vendor", "fact.city: Springfield"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: Acme"))})
        self.reject("ONTOLOGY_ENTITY_AND_ABOUT", {NOTE: fm(*base_lines("entity_id: acme-widgets", "about: acme-widgets"))})
        self.reject("ONTOLOGY_FACT_WITHOUT_ENTITY", {NOTE: fm(*base_lines("fact.city: Springfield"))})

    def test_dates(self) -> None:
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", "review_after: 2027-02-28"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "review_after: 2027-02-30"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "last_verified: soon"))})

    def test_shape_limits(self) -> None:
        facts = [f"fact.f{index}: v" for index in range(rules.MAX_FACTS)]
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", *facts))})
        self.reject("ONTOLOGY_SHAPE_LIMIT", {NOTE: fm(*base_lines("entity_id: acme-widgets", *facts, "fact.extra: v"))})
        large = [f"fact.f{index}: " + "v" * 480 for index in range(36)]
        self.reject("ONTOLOGY_SHAPE_LIMIT", {NOTE: fm(*base_lines("entity_id: acme-widgets", *large))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.city: " + "v" * 501))})

    def test_scalar_grammar(self) -> None:
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", 'fact.note: "uses #hash and a: colon"', "fact.quote: 'it''s fine'"))})
        rejected = {
            "fact.note: has #comment": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: key: value": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: [a, b]": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: &anchor x": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: ~": "ONTOLOGY_INVALID_VALUE",
            'fact.note: "\\u2028"': "ONTOLOGY_INVALID_VALUE",
            'fact.note: "\\q"': "ONTOLOGY_INVALID_VALUE",
            'fact.note: "open': "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: trailing ": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.note: >": "ONTOLOGY_UNSUPPORTED_YAML",
            "fact.bad-field: x": "ONTOLOGY_INVALID_VALUE",
            "fact.Bad_Field: x": "ONTOLOGY_KEY_NOT_CANONICAL",
        }
        for line, code in rejected.items():
            with self.subTest(line=line):
                self.reject(code, {NOTE: fm(*base_lines("entity_id: acme-widgets", line))})

    def test_list_grammar(self) -> None:
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", "aliases:", "  - Acme", "  - 'Acme, Inc'"))})
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", 'aliases: ["Acme, Inc", Acme Co]'))})
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm(*base_lines("entity_id: acme-widgets", "aliases: Acme"))})
        self.reject("ONTOLOGY_UNSUPPORTED_YAML", {NOTE: fm(*base_lines("entity_id: acme-widgets", "aliases: [Acme, [x]]"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "aliases: [" + "a" * 81 + "]"))})

    def test_certified_lists_need_values_and_evidence(self) -> None:
        self.reject("ONTOLOGY_CERTIFIED_WITHOUT_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "certified_evidence: [fact.city]"))})
        self.reject("ONTOLOGY_EVIDENCE_MISSING", {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.city: Springfield", "certified_evidence: [fact.city]"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.city: Springfield", "certified_evidence: [title]"))})
        self.reject("ONTOLOGY_INVALID_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.city: Springfield", "evidence.city: https://example.test"))})


class SecretTests(OntologyTestCase):
    def assert_secret(self, line: str, secret: str) -> None:
        error = self.reject("ONTOLOGY_SECRET_LIKE_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", line))})
        self.assertNotIn(secret, str(error))

    def test_provider_tokens_are_rejected_without_echo(self) -> None:
        for secret in (secret_openai(), secret_aws(), secret_github(), "xo" + "xb-" + "1" * 12, "AI" + "za" + "k" * 33):
            with self.subTest(kind=secret[:3]):
                self.assert_secret(f"fact.token: {secret}", secret)
                self.assert_secret(f"aliases: [{secret}]", secret)

    def test_pem_bearer_userinfo_and_assignments(self) -> None:
        pem = secret_pem()
        self.assert_secret(f'fact_source: "{pem}"', pem)
        bearer = "Bearer " + "abcDEF123" * 3
        self.assert_secret(f"fact.header: {bearer}", bearer)
        url = "https://user:" + "pass" + chr(64) + "example.test/x"
        self.assert_secret(f"fact_source: {url}", url)
        assignment = "pass" + "word=" + "hunter2"
        self.assert_secret(f"fact.login: {assignment}", assignment)

    def test_url_query_credentials_and_entropy(self) -> None:
        url = "https://example.test/feed?to" + "ken=abc123"
        self.assert_secret(f"fact_source: {url}", url)
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", "fact_source: https://example.test/feed?page=2"))})
        blob = "Zx9" * 12
        self.assert_secret(f"fact.blob: {blob}", blob)
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.slug: an-ordinary-lowercase-slug-for-a-product-page"))})

    def test_raw_hash_is_exempt_only_in_fact_source(self) -> None:
        digest = sha(b"invented source")
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", f"fact_source: raw:sha256:{digest}"))})
        self.assert_secret(f"fact.ref: raw:sha256:{digest}", digest)

    def test_secret_ref_is_allowed_only_in_facts(self) -> None:
        reference = "secret-ref:vendor.env#VENDOR_TOKEN"
        self.accept({NOTE: fm(*base_lines("entity_id: acme-widgets", f"fact.vendor_token: {reference}"))})
        self.assert_secret(f"fact_source: {reference}", "VENDOR_TOKEN")
        self.assert_secret(f"aliases: [{reference}]", "VENDOR_TOKEN")
        self.assert_secret("fact.vendor_token: secret-ref:../vendor.env#X", "../vendor")

    def test_existing_secret_title_blocks_ontology_edits(self) -> None:
        self.put(NOTE, fm(f"title: {secret_aws()}"))
        self.reject("ONTOLOGY_SECRET_LIKE_VALUE", {NOTE: fm(f"title: {secret_aws()}", "entity_id: acme-widgets")})

    def test_secret_like_key_names_are_redacted(self) -> None:
        name = "a1" * 20
        error = self.reject("ONTOLOGY_SECRET_LIKE_VALUE", {NOTE: fm(*base_lines("entity_id: acme-widgets", f"fact.{name}: x"))})
        self.assertNotIn(name, str(error))


class CertificationTests(OntologyTestCase):
    DIGEST = "wiki/reviews/owner-digest.md"
    CERTIFIED = base_lines(
        "entity_id: acme-widgets",
        "fact.revenue: 1200",
        "fact.city: Springfield",
        "review_after: 2027-01-01",
        "certified_owner: [fact.revenue]",
    )

    def setUp(self) -> None:
        super().setUp()
        self.put(self.DIGEST, b"# Owner review\n\nApproved the listed facts.\n")

    def certify(self) -> dict:
        return {"certification": {"class": "owner", "digest": self.DIGEST}, "preconditions": self.pin(self.DIGEST)}

    def test_adding_owner_certification_requires_pinned_digest(self) -> None:
        new = fm(*self.CERTIFIED)
        self.reject("ONTOLOGY_OWNER_CERTIFICATION_REQUIRED", {NOTE: new})
        plan = self.accept({NOTE: new}, **self.certify())
        self.assertEqual(plan["ontology"]["approval"], "OWNER APPROVAL REQUIRED")
        self.assertEqual(
            plan["ontology"]["certified_owner"],
            [{"path": NOTE, "key": "fact.revenue", "value": "1200"}],
        )
        self.reject("ONTOLOGY_CERTIFICATION_NOT_PINNED", {NOTE: new}, certification={"class": "owner", "digest": self.DIGEST})

    def test_certification_shape_and_self_certification(self) -> None:
        new = fm(*self.CERTIFIED)
        pins = self.pin(self.DIGEST)
        for certification in (
            {"class": "agent", "digest": self.DIGEST},
            {"class": "owner", "digest": self.DIGEST, "note": "self"},
            {"class": "owner"},
            "owner",
        ):
            with self.subTest(certification=certification):
                self.reject("ONTOLOGY_CERTIFICATION_INVALID", {NOTE: new}, certification=certification, preconditions=pins)
        self.reject(
            "ONTOLOGY_CERTIFICATION_INVALID",
            {NOTE: new},
            certification={"class": "owner", "digest": NOTE},
            preconditions=pins,
        )
        self.reject(
            "ONTOLOGY_CERTIFICATION_INVALID",
            {NOTE: new},
            certification={"class": "owner", "digest": "wiki/reviews/missing.md"},
            preconditions=pins,
        )

    def test_unchanged_certified_keys_cannot_be_bypassed(self) -> None:
        self.put(NOTE, fm(*self.CERTIFIED))
        changed_value = [line.replace("1200", "9900") for line in self.CERTIFIED]
        changed_entity = [line.replace("entity_id: acme-widgets", "entity_id: other-co") for line in self.CERTIFIED]
        extended = [line.replace("2027-01-01", "2030-01-01") for line in self.CERTIFIED]
        removed = [line for line in self.CERTIFIED if not line.startswith("review_after")]
        dropped = [line for line in self.CERTIFIED if not line.startswith("certified_owner")]
        for lines in (changed_value, changed_entity, extended, removed, dropped):
            with self.subTest(lines=lines):
                self.reject("ONTOLOGY_OWNER_CERTIFICATION_REQUIRED", {NOTE: fm(*lines)})
                self.accept({NOTE: fm(*lines)}, **self.certify())

    def test_uncertified_edits_next_to_certified_keys_need_no_certification(self) -> None:
        self.put(NOTE, fm(*self.CERTIFIED))
        self.accept({NOTE: fm(*[line.replace("Springfield", "Shelbyville") for line in self.CERTIFIED])})
        self.accept({NOTE: fm(*[line.replace("2027-01-01", "2026-12-01") for line in self.CERTIFIED])})
        requoted = [line.replace("fact.revenue: 1200", 'fact.revenue: "1200"') for line in self.CERTIFIED]
        self.accept({NOTE: fm(*requoted)})

    def test_unparseable_old_certification_fails_closed(self) -> None:
        self.put(NOTE, fm(*base_lines("entity_id: acme-widgets", "fact.revenue: 1200", "certified_owner: fact.revenue")))
        self.reject(
            "ONTOLOGY_OWNER_CERTIFICATION_REQUIRED",
            {NOTE: fm(*base_lines("entity_id: acme-widgets", "fact.revenue: 1200", "certified_owner: [fact.revenue]"))},
        )


class EvidenceTests(OntologyTestCase):
    def evidence_note(self, ref: str, *extra: str) -> bytes:
        return fm(*base_lines("entity_id: acme-widgets", "fact.city: Springfield", f"evidence.city: {ref}", "certified_evidence: [fact.city]", *extra))

    def test_raw_evidence_must_be_pinned_by_content_hash(self) -> None:
        self.put(".raw/call-notes.txt", b"invented call notes\n")
        digest = sha(b"invented call notes\n")
        new = self.evidence_note(f"raw:sha256:{digest}")
        plan = self.accept({NOTE: new}, preconditions=self.pin(".raw/call-notes.txt"))
        self.assertEqual(plan["ontology"]["certified_evidence"][0]["key"], "fact.city")
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: new})
        self.put(".raw/other.txt", b"other\n")
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: new}, preconditions=self.pin(".raw/other.txt"))
        self.put("wiki/copy.txt", b"invented call notes\n")
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: new}, preconditions=self.pin("wiki/copy.txt"))

    def test_bank_evidence_verifies_the_package(self) -> None:
        evidence_id = rules_verify_id()
        package = f"inbox/research/{evidence_id}"
        shutil.copytree(EVIDENCE_FIXTURE, self.vault / package)
        members = [f"{package}/{name}" for name in rules.EVIDENCE_PACKAGE_MEMBERS]
        new = self.evidence_note(f"bank:{EVIDENCE_WORKSPACE}/{evidence_id}")
        self.accept({NOTE: new}, preconditions=self.pin(*members))
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: new}, preconditions=self.pin(*members[:2]))
        wrong = self.evidence_note(f"bank:other-client/{evidence_id}")
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: wrong}, preconditions=self.pin(*members))

    def test_note_evidence_resolves_one_pinned_note_with_the_heading(self) -> None:
        source = "wiki/sources/vendor-call.md"
        self.put(source, b"---\ntitle: Call\n---\n# Vendor call\n\n## Pricing\n\nInvented.\n\n```\n## Hidden\n```\n")
        self.accept({NOTE: self.evidence_note("note:[[vendor-call]]#Pricing")}, preconditions=self.pin(source))
        self.accept({NOTE: self.evidence_note("note:[[wiki/sources/vendor-call]]#Pricing")}, preconditions=self.pin(source))
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: self.evidence_note("note:[[vendor-call]]#Pricing")})
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: self.evidence_note("note:[[vendor-call]]#Hidden")}, preconditions=self.pin(source))
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: self.evidence_note("note:[[vendor-call]]#Missing")}, preconditions=self.pin(source))

    def test_note_evidence_rejects_ambiguity_self_citation_and_out_of_scope(self) -> None:
        self.put("wiki/a/dup.md", b"## Pricing\n")
        self.put("wiki/b/dup.md", b"## Pricing\n")
        self.reject(
            "ONTOLOGY_EVIDENCE_UNVERIFIED",
            {NOTE: self.evidence_note("note:[[dup]]#Pricing")},
            preconditions=self.pin("wiki/a/dup.md", "wiki/b/dup.md"),
        )
        self.put(NOTE, BASE + b"## Pricing\n")
        self_cited = fm(
            *base_lines(
                "entity_id: acme-widgets",
                "fact.city: Springfield",
                "evidence.city: note:[[acme-widgets]]#Pricing",
                "certified_evidence: [fact.city]",
            ),
            body=BODY + b"## Pricing\n",
        )
        self.reject(
            "ONTOLOGY_EVIDENCE_UNVERIFIED",
            {NOTE: self_cited},
        )
        self.put(NOTE, BASE)
        self.put("inbox/outside.md", b"## Pricing\n")
        self.reject(
            "ONTOLOGY_EVIDENCE_UNVERIFIED",
            {NOTE: self.evidence_note("note:[[inbox/outside]]#Pricing")},
            preconditions=self.pin("inbox/outside.md"),
        )

    def test_unchanged_evidence_certified_keys_are_reverified(self) -> None:
        self.put(".raw/call-notes.txt", b"invented call notes\n")
        digest = sha(b"invented call notes\n")
        self.put(NOTE, self.evidence_note(f"raw:sha256:{digest}"))
        edited = self.evidence_note(f"raw:sha256:{digest}", "entity_type: vendor")
        self.reject("ONTOLOGY_EVIDENCE_UNVERIFIED", {NOTE: edited})
        self.accept({NOTE: edited}, preconditions=self.pin(".raw/call-notes.txt"))


def rules_verify_id() -> str:
    from claude_empire.research_evidence import verify_package

    return verify_package(EVIDENCE_FIXTURE, workspace=EVIDENCE_WORKSPACE)["evidence_id"]


@unittest.skipUnless(os.name == "nt", "native Windows filesystem")
class WindowsOntologyTests(OntologyTestCase):
    def test_crlf_bom_round_trip_through_apply(self) -> None:
        crlf_body = BODY.replace(b"\n", b"\r\n")
        self.put(NOTE, fm(*base_lines(), nl="\r\n", body=crlf_body, bom=True))
        new = fm(*base_lines("entity_id: acme-widgets"), nl="\r\n", body=crlf_body, bom=True)
        apply_bundle(self.vault, self.op({NOTE: new}))
        self.assertEqual(self.read(NOTE), new)

    def test_real_short_name_alias_is_rejected(self) -> None:
        import ctypes

        long_dir = self.vault / "wiki" / "LongDirectoryNameForAliasing"
        long_dir.mkdir()
        (long_dir / "note.md").write_bytes(BASE)
        buffer = ctypes.create_unicode_buffer(1024)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buffer, 1024)
        short_name = Path(buffer.value).name if length else ""
        if not short_name or short_name == long_dir.name:
            self.skipTest("8.3 short names are disabled on this volume")
        alias = f"wiki/{short_name}/note.md"
        with self.assertRaises(TransactionValidationError) as caught:
            inspect_bundle(self.vault, self.op({alias: fm(*base_lines("entity_id: aliased"))}))
        self.assertEqual(caught.exception.code, "PORTABLE_PATH_ALIAS")

    def test_casefold_exclusion_on_case_insensitive_volume(self) -> None:
        self.put(".claude-empire.json", json.dumps({"ontology": {"roots": ["wiki"], "exclude": ["wiki/private"]}}).encode())
        self.put("wiki/private/plan.md", BASE)
        self.reject("ONTOLOGY_TARGET_SCOPE", {"wiki/private/plan.md": fm(*base_lines("entity_id: plan"))})


if __name__ == "__main__":
    unittest.main()
