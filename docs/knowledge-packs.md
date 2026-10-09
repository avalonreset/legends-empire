# Modular knowledge shelves

A Legends module can ship a small reusable Markdown knowledge pack. The agent
discovers the module through `cto-legends`, reads its recipe, and can attach its
knowledge to an explicitly selected Empire. Installing a module never selects
or modifies a personal vault automatically. No additional registered skill is
needed.

Stewardship is a capability within `legends-empire`. Its pack is a shelf about
maintaining knowledge and work, not a second personal vault or another product
installation. The shelf makes its methods available offline and visible in
Obsidian, alongside the owner's existing knowledge.

## Three kinds of ownership

| Material | Location | Update behavior |
| --- | --- | --- |
| Released knowledge | Installed module `knowledge/<pack>/vault/` | Updated with the module |
| Attached reference snapshot | Selected Empire shelf `reference/` | Updated only when the last shipped bytes remain unchanged |
| User knowledge | Shelf `index.md` and the owner's selected project, campaign, evidence and session locations | Never overwritten by pack updates |

The first attachment creates a readable `index.md`, a `reference/` snapshot,
and a `pack-state.json` ledger. The index explains the purpose, links to the
reference entrypoint and gives the stable module resolver:
`cto-legends handoff legends-empire`. It is thereafter owned by the user.
The workflow does not create an empty `work/` directory. A person can choose
that destination for private records or keep using existing vault locations.

Module knowledge can also be read directly through router handoff without
attachment. Attachment is useful when the user wants a durable, offline,
searchable shelf. It deliberately copies a versioned reference snapshot; it
does not relocate the module or synchronize private records upstream.

## Attach or update

Use the pack from the installed module. Preview with an explicit vault and
pinned operation identity and timestamp:

```bash
python scripts/claude-empire.py knowledge attach knowledge/stewardship/pack.json \
  --vault /path/to/Empire --operation-id attach-stewardship-001 \
  --generated-at 2026-09-28T12:00:00Z
```

The output includes the complete generic Empire transaction, its changed paths
and `approved_plan_sha256`. Read the proposed content, then repeat the same
command with `--apply --approved-plan-sha256 <reviewed-hash>` when authorized.
Existing authorization for the work need not become another permission question.
The hash binds the reviewed changes technically; it is not an independent
approval policy.

Use `--destination wiki/library/my-chosen-shelf` to select another location.
This version supports destinations below `wiki/library/`; it does not move or
reorganize existing records to make them fit. Index links into an existing
custom ontology are a separate, reviewed user-note change. Attachment does
not claim to integrate every possible vault layout automatically.

Empire 0.3.1 supports native Windows preview and apply on local NTFS through
the same recoverable transaction engine with its native handle/ACL backend.
Linux and macOS use POSIX confinement. WSL remains optional; mounted Windows
drives in WSL need DrvFS metadata enabled. See [platform setup](windows-wsl.md)
for storage and recovery limits. Preview and apply in the same environment;
do not replay a Windows approval hash or unfinished journal through POSIX or vice
versa. Valid complete foreign history remains intact and does not block new work.
There is no alternate direct-write installer.

After updating the module, run the same attachment command with a new operation
ID and the new pack. An unchanged pack returns `noop` without writing files.
The module version and the attached snapshot version can differ until this
explicit update. An older pack can similarly restore earlier references when
all owned reference files are unchanged; this never rolls back user records.

## Preserve edits and history

- Updates check every owned reference against its last shipped hash. A modified
  or missing reference refuses the complete operation, even when that reference
  would otherwise remain unchanged. Inspect and preserve the edit before
  choosing a migration; there is no force-overwrite flag.
- Existing user-editable `index.md` is preserved. An entrypoint rename requires
  explicit index migration instead of leaving its link silently broken.
- Files removed upstream remain on disk, marked `obsolete: true` in the state
  ledger. The attachment workflow has no deletion command.
- New upstream filenames cannot overwrite unowned content. Case aliases,
  escaping paths and symlink/junction redirects are refused.
- The ledger and all reference preconditions are bound to the reviewed
  transaction. Edits between preview and apply cause a conflict; re-read and
  prepare a new plan. Interrupted writes use existing Empire recovery.

The state checksum detects accidental ledger edits, not malicious forgery.
Archive/catalog verification establishes the distribution boundary. Pack hashes
check content identity; they are not signatures or proof that guidance is true.
Read imported Markdown as reference material, subject to the owner's current
instructions. A pack cannot grant credentials, paid calls, publication, or
access to other vaults.

## Pack author contract

`pack.json` has schema `legends.knowledge-pack/v1` and these exact fields:

```json
{
  "schema": "legends.knowledge-pack/v1",
  "id": "stewardship",
  "module": "legends-empire",
  "version": "0.2.0",
  "entrypoint": "index.md",
  "files": [{"path": "index.md", "sha256": "<64 lowercase hex characters>"}],
  "ontology": {
    "kind": "machinery",
    "suggested_home": "wiki/library/legends-empire/stewardship",
    "purpose": "Keep knowledge and work understandable over time."
  }
}
```

File paths resolve beneath `pack.json`'s sibling `vault/` directory. Only
declared UTF-8 Markdown files are copied. The entrypoint must be declared.
The pack allows at most 256 files and 8 MiB in total. Paths must be portable,
relative and non-colliding. IDs use lowercase hyphenated names; versions use
stable `X.Y.Z`. Ontology kind is `machinery` or `library`, and placement remains
an overridable suggestion. The contract contains no executable commands.

Preserve relative links within the declared snapshot. Include methods,
record templates, ownership guidance and verification limits that add value
for the module's outcome; do not manufacture a large wiki merely for symmetry.
Never distribute private instances, personal records, credentials or reports.
Changing manifest bytes requires a new version. Release admission should check
knowledge links and hashes, package inclusion, fresh attachment, update conflict
handling and natural-language discovery through the router.

This first contract does not automatically register every installed module,
modify a root index, merge edited references, interpret arbitrary ontology
schemas, synchronize Plane, or migrate records. Those require explicit future
capabilities and acceptance evidence. The owner retains readable Markdown
even if the router, module or agent host is later removed.
