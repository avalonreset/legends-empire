# Optional AI Marketing Hub Home adapter

`legends-empire` works without Home. Home is a separately obtained, authorized
workspace, not a bundled dependency. Empire is the session starting point.
The shared-root profile uses Home-compatible business/project records even before
Home is installed. A separately obtained Home capability layer can then occupy
its native locations without creating a second ontology or task system.

Resolve the module through the single `cto-legends handoff legends-empire`
router. The commands below use the installed module's `scripts/claude-empire.py`.
They do not acquire Home, authenticate GitHub, install packages, or grant a license.

## Layout and ownership

For a new shared-root workspace, use the reviewed preparation and installation
flow below. Do not overlay a whole Home ZIP manually: `Projects/` and `projects/`
collide on Windows, and both systems have root instructions and settings.

The previously supported separate-root binding remains available: preserve an
existing native Home root inside `/srv/Empire/HomeWorkspace` or at an explicitly
selected external location. Home-ancestor bindings refuse. Same-root binding is
allowed only with `--shared-root` and verified managed instruction composition.

## Prepare one canonical structure, with or without Home

The following commands default to previews. Apply each exact reviewed plan by
repeating it with `--apply --approved-plan-sha256 REVIEWED_HASH`.

```sh
python scripts/claude-empire.py home-adapter prepare --vault /srv/Empire \
  --operation-id prepare-home-layout --generated-at 2026-10-01T00:00:00Z
```

Preparation adds an original public `EMPIRE-ENTRY.md`, a profile record and small
slot markers in `Projects/Active`, `Projects/Completed`,
`Workspaces/Clients/Companies`, `Workspaces/Clients/People`, and
`Workspaces/Brands & Sites`. It preserves an existing `AGENTS.md`; when absent,
it creates an Empire entry pointing to the new document. No Home installation,
business, brand, method, agent role, or marketing readiness is invented.

Create an explicitly requested project with an original record specification:

```json
{
  "schema": "legends.home-records/v1",
  "project_id": "website-improvement",
  "title": "Website improvement",
  "description": "Prepare a reviewable improvement from supplied evidence.",
  "kind": "marketing",
  "company_id": "example-company",
  "brand_id": "example-brand"
}
```

```sh
python scripts/claude-empire.py home-adapter scaffold --vault /srv/Empire \
  --spec /path/to/reviewed-records.json --operation-id create-project \
  --generated-at 2026-10-01T00:00:00Z
```

The project uses `Projects/Active/<id>/<id>.md`, sibling `context.md` and `_tasks/`.
Its Project Manager ID is unique to that root and canonical path. Business
identities are optional, explicitly supplied, never inferred. Company records use
`Workspaces/Clients/Companies/<id>.md`; brand packages use
`Workspaces/Brands & Sites/<id>/`. Existing typed company/brand records are reused
without replacing their facts. Conflicting project files refuse, preserving work.

For whole-life work, set `kind` to `whole-life` and omit company/brand IDs.
All contexts begin `draft`, with unknown facts and method selections empty.
No fabricated company, audience, brain or workflow is created to make a life
project pass a marketing validator. Ordinary Empire work continues without Home.
This is preparatory scaffolding, not a governed Home session or completed task;
follow Home's session/task procedures when activating its installed methods.

## Slot in an authorized private Home capability layer

Supply your existing authorized pristine release source. No GitHub authentication
or private download occurs automatically:

```sh
python scripts/claude-empire.py home-adapter install --vault /srv/Empire \
  --home-source /path/to/authorized-home --operation-id install-home \
  --generated-at 2026-10-01T00:00:00Z
```

Installation validates the pinned release inventory and every selected source
file. Only inventory-owned UTF-8 methods, brains, workflows, native scripts,
indexes and templates in `AI Team/`, `Workspaces/`, `Projects/`, `_system/`,
`_templates/`, `Home/`, and supported `_assets/` are included. Untracked customer
records and modified selected release files are never silently copied.
The exact project-local `.agents/skills/aimh/SKILL.md` and
`.claude/skills/aimh/SKILL.md` entries, QUICKSTART.md, requirements-dev.txt,
qualified text `_examples/` and license/attribution notices are
included. They do not replace the CTO router or activate desktop plugins.

This is a **headless capability layer**, not a full Home desktop installation.
It excludes `.obsidian` plugins/settings, maintainer folders,
non-text assets and packaged tooling. Existing Obsidian settings remain owned by
the user; dashboard rendering, plugin activation and complete package validation
are separate work. Existing nonidentical destination files cause a conflict.
Identical files are reused. No implicit migration or overwrite occurs.
The single mutable starter `AI Team/Sessions/handoff.md` is preserved on reinstall
and checked for its bounded `type: handoff` record shape rather than frozen to its
original bytes. Methods, scripts, guidance and other release entries remain
digest-checked. Newly created customer/session records are outside the release inventory.

The explicit approved plan covers bounded recoverable batches (at most 400 files
each). An `installing` marker precedes writes; only a final verification of every
selected file allows `installed`. Native routing refuses incomplete or changed
managed installs. A failed run can leave completed batches, not a claim of whole
installation atomicity. After a hard interruption, run the existing transaction
recovery command first, then preview and approve the remaining install again.
Identical completed files are reused; source drift and conflicting changes refuse.
Batch journals retain ordinary rollback/recovery evidence. Back up a live vault
before a separately authorized migration; this installer never moves old records.

Compose the two instruction sources explicitly after preparation:

```sh
python scripts/claude-empire.py home-adapter compose --vault /srv/Empire \
  --home-source /path/to/authorized-home --operation-id compose-home \
  --generated-at 2026-10-01T00:00:00Z
```

Composition preserves exact original owner and pinned Home contracts in private
local snapshots below `wiki/integrations/aimh-home/shared-root/`. It replaces only
the reviewed root `AGENTS.md` with an original scope/entry composition. Home's
Secretary procedure does not replace the Empire operator; Home's native role
dispatch remains disabled and external host delegation is labeled honestly.
Unknown conflicts still require explicit resolution. No extra authority is granted.

Shared-root inspection accepts that exact generated contract only when both source
hashes, Home pin and composition provenance verify. Editing the generated contract
or either recorded snapshot refuses; there is no general AGENTS drift exemption.
Retain these local snapshots as private licensed/owner material, not public module
source. Root `CLAUDE.md`/`CODEX.md`, settings and other instructions are not rewritten.

Finally create an enabled binding using the existing preview/apply commands with
`--home /srv/Empire --vault /srv/Empire --shared-root`. Select a real project later.
Detach disables the binding and leaves canonical records and capabilities intact;
the composed contract requires an enabled checked binding before activating Home.

## Reference-only fallback

Only two adapter files are created per binding:

```text
Empire/wiki/integrations/aimh-home/<binding-id>/binding.json
Empire/wiki/integrations/aimh-home/<binding-id>/README.md
```

The binding owns references, not duplicate business facts or tasks. The README is
user-editable; repeat attachment preserves annotations. Reference-only attachment
does not change root instructions, Home files, notes or other bindings. A session started
from Empire can discover bindings using `home-adapter list`. Explicitly incorporate
that discovery step into the host's session instructions if desired; this adapter
does not silently rewrite `AGENTS.md`, `CLAUDE.md`, `CODEX.md`, or startup hooks.
Only the separate explicit `compose` command replaces the reviewed root contract.

For selected Home work, read its operating contract and resolved context. Home's
Secretary entry and disabled native role dispatch are scoped to its procedures;
Empire remains the entry and orchestration layer. Report contradictory instructions
rather than silently choosing wider authority. A binding grants no external action,
membership, tenancy isolation, credential access, or additional agent permissions.

## Inspect, preview, attach

Initialize an Empire vault through the existing `init` workflow first. On Windows,
inspection and previews work natively; writes use WSL/POSIX directory confinement.
On WSL, use a filesystem supporting private file modes (Linux storage, or correctly
configured mounts). Do not weaken transaction protections to force an attachment.
Keep attachment and subsequent checks in the same path namespace; cross-OS or
relocated bindings require explicit rebinding.

```sh
python scripts/claude-empire.py home-adapter inspect --home /srv/Empire/HomeWorkspace
python scripts/claude-empire.py home-adapter plan \
  --vault /srv/Empire --home /srv/Empire/HomeWorkspace --binding-id marketing \
  --operation-id attach-marketing --generated-at 2026-10-01T00:00:00Z
```

Review the returned transaction and retain its `approved_plan_sha256`. Apply with
the same arguments and the exact hash:

```sh
python scripts/claude-empire.py home-adapter apply \
  --vault /srv/Empire --home /srv/Empire/HomeWorkspace --binding-id marketing \
  --operation-id attach-marketing --generated-at 2026-10-01T00:00:00Z \
  --approved-plan-sha256 REVIEWED_HASH
```

These commands create a workspace-only binding; no business or project is invented.
Optionally supply `--project Projects/Active/Example/context.md` to both commands
to bind an existing project. The native capitalized `Projects/` layout is retained.
Apply reconstructs the plan and rechecks Home core fingerprints; source drift or
an altered destination causes refusal. Repeat attachment is a no-op. Retargeting
an existing binding refuses; use a reviewed new binding ID.

## Discover and route

```sh
python scripts/claude-empire.py home-adapter list --vault /srv/Empire
python scripts/claude-empire.py home-adapter check --vault /srv/Empire --binding-id marketing
python scripts/claude-empire.py home-adapter check --vault /srv/Empire --binding-id marketing \
  --project Projects/Active/Example/context.md --native \
  --native-python /absolute/path/to/trusted/home-python
```

The project override selects one project for this check without mutating the
binding. A fresh workspace reports `native_readiness: not_selected` until a project
is selected; attachment alone is never a claim of native project readiness.

`--native` explicitly executes Home's pinned `resolve-context.py` with its original
Home `--vault` and project path. The scripts are digest-checked again and staged in
a temporary isolated folder, avoiding unpinned adjacent modules or cached bytecode.
No Home script content is packaged with Empire. The temporary copy is removed.
The result reports readiness, exit code and a hash, not private resolved facts.

The optional native Python environment needs Home's `PyYAML` dependency. Empire's
standalone runtime does not. `--native-python` names an absolute, trusted interpreter;
without it the current interpreter is used. Missing PyYAML returns
`native_readiness: dependency_missing` with actionable instructions. No dependency
is installed automatically. Invalid native context reports `refused`; investigate
with Home's tools rather than claiming a successful binding made the project ready.

Native project resolution does not prove the full Home package, sessions,
assignments, frozen task criteria, Obsidian rendering, execution authorization, or
marketing quality. Continue Home's checks where the chosen procedure requires them.

## Unplug without deleting knowledge

```sh
python scripts/claude-empire.py home-adapter detach --vault /srv/Empire \
  --binding-id marketing --operation-id unplug-marketing --generated-at 2026-10-01T00:00:00Z
# Review the new hash, then repeat with:
# --apply --approved-plan-sha256 REVIEWED_HASH
```

Detach sets the adapter-owned binding's `enabled` flag to false. It preserves the
route note and every Home/business file. It works even if Home is unavailable.
Discovery labels it disabled; check reports `detached` without invoking Home.
Repeated detach is a no-op. To reconnect, create a fresh reviewed binding ID.

## Compatibility contract

The qualified pin is Home **v0.5.2**, commit
`c18ff16c69e7ba4bcbb556472b0fd8c9bb930c9d`.
`config/home-compatibility.json` contains only path/hash metadata for the operating
contract, integrity inventory and native scripts, with CRLF normalized to LF.
`pinned-core` means those specific bytes match. It is not a claim that an entire
working tree is clean, licensed, latest upstream, or ready for a particular job.
Customer project content can evolve without changing the core pin.

Changed or unknown core versions report `unverified` and cannot be attached or
executed through this adapter. Future pins require reviewed upstream changes and
native fixture regression, not a version-string override. Upstream updates are
never automatic. No Home source, customer information, private repository URLs,
or credentials belong in the public adapter package.
