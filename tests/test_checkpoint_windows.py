#!/usr/bin/env python3
"""Native Windows Git checkpoint and retained cwd namespace regressions."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import claude_empire.checkpoint as checkpoint
from claude_empire.paths import directory_open_flags
from test_checkpoint import (
    git,
    make_repo,
    test_binary_raw_checkpoint_uses_blob_bytes,
    test_checkpoint_commits_exact_operation,
    test_checkpoint_ignores_external_git_object_store_overrides,
    test_checkpoint_respects_gitignore_and_reports_skipped_metadata,
    test_core_filemode_false_still_rejects_content_mismatch,
    test_existing_checkpoint_is_not_success_after_branch_reset,
    test_intent_to_add_index_state_is_rejected_and_preserved,
    test_nested_vault_cannot_checkpoint_parent_repository,
    test_retry_finalizes_one_commit_after_final_record_failure,
    test_temporary_index_freezes_verified_bytes,
    test_unrelated_staging_and_drift_are_rejected,
    transaction,
)


def test_native_cwd_guards_block_root_and_ancestor_rename() -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    with tempfile.TemporaryDirectory() as temporary:
        parent = Path(temporary) / "ancestor"
        root = parent / "vault"
        root.mkdir(parents=True)
        descriptor = checkpoint.os.open(root, directory_open_flags())
        try:
            with checkpoint._windows_pinned_cwd(descriptor) as pinned:
                assert pinned == root
                for target in (root, parent):
                    # A writable directory handle could turn an empty directory
                    # into a junction. Deny write as well as rename/delete.
                    writer = create(
                        str(target), 0x40000000, 7, None, 3, 0x02200000, None
                    )
                    if writer != ctypes.c_void_p(-1).value:
                        close(writer)
                        raise AssertionError("cwd guard allowed a writable handle")
                    assert ctypes.get_last_error() == 32
                    probe = subprocess.run(
                        [
                            sys.executable, "-c",
                            "import os,sys\n"
                            "try:\n"
                            " os.rename(sys.argv[1],sys.argv[2])\n"
                            "except OSError as exc:\n"
                            " sys.exit(0 if exc.winerror in (5,32) else 2)\n"
                            "sys.exit(3)\n",
                            str(target), str(target.with_name(target.name + "-moved")),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    assert probe.returncode == 0, (probe.returncode, probe.stderr)
            # No leaked deny-delete handles after the context exits.
            moved = root.with_name("released")
            root.rename(moved)
            moved.rename(root)
        finally:
            checkpoint.os.close(descriptor)


def test_native_active_root_requires_retained_cwd_guard() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        descriptor = checkpoint.os.open(root, directory_open_flags())
        token = checkpoint._ACTIVE_ROOT.set((root, descriptor))
        try:
            try:
                checkpoint._descriptor_root_path(root)
            except checkpoint.CheckpointError as exc:
                assert exc.code == "CHECKPOINT_FD_CWD_UNSUPPORTED"
            else:
                raise AssertionError("unguarded native cwd was accepted")
        finally:
            checkpoint._ACTIVE_ROOT.reset(token)
            checkpoint.os.close(descriptor)


def test_native_cwd_failure_preserves_git_head() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = make_repo(Path(temporary) / "vault")
        transaction(root)
        before = git(root, "rev-parse", "HEAD")
        with patch.object(
            checkpoint, "_windows_pinned_cwd",
            side_effect=OSError("injected inaccessible ancestor"),
        ):
            try:
                checkpoint.checkpoint_operation(root, "save-one", run_lint=False)
            except checkpoint.CheckpointError as exc:
                assert exc.code == "CHECKPOINT_FD_CWD_UNSUPPORTED"
            else:
                raise AssertionError("unavailable cwd guard did not fail closed")
        assert git(root, "rev-parse", "HEAD") == before
        assert not (
            root / ".vault-meta/transactions/save-one/checkpoint.pending.json"
        ).exists()
        # A failed guard must release the mutation lock.
        checkpoint.checkpoint_operation(root, "save-one", run_lint=False)


def main() -> None:
    if os.name != "nt":
        print("Native Windows checkpoint tests skipped on this platform.")
        return
    checks = (
        test_native_cwd_guards_block_root_and_ancestor_rename,
        test_native_active_root_requires_retained_cwd_guard,
        test_native_cwd_failure_preserves_git_head,
        test_checkpoint_commits_exact_operation,
        test_checkpoint_respects_gitignore_and_reports_skipped_metadata,
        test_existing_checkpoint_is_not_success_after_branch_reset,
        test_checkpoint_ignores_external_git_object_store_overrides,
        test_unrelated_staging_and_drift_are_rejected,
        test_intent_to_add_index_state_is_rejected_and_preserved,
        test_core_filemode_false_still_rejects_content_mismatch,
        test_temporary_index_freezes_verified_bytes,
        test_retry_finalizes_one_commit_after_final_record_failure,
        test_binary_raw_checkpoint_uses_blob_bytes,
        test_nested_vault_cannot_checkpoint_parent_repository,
    )
    for check in checks:
        check()
    print(f"All {len(checks)} native Windows checkpoint checks passed.")


if __name__ == "__main__":
    main()
