# Windows and WSL guide

Empire 0.3.1 supports native Windows transactions on local NTFS through a
Windows filesystem backend. Linux, macOS and optional WSL use the existing
POSIX backend. Both use the same reviewed transactions, ownership rules,
private journal and recovery workflow.

## Platform support

| Capability | WSL / Linux / macOS | Native Windows (incl. Git Bash) |
|---|---|---|
| Inspection, dry-run previews, retrieval | Yes | Yes |
| Provider evidence `research-evidence verify/plan` | Yes, read-only | Yes, read-only |
| Python vault writes (`transaction apply`, `init`, `adopt`, `migrate`, `capture apply`, `mode set`, knowledge attachment, Home workflows) | Yes, on a filesystem that preserves POSIX permissions | Yes, local NTFS under the native constraints below |
| Python capture queue commands | Yes | Yes, same native constraints |
| Git checkpoints (`checkpoint`) | Yes | Yes, with Git installed and guarded working directory |
| Bash setup scripts and shell test suites | Yes | No (POSIX-only) |
| Claude Code hooks (`SessionStart`, `Stop`) | Yes (works out of the box) | Partial: requires `python3` on `PATH`; see [below](#claude-code-hooks-and-python3-on-windows) |

## Native Windows scope and recovery

- Use a local fixed-drive NTFS vault. UNC paths, network drives, FAT/exFAT/ReFS
  and reparse-point paths are refused by the native backend. This includes
  junctions, symlinks and cloud placeholder paths; keep an ordinary local vault
  outside a managed sync tree.
- Existing files must have owner and primary group matching the invoking
  Windows token, so their security descriptors can be restored without
  ownership privileges. Unsupported ownership is refused during preparation,
  before any transaction content is changed.
- Read-only files, encrypted EFS files and files with named NTFS alternate streams are refused
  before replacement, because copying only their main bytes would lose protected
  or auxiliary content. This backend preserves file bytes and owner/group/DACL;
  it does not claim preservation of SACL auditing or every NTFS metadata feature.
- Root and child paths use retained handles and relative NT opens. Reparse
  objects are refused. Renaming a directory does not redirect an already
  retained handle into a replacement directory.
- Root identity has a process-lifetime named mutex. Runtime metadata and new
  files receive protected owner-and-SYSTEM ACLs at creation. Existing files'
  owner/group/DACL are bound into the reviewed plan and preserved on replacement
  and rollback. Permission changes cause a conflict.
- File bytes are flushed and each replacement is atomic. The journal supports
  recovery after process interruption, including a killed writer. Ordinary-user
  Windows does not expose POSIX directory `fsync`; this backend does **not**
  claim the same durability for directory entries after sudden power loss.
  Maintain backups and verify the vault after an OS/storage failure.
- Review, apply, replay and recover an unfinished operation in the same backend.
  Complete foreign history with a valid correlated result and intact backups
  remains readable and does not block new work; its records stay unchanged and
  foreign permission descriptors are never applied. Missing-result, unfinished
  or replayed foreign operations require their original backend. WSL remains
  available for existing POSIX vault operations.

Installing the module does not mutate a vault. Native invocation uses the same
Python command as other systems; no WSL helper or alternate direct-copy
installer is involved. Existing shell helpers and their legacy lock protocol
remain POSIX-only, and native support does not make Bash scripts Windows
programs. Do not run native and WSL writers concurrently against one vault.

After a crashed process, inspect the operation journal. A confirmed-dead lock
owner can be reaped using the existing explicit stale-lock recovery option;
age alone never authorizes reaping a live owner. Recovery validates all backup
hashes and permission descriptors before restoring files.

POSIX vaults require persistent POSIX file permissions. A Windows drive mounted
inside WSL needs DrvFS metadata enabled, as described below.

## Windows drives mounted inside WSL

WSL alone is not enough for a vault at `/mnt/c`, `/mnt/e`, or another mounted
Windows drive. DrvFS must retain the exact POSIX permissions used by transaction
recovery. Without its `metadata` mount option, `chmod` can return successfully
while files still report permissions such as `0777`. Microsoft's
[WSL file-permission guide](https://learn.microsoft.com/en-us/windows/wsl/file-permissions)
explains this behavior and the metadata option.

Before applying or recovering a transaction, the engine creates an empty,
temporary file inside the confined `.vault-meta` runtime directory. It requests
mode `0600`, reopens the file to verify the mode persisted, and removes the
probe. A mismatch fails with `UNSUPPORTED_FILESYSTEM_PERMISSIONS` before any
transaction journal, backup, or vault note is written. The lock and empty
runtime directories may already have been created. A probe or cleanup I/O
failure instead reports `FILESYSTEM_PREFLIGHT_FAILED`.

Use the WSL Linux filesystem, or have the operator enable `metadata` for the
chosen DrvFS mount and verify its persistence after WSL restarts. See Microsoft's
[WSL automount configuration](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#automount-options).
The CLI does not remount drives, change host configuration, or move the vault.
An ad hoc metadata-enabled mount may need to be recreated after reboot. Review
and apply using the same vault path and environment after mount setup.

This probe establishes permission support on the runtime filesystem. It does
not certify nested mounts or prevent later filesystem changes; exact per-file
hash and mode checks still apply. Native Windows inspection and dry-run previews
remain supported without either WSL or metadata.

## Claude Code hooks and python3 on Windows

`hooks/hooks.json` spawns each hook using the [Claude Code exec-form command
hook](https://code.claude.com/docs/en/hooks): `"command": "python3"` with an
`args` array. Claude Code resolves `python3` as an executable on `PATH` and
spawns it directly; there is no shell, so no `.bat` shim, alias function, or
shell profile is consulted.

WSL and most Linux and macOS Python installs provide a `python3` on `PATH` by
default, so hooks work there without extra setup. Native Windows commonly does
not:

- python.org installer: installs `python.exe`, not `python3.exe`. Either add
  a `python3.exe` shim earlier on `PATH` than the interpreter, install a
  distribution that provides `python3.exe`, or run Claude Code from WSL so
  hooks resolve the WSL `python3`.
- Microsoft Store Python: the `python3` app execution alias can be a stub
  that opens the Store instead of running Python. Disable the `python3` app
  execution alias in Windows Settings, then install Python from python.org
  or WSL and confirm the real interpreter is on `PATH`.

When `python3` cannot be resolved, Claude Code fails to spawn the hook
process, so claude-empire's own code never runs and cannot emit a
diagnostic. SessionStart context and Stop recovery warnings are both silently
absent in that case; the rest of claude-empire (skills and the CLI) is
unaffected, since only the optional hook path depends on `python3`.

## Optional WSL backend

WSL remains useful for Bash helpers and existing POSIX journals. Its directory
descriptors provide confinement while the native backend uses NT directory
handles. Choose one backend for an operation and retain that environment for
recovery. The native backend does not loosen the POSIX engine's permission
requirements on DrvFS.

## WSL troubleshooting

WSL being "installed" does not always mean WSL is working. Symptoms and checks,
roughly in the order worth trying:

| Symptom | Check |
|---|---|
| `wsl --install` completed but `wsl --status` or `wsl -l -v` hangs indefinitely | This field-reported hang has no confirmed cause. Follow Microsoft's WSL hang diagnosis and reporting flow below. |
| `wsl` reports a kernel or version error | Run `wsl --update`, then `wsl --shutdown`, then retry. |
| WSL worked before and stopped after an update or software change | Do not assume a cause. Update Windows and WSL, then follow Microsoft's WSL troubleshooting flow. |
| Approval hash from a native dry-run fails inside WSL with `PLAN_CHANGED` | By design: the approval hash binds the reviewing environment's filesystem identity. Run the dry-run review inside WSL when the apply will happen there; a natively produced `approved_plan_sha256` cannot be replayed from WSL. |
| Writes fail with `UNSAFE_VAULT_IDENTITY` mentioning stable file identity | The vault sits on FAT/exFAT or an unsupported network share. Move it to NTFS, or keep it inside the WSL filesystem. |

WSL troubleshooting checklist:

1. Start with Microsoft's official
   [WSL troubleshooting guide](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting).
2. Confirm virtualization is enabled in BIOS/UEFI and that both "Virtual
   Machine Platform" and "Windows Subsystem for Linux" are enabled. Reboot
   after enabling either feature.
3. Run `wsl --update` from an elevated prompt, then `wsl --shutdown`, and retry
   `wsl --status`.
4. Confirm the hypervisor launch setting is enabled. If a third-party
   hypervisor is installed, use a current version that supports Hyper-V or
   temporarily turn it off while diagnosing the conflict.
5. If WSL still hangs, follow Microsoft's
   [WSL hang data-collection steps](https://learn.microsoft.com/en-us/windows/wsl/troubleshooting-guide#wsl-hangs)
   and file the resulting report with the WSL project. Do not attribute the
   hang to a specific cause without supporting diagnostics.

## Working across the boundary

The supported native workflow is: inspect, review and apply with native Python
against a supported local vault. If choosing WSL instead, perform all three
steps there. Approval hashes bind to the environment that produced them.
Keeping the vault inside the WSL filesystem (rather than on a mounted Windows
drive) avoids both the identity caveats above and cross-boundary performance
overhead.
