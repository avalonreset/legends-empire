#!/usr/bin/env python3
"""Hermetic tests for canonical product and capability contracts."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_empire.contracts import (
    CORE_TRANSACTION_TYPES,
    evaluate_capabilities,
    validate_contracts,
)
from claude_empire.transaction import OPERATION_TYPES


FIXTURE = ROOT / "tests" / "fixtures" / "contracts" / "valid"
CONTRACTS_CLI = ROOT / "claude_empire" / "contracts.py"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class FixtureRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name) / "repo"
        shutil.copytree(FIXTURE, self.root)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    @property
    def capabilities_path(self) -> Path:
        return self.root / "config" / "capabilities.json"

    def capability(self) -> dict:
        return _read(self.capabilities_path)["capabilities"][0]

    def update_capability(self, **changes: object) -> None:
        document = _read(self.capabilities_path)
        document["capabilities"][0].update(changes)
        _write(self.capabilities_path, document)

    def configuration_root(self) -> Path:
        return self.root / ".vault-meta" / "example"


class CanonicalContractTests(unittest.TestCase):
    def test_capability_and_transaction_operation_types_cannot_drift(self) -> None:
        self.assertEqual(OPERATION_TYPES, CORE_TRANSACTION_TYPES)

    def test_canonical_contracts_validate(self) -> None:
        self.assertEqual([], validate_contracts(ROOT))

    def test_manifest_covers_every_current_skill(self) -> None:
        document = _read(ROOT / "config" / "capabilities.json")
        contracted = {item["id"] for item in document["capabilities"]}
        discovered = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
        self.assertEqual(discovered, contracted)
        self.assertEqual({"cto-legends"}, contracted)

    def test_product_contract_locks_privacy_compatibility_and_release_authority(
        self,
    ) -> None:
        document = _read(ROOT / "config" / "product-contract.json")
        self.assertTrue(document["privacy_defaults"]["local_first"])
        self.assertEqual(
            "explicit_consent", document["privacy_defaults"]["remote_egress"]
        )
        self.assertEqual(
            "explicit_environment_opt_in",
            document["privacy_defaults"]["session_context_injection"],
        )
        self.assertEqual(
            "aggregate_status_only",
            document["privacy_defaults"]["automatic_recovery_warning"],
        )
        self.assertEqual("disabled", document["privacy_defaults"]["transcript_capture"])
        self.assertTrue(document["compatibility"]["filesystem_fallback"])
        gates = {gate["id"] for gate in document["release_gates"]}
        self.assertIn("owner-promotion-approval", gates)
        self.assertIn("artifact-safety", gates)
        self.assertIn("fresh-context-review", gates)
        self.assertIn("core-workflow-forward-tests", gates)

    def test_evaluation_is_deterministic_and_has_no_absolute_roots(self) -> None:
        first = evaluate_capabilities(ROOT)
        second = evaluate_capabilities(ROOT)
        self.assertEqual(first, second)
        self.assertTrue(first["valid"])
        self.assertEqual(0, first["summary"]["degraded"])
        self.assertNotIn(str(ROOT), json.dumps(first, sort_keys=True))

    def test_canonical_core_capabilities_report_only_behavioral_verification(
        self,
    ) -> None:
        report = evaluate_capabilities(ROOT, verify=True)
        core = {
            item["id"]: item
            for item in report["capabilities"]
            if item["tier"] == "core"
        }
        self.assertEqual(
            {"cto-legends": "configured"},
            {capability_id: item["state"] for capability_id, item in core.items()},
        )
        router = core["cto-legends"]
        self.assertTrue(
            any("no automated" in reason for reason in router["reasons"]),
            router,
        )
        self.assertEqual(0, report["summary"]["degraded"])

    def test_canonical_verifiers_are_behavioral_or_explain_their_absence(self) -> None:
        document = _read(ROOT / "config" / "capabilities.json")
        behavioral_targets = {
            "tests/test_detect_transport.py",
            "tests/test_lint_engine.py",
            "tests/test_retrieve.py",
            "tests/test_vault_ops.py",
            "tests/test_wiki_mode.py",
        }
        for capability in document["capabilities"]:
            command = capability["verification_command"]
            if command:
                self.assertIn(command[1], behavioral_targets, capability["id"])
                self.assertNotIn("--check-only", command)
                self.assertNotIn("claude_empire/contracts.py", command)
            else:
                self.assertTrue(
                    capability.get("verification_reason", "").strip(), capability["id"]
                )

    def test_router_capability_scope_is_read_only(self) -> None:
        document = _read(ROOT / "config" / "capabilities.json")
        capabilities = {item["id"]: item for item in document["capabilities"]}
        router = capabilities["cto-legends"]
        self.assertIn(
            "skills/cto-legends/SKILL.md", router["implementation_paths"]
        )
        self.assertEqual([], router["write_scope"])
        self.assertEqual("none", router["transaction_type"])
        self.assertEqual("not_applicable", router["confirmation"]["mutation"])
        self.assertEqual("not_applicable", router["confirmation"]["network_egress"])
        self.assertEqual("forbidden", router["confirmation"]["destructive"])
        self.assertFalse(router["needs"]["shell"])
        self.assertFalse(router["needs"]["network"])
        self.assertNotIn("vault:.raw/**", router["read_scope"])
        self.assertNotIn("vault:.vault-meta/**", router["read_scope"])

    def test_cli_check_only_and_unknown_capability(self) -> None:
        valid = subprocess.run(
            [
                sys.executable,
                str(CONTRACTS_CLI),
                "--repo-root",
                str(ROOT),
                "--check-only",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, valid.returncode, valid.stderr)
        self.assertTrue(json.loads(valid.stdout)["valid"])
        missing = subprocess.run(
            [
                sys.executable,
                str(CONTRACTS_CLI),
                "--repo-root",
                str(ROOT),
                "--capability",
                "does-not-exist",
                "--check-only",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(2, missing.returncode)
        self.assertEqual(
            "unknown_capability", json.loads(missing.stdout)["errors"][0]["code"]
        )


class ValidationTests(FixtureRepo):
    def test_valid_fixture(self) -> None:
        self.assertEqual([], validate_contracts(self.root))

    def test_missing_implementation_path_is_rejected(self) -> None:
        self.update_capability(implementation_paths=["skills/example/MISSING.md"])
        errors = validate_contracts(self.root)
        codes = {item["code"] for item in errors}
        self.assertIn("missing_implementation_path", codes)
        self.assertIn("missing_skill_implementation", codes)

    def test_unregistered_skill_is_rejected(self) -> None:
        extra = self.root / "skills" / "extra"
        extra.mkdir()
        (extra / "SKILL.md").write_text(
            "---\nname: extra\ndescription: extra\n---\n", encoding="utf-8"
        )
        errors = validate_contracts(self.root)
        self.assertIn("unregistered_skill", {item["code"] for item in errors})

    def test_inconsistent_mutation_and_egress_declarations_are_rejected(self) -> None:
        capability = self.capability()
        capability["write_scope"] = [
            {"pattern": "plugin:config/generated.json", "access": "transactional"},
            {"pattern": "vault:.raw/source.md", "access": "transactional"},
        ]
        capability["transaction_type"] = "none"
        capability["confirmation"]["mutation"] = "not_applicable"
        capability["confirmation"]["network_egress"] = "explicit"
        document = _read(self.capabilities_path)
        document["capabilities"][0] = capability
        _write(self.capabilities_path, document)
        codes = {item["code"] for item in validate_contracts(self.root)}
        self.assertTrue(
            {
                "product_mutation",
                "source_mutation",
                "inconsistent_transaction",
                "inconsistent_confirmation",
            }.issubset(codes),
            codes,
        )

    def test_absolute_tracked_paths_are_rejected(self) -> None:
        self.update_capability(
            implementation_paths=["/tmp/example/SKILL.md"],
            verification_command=["/usr/bin/python3", "-V"],
        )
        errors = validate_contracts(self.root)
        self.assertGreaterEqual(
            sum(item["code"] == "absolute_path" for item in errors), 2
        )
        self.assertNotIn(str(self.root), json.dumps(errors, sort_keys=True))

    def test_empty_verifier_requires_an_explicit_reason(self) -> None:
        self.update_capability(verification_command=[], verification_reason="")
        errors = validate_contracts(self.root)
        self.assertIn("missing_verification_reason", {item["code"] for item in errors})

    def test_self_and_schema_only_verifiers_are_rejected(self) -> None:
        self.update_capability(
            verification_command=[
                "{python}",
                "claude_empire/contracts.py",
                "--check-only",
            ]
        )
        errors = validate_contracts(self.root)
        self.assertIn("self_verification", {item["code"] for item in errors})

        self.update_capability(
            verification_command=[
                "{python}",
                "scripts/claude-empire.py",
                "contracts",
                "--verify",
            ]
        )
        errors = validate_contracts(self.root)
        self.assertIn("self_verification", {item["code"] for item in errors})

        self.update_capability(
            verification_command=["{python}", "tests/test_contracts.py"]
        )
        errors = validate_contracts(self.root)
        self.assertIn("schema_only_verification", {item["code"] for item in errors})

        self.update_capability(
            verification_command=[
                "{python}",
                "scripts/claude-empire.py",
                "package",
                "validate",
            ]
        )
        errors = validate_contracts(self.root)
        self.assertIn("schema_only_verification", {item["code"] for item in errors})

    def test_malformed_json_returns_structured_errors(self) -> None:
        self.capabilities_path.write_text("{ definitely not JSON", encoding="utf-8")
        errors = validate_contracts(self.root)
        self.assertEqual("invalid_json", errors[0]["code"])
        self.assertEqual("capabilities_contract", errors[0]["location"])

    def test_duplicate_contract_keys_return_structured_errors(self) -> None:
        self.capabilities_path.write_text(
            '{"schema_version":1,"schema_version":1,"capabilities":[]}',
            encoding="utf-8",
        )
        errors = validate_contracts(self.root)
        self.assertEqual("invalid_json", errors[0]["code"])
        self.assertIn("duplicate JSON object key", errors[0]["message"])

        for number in ("NaN", "1e999"):
            self.capabilities_path.write_text(
                f'{{"schema_version":{number},"capabilities":[]}}', encoding="utf-8"
            )
            errors = validate_contracts(self.root)
            self.assertEqual("invalid_json", errors[0]["code"])
            self.assertIn("non-finite JSON number", errors[0]["message"])


class ReadinessStateTests(FixtureRepo):
    def _state(self, *, verify: bool = False) -> tuple[str, list[str]]:
        report = evaluate_capabilities(self.root, verify=verify)
        item = report["capabilities"][0]
        return item["state"], item["reasons"]

    def test_missing_optional_configuration_is_available(self) -> None:
        state, reasons = self._state()
        self.assertEqual("available", state)
        self.assertEqual(2, len(reasons))

    def test_partial_configuration_is_degraded(self) -> None:
        config = self.configuration_root()
        config.mkdir(parents=True)
        (config / "ready.flag").write_text("ready\n", encoding="utf-8")
        state, reasons = self._state()
        self.assertEqual("degraded", state)
        self.assertTrue(any("partial configuration" in reason for reason in reasons))

    def test_complete_configuration_is_configured(self) -> None:
        config = self.configuration_root()
        (config / "cache").mkdir(parents=True)
        (config / "ready.flag").write_text("ready\n", encoding="utf-8")
        self.assertEqual(("configured", []), self._state())

    def test_successful_execution_is_verified(self) -> None:
        config = self.configuration_root()
        (config / "cache").mkdir(parents=True)
        (config / "ready.flag").write_text("ready\n", encoding="utf-8")
        self.assertEqual(("verified", []), self._state(verify=True))

    def test_failed_execution_is_degraded(self) -> None:
        config = self.configuration_root()
        (config / "cache").mkdir(parents=True)
        (config / "ready.flag").write_text("ready\n", encoding="utf-8")
        self.update_capability(
            verification_command=["{python}", "-c", "raise SystemExit(7)"]
        )
        state, reasons = self._state(verify=True)
        self.assertEqual("degraded", state)
        self.assertEqual(["verification failed with exit code 7"], reasons)

    def test_missing_automated_verifier_stays_configured_with_reason(self) -> None:
        config = self.configuration_root()
        (config / "cache").mkdir(parents=True)
        (config / "ready.flag").write_text("ready\n", encoding="utf-8")
        reason = "no automated end-to-end behavioral verifier is implemented"
        self.update_capability(
            verification_command=[],
            verification_reason=reason,
        )
        self.assertEqual(("configured", [reason]), self._state())
        self.assertEqual(("configured", [reason]), self._state(verify=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
