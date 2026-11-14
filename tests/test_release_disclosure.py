"""Release disclosure layers: generic personal-data patterns and private denylist.

All terms here are synthetic. A real private denylist never enters the repository.
"""
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import disclosure, release

FAKE_LIST = b"""# synthetic
zephyr quasar
word:qux
word:vexal
"""


_SAVED_ENV = {}


def setUpModule():
    for key in (disclosure.DENYLIST_ENV, disclosure.REQUIRE_ENV):
        _SAVED_ENV[key] = os.environ.pop(key, None)
    _SAVED_ENV["__default__"] = disclosure.DEFAULT_DENYLIST
    disclosure.DEFAULT_DENYLIST = Path(tempfile.gettempdir()) / "no-such-dir-x9" / "absent-denylist.txt"


def tearDownModule():
    disclosure.DEFAULT_DENYLIST = _SAVED_ENV.pop("__default__")
    for key, value in _SAVED_ENV.items():
        os.environ.pop(key, None)
        if value is not None:
            os.environ[key] = value


def fake_list():
    return disclosure.parse_denylist(FAKE_LIST, source="fake")


def codes(errors):
    return [item["code"] for item in errors]


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.denylist = fake_list()

    def assertHit(self, text, count=1):
        hits = [e for e in disclosure.scan_text("doc.md", text, self.denylist) if e["code"] == "private_term"]
        self.assertEqual(len(hits), count, text.encode("unicode_escape"))

    def test_spacing_hyphen_underscore_and_joined(self):
        for text in ("Zephyr Quasar", "zephyr-quasar", "ZEPHYR_QUASAR", "zephyrquasar", "zephyr   quasar"):
            self.assertHit(text)

    def test_camel_case(self):
        self.assertHit("class ZephyrQuasarClient: pass")

    def test_fullwidth_forms(self):
        self.assertHit("Ｚｅｐｈｙｒ Ｑｕａｓａｒ")

    def test_zero_width_characters(self):
        self.assertHit("zeph​yr qua‍sar")
        self.assertHit("ve​xal")

    def test_cyrillic_and_greek_homoglyphs(self):
        self.assertHit("zеphyr quаsаr")
        self.assertHit("vεxal")

    def test_line_split(self):
        self.assertHit("See zephyr\nquasar for details")
        self.assertHit("zephyr \\\n    quasar")

    def test_word_entries_match_whole_tokens_only(self):
        self.assertHit("the qux host")
        self.assertHit("QuxHost and VexalSync", 2)  # camel-case parts count as tokens
        self.assertHit("quxote and quux", 0)
        self.assertHit("convexal", 0)

    def test_findings_never_echo_terms(self):
        errors = disclosure.scan_text("doc.md", "zephyr quasar and qux", self.denylist)
        blob = json.dumps(errors).casefold()
        for term in ("zephyr", "quasar", "qux"):
            self.assertNotIn(term, blob)
        self.assertTrue(all("sha256:" in e["message"] for e in errors))

    def test_clean_text_passes(self):
        self.assertEqual(disclosure.scan_text("doc.md", "An ordinary release note.", self.denylist), [])


class DenylistParsingTests(unittest.TestCase):
    def test_terms_shorter_than_three_rejected_without_echo(self):
        with self.assertRaises(disclosure.DisclosureError) as ctx:
            disclosure.parse_denylist(b"q-z\n")
        self.assertNotIn("q-z", str(ctx.exception))

    def test_short_compound_rejected(self):
        with self.assertRaises(disclosure.DisclosureError):
            disclosure.parse_denylist(b"compound:abcd\n")

    def test_tier_selection(self):
        tiers = [e.tier for e in disclosure.parse_denylist(
            b"qux\nword:longerword\nmyclnt\nzephyrquasar\ncompound:myclnt\n610\n19990101\n"
        ).entries]
        self.assertEqual(tiers, ["word", "word", "joined", "stream", "stream", "word", "stream"])

    def test_empty_list_rejected(self):
        with self.assertRaises(disclosure.DisclosureError):
            disclosure.parse_denylist(b"# only comments\n\n")

    def test_non_utf8_rejected(self):
        with self.assertRaises(disclosure.DisclosureError):
            disclosure.parse_denylist(b"\xff\xfe\x00bad")


class TierTests(unittest.TestCase):
    def hits(self, entry, text):
        listing = disclosure.parse_denylist(entry.encode())
        return len(disclosure.private_findings("doc.md", text, listing))

    def test_short_terms_match_tokens_not_substrings(self):
        for text in ("Qux", "QUX", "q\u200bux", "qux_tool", "Users/qux/", "qux@host", "quxVault", "\uff51\uff55\uff58"):
            self.assertEqual(self.hits("qux", text), 1, text.encode("unicode_escape"))
        for text in ("quxote", "the quxote bench", "a" * 10):
            self.assertEqual(self.hits("qux", text), 0, text)

    def test_multi_word_word_entry_matches_consecutive_tokens(self):
        self.assertEqual(self.hits("word:rc vex", "an rc-vex host"), 1)
        self.assertEqual(self.hits("word:rc vex", "rc and vex"), 0)

    def test_medium_terms_match_whole_token_runs(self):
        for text in ("MyClnt", "my-clnt", "my\nclnt", "my_clnt", "myclnt"):
            self.assertEqual(self.hits("myclnt", text), 1, text)
        self.assertEqual(self.hits("myclnt", "dummyclntx"), 0)
        self.assertEqual(self.hits("compound:myclnt", "dummyclntx"), 1)

    def test_long_terms_match_glued_substrings(self):
        self.assertEqual(self.hits("zephyrquasar", "xxzephyrquasarxx"), 1)
        self.assertEqual(self.hits("zephyrquasar", "foo_Zephyr\nQuasar_bar"), 1)

    def test_hex_digests_masked_for_short_hex_terms(self):
        digest = "0" * 20 + "19990101" + "0" * 36
        self.assertEqual(self.hits("19990101", "sha256 " + digest), 0)
        self.assertEqual(self.hits("1999-01-01", "born 1999-01-01"), 1)

    def test_decoded_views(self):
        encoded = "".join("\\u%04x" % ord(ch) for ch in "zephyr quasar")
        self.assertEqual(self.hits("zephyr quasar", encoded), 1)
        self.assertEqual(self.hits("zephyr quasar", "%7Aephyr%20quasar"), 1)
        self.assertEqual(self.hits("zephyr quasar", "&#122;ephyr &#x71;uasar"), 1)
        import base64
        blob = base64.b64encode(b"the zephyr quasar notes, version two").decode()
        self.assertEqual(self.hits("zephyr quasar", "data = '" + blob + "'"), 1)

    def test_binary_strings(self):
        listing = disclosure.parse_denylist(FAKE_LIST)
        data = b"\x89PNG\x00\x01zephyr quasar\x00\x02" + "vexal!".encode("utf-16-le")
        self.assertEqual(len(disclosure.scan_binary("a.png", data, listing)), 2)
        self.assertEqual(disclosure.scan_binary("a.png", data, None), [])

    def test_binary_hook_in_release_scan(self):
        listing = disclosure.parse_denylist(FAKE_LIST)
        data = b"\x00\x01binary zephyr quasar\x00"
        self.addCleanup(disclosure.configure, None, require=None)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "list.txt"
            target.write_bytes(FAKE_LIST)
            disclosure.configure(str(target))
            found = release._scan_content(
                "sample.bin", data, approved_private_urls=frozenset(),
                reviewed_binaries={"sample.bin": release._sha256(data)},
            )
        self.assertEqual(codes(found), ["private_term"])
        self.assertTrue(listing.entries)

    def test_member_name_findings_are_hash_labelled(self):
        self.addCleanup(disclosure.configure, None, require=None)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "list.txt"
            target.write_bytes(FAKE_LIST)
            disclosure.configure(str(target))
            found = release._member_name_findings("docs/zephyr-quasar.md")
        self.assertEqual(codes(found), ["private_term"])
        self.assertTrue(found[0]["path"].startswith("archive-member:"))
        self.assertNotIn("zephyr", json.dumps(found).casefold())


class GenericLayerTests(unittest.TestCase):
    def scan(self, text):
        return codes(disclosure.scan_text("doc.md", text, None))

    def test_phone_numbers(self):
        # Fixtures are assembled at runtime so this file passes its own release scan.
        line = "867" + "-5309"
        for text in ("call 312-" + line, "(312) " + line, "+1 312." + line.replace("-", "."), "1-312-" + line):
            self.assertIn("phone_number", self.scan(text), text)

    def test_fictional_phone_range_allowed(self):
        self.assertEqual(self.scan("call 312-555-" + "0142 or (212) 555-" + "0199"), [])

    def test_non_phone_digits_ignored(self):
        for text in ("version 1.2.3", "2026-11-14", "sha 3128675309", "0.312.867." + "5309", "id 312-867-" + "53091"):
            self.assertNotIn("phone_number", self.scan(text), text)

    def test_tailnet_addresses(self):
        self.assertIn("tailnet_address", self.scan("ssh user@100." + "101.102.103"))
        self.assertIn("tailnet_address", self.scan("host 100." + "64.0.1"))
        self.assertNotIn("tailnet_address", self.scan("host 100.63.0.1 and 100.128.0.1"))
        self.assertNotIn("tailnet_address", self.scan("version 100.70.1.2.3"))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(disclosure.configure, None, require=None)
        saved = {k: os.environ.pop(k, None) for k in (disclosure.DENYLIST_ENV, disclosure.REQUIRE_ENV)}

        def restore():
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value

        self.addCleanup(restore)
        self.listfile = Path(self.temp.name) / "list.txt"
        self.listfile.write_bytes(FAKE_LIST)

    def test_missing_list_is_generic_only_by_default(self):
        disclosure.configure(None, require=None)
        loaded, errors = disclosure.resolve()
        self.assertIsNone(loaded)
        self.assertEqual(errors, [])

    def test_missing_list_fails_closed_when_required(self):
        disclosure.configure(None, require=True)
        loaded, errors = disclosure.resolve()
        self.assertIsNone(loaded)
        self.assertEqual(codes(errors), ["denylist_required"])

    def test_require_via_environment(self):
        os.environ[disclosure.REQUIRE_ENV] = "1"
        disclosure.configure(None, require=None)
        self.assertEqual(codes(disclosure.resolve()[1]), ["denylist_required"])

    def test_symlinked_list_refused(self):
        link = Path(self.temp.name) / "link.txt"
        try:
            link.symlink_to(self.listfile)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        disclosure.configure(str(link))
        self.assertEqual(codes(disclosure.resolve()[1]), ["denylist_invalid"])

    def test_default_path_used_when_present(self):
        saved = disclosure.DEFAULT_DENYLIST
        self.addCleanup(setattr, disclosure, "DEFAULT_DENYLIST", saved)
        disclosure.DEFAULT_DENYLIST = self.listfile
        disclosure.configure(None, require=True)
        loaded, errors = disclosure.resolve()
        self.assertEqual((len(loaded.entries), errors), (3, []))

    def test_unreadable_selected_list_fails_closed(self):
        disclosure.configure(str(Path(self.temp.name) / "absent.txt"))
        self.assertEqual(codes(disclosure.resolve()[1]), ["denylist_unreadable"])

    def test_environment_selection_and_scan_content_hook(self):
        os.environ[disclosure.DENYLIST_ENV] = str(self.listfile)
        loaded, errors = disclosure.resolve()
        self.assertEqual((len(loaded.entries), errors), (3, []))
        found = release._scan_content(
            "README.md", b"Built by the zephyr quasar team.\n",
            approved_private_urls=frozenset(), reviewed_binaries={},
        )
        self.assertIn("private_term", codes(found))

    def test_audit_artifact_fails_closed_when_required_list_missing(self):
        disclosure.configure(None, require=True)
        report = release.audit_artifact(Path(self.temp.name) / "missing.zip")
        self.assertFalse(report["ok"])
        self.assertEqual(codes(report["errors"]), ["denylist_required"])


@unittest.skipIf(shutil.which("git") is None, "git is required")
class CommitAndNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(disclosure.configure, None, require=None)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.listfile = Path(self.temp.name) / "list.txt"
        self.listfile.write_bytes(FAKE_LIST)
        self.git("init", "-q")
        self.commit("first", "initial import")
        self.git("tag", "v0.0.1")
        self.commit("second", "feat: add the zephyr-quasar adapter")
        self.commit("third", "docs: tidy")

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="dev@example.com",
                   GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="dev@example.com")
        subprocess.run(["git", "-C", str(self.repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                       check=True, capture_output=True, env=env)

    def commit(self, name, message):
        (self.repo / f"{name}.txt").write_text(name + "\n", encoding="utf-8")
        self.git("add", f"{name}.txt")
        self.git("commit", "-q", "-m", message)

    def test_commit_messages_since_last_tag(self):
        items, errors = disclosure.commit_messages(self.repo, None, release._run_git)
        self.assertEqual(errors, [])
        self.assertEqual(len(items), 2)

    def test_private_term_in_commit_message_reported_without_echo(self):
        disclosure.configure(str(self.listfile), require=True)
        report = release.audit_disclosure(self.repo)
        self.assertFalse(report["ok"])
        self.assertEqual(codes(report["errors"]), ["private_term"])
        self.assertTrue(report["errors"][0]["path"].startswith("commit:"))
        self.assertNotIn("zephyr", json.dumps(report).casefold())

    def test_release_notes_scanned_with_generic_layer(self):
        notes = Path(self.temp.name) / "NOTES.md"
        notes.write_text("Questions: 312-867-" + "5309\n", encoding="utf-8")
        disclosure.configure(None, require=None)
        report = release.audit_disclosure(self.repo, text_files=[notes], include_commits=False)
        self.assertEqual(codes(report["errors"]), ["phone_number"])
        self.assertEqual(report["summary"]["private_layer"], "absent")

    def test_clean_range_passes(self):
        disclosure.configure(str(self.listfile), require=True)
        report = release.audit_disclosure(self.repo, revision_range="HEAD~1..HEAD")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["summary"]["private_entries"], 3)

    def test_no_tag_fails_closed_unless_range_given(self):
        self.git("tag", "-d", "v0.0.1")
        _, errors = disclosure.commit_messages(self.repo, None, release._run_git)
        self.assertEqual(codes(errors), ["no_release_tag"])
        items, errors = disclosure.commit_messages(self.repo, "HEAD", release._run_git)
        self.assertEqual((len(items), errors), (3, []))

    def test_shallow_clone_refused(self):
        clone = Path(self.temp.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", self.repo.as_uri(), str(clone)],
                       check=True, capture_output=True)
        _, errors = disclosure.commit_messages(clone, "HEAD", release._run_git)
        self.assertEqual(codes(errors), ["shallow_history"])

    def test_option_like_range_refused(self):
        _, errors = disclosure.commit_messages(self.repo, "--all", release._run_git)
        self.assertEqual(codes(errors), ["invalid_range"])


class CliTests(unittest.TestCase):
    def test_cli_disclosure_requires_list(self):
        from claude_empire import cli
        self.addCleanup(disclosure.configure, None, require=None)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = cli.main(["release", "disclosure", "--no-commits", "--require-denylist"])
        self.assertEqual(code, 1)
        self.assertEqual(codes(json.loads(buffer.getvalue())["errors"]), ["denylist_required"])


if __name__ == "__main__":
    unittest.main()
