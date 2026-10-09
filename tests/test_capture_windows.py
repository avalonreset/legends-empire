#!/usr/bin/env python3
"""Native Windows capture acceptance, reusing the adversarial portable cases.

The full capture suite also tests POSIX descriptor injection and symlink races.
Those remain in the POSIX matrix; this suite executes real NTFS transactions,
queue recovery and concurrency without requiring symlink creation privileges.
"""
from __future__ import annotations
import multiprocessing
import os
import unittest
import test_capture as cases


@unittest.skipUnless(os.name == "nt", "native Windows capture backend")
class NativeCaptureTests(unittest.TestCase):
    pass


_CASES = (
    "test_visible_inbox_and_legacy_raw_compatibility",
    "test_filesystem_plan_is_read_only_and_matches_capture_preflight",
    "test_configurable_visible_inbox",
    "test_metadata_sniffing_is_bounded_and_deterministic",
    "test_batch_budgets_are_preflighted_before_copy",
    "test_direct_batch_rolls_back_as_one_transaction",
    "test_queue_lifecycle_is_idempotent",
    "test_queue_failure_resume_and_crash_recovery",
    "test_queue_rejects_malformed_entries_and_mismatched_actions",
    "test_queue_lock_is_process_held",
    "test_queue_and_mutation_locks_share_the_vault_advisory_lock",
    "test_nested_queue_lock_attempt_fails_promptly_without_leaking",
    "test_concurrent_queue_writers_are_lossless_and_serialized",
    "test_explicit_queue_recovery_can_reap_stale_pid_reuse_lock",
    "test_queue_force_stale_lock_reaps_dead_same_host_owner",
    "test_ownerless_queue_lock_requires_explicit_force",
    "test_queue_write_and_read_limits_are_one_durable_contract",
    "test_oversized_queue_primary_recovers_from_bounded_backup",
    "test_queue_entry_count_is_bounded_before_mutation",
    "test_deletion_is_review_only",
    "test_cli_capture_uses_one_recoverable_binary_transaction",
)


def _case(name):
    def run(self):
        getattr(cases, name)()
    run.__name__ = name
    return run


for _name in _CASES:
    setattr(NativeCaptureTests, _name, _case(_name))


if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    unittest.main(verbosity=2)
