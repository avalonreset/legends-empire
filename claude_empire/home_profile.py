"""Original Home-compatible record scaffolds and explicit shared-root composition.

The public profile contains no Home methods or templates. Authorized owners
supply their separately obtained Home installation for contract composition.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import uuid

from . import home_adapter as adapter
from .transaction import read_vault_regular, sha256_bytes

BASE = "wiki/integrations/aimh-home/shared-root"
PROFILE = f"{BASE}/profile.json"
COMPOSITION = f"{BASE}/composition.json"
OWNER_CONTRACT = f"{BASE}/empire-agents-original.md"
HOME_CONTRACT = f"{BASE}/home-agents-original.md"
ENTRY = "EMPIRE-ENTRY.md"
SLOTS = ("Projects/Active", "Projects/Completed", "Workspaces/Clients/Companies",
         "Workspaces/Clients/People", "Workspaces/Brands & Sites")
PROFILE_SCHEMA = "legends.home-ready-profile/v1"
COMPOSITION_SCHEMA = "legends.home-composition/v1"


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def slug(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        adapter.fail("record IDs must be lowercase letters/digits/hyphens, starting with a letter")
    return value


def entry_text() -> bytes:
    return ("# Empire session entry\n\n"
            "Every session starts from this Empire root. Read the root AGENTS.md and the relevant "
            "canonical project context; stay at this root when running tools with explicit paths.\n\n"
            "Empire's root Home.md remains the house map. The optional Home dashboard is explicitly "
            "[Home/Home.md](Home/Home.md), or [[Home/Home]]; never resolve it by ambiguous basename. "
            "Qualified _examples are illustrative only, never ready business contexts.\n\n"
            "## Canonical records\n\n"
            "- Project Manager overviews: Projects/Active/<id>/<id>.md; context.md beside each overview.\n"
            "- New project tasks: the same project's _tasks/; no parallel task list.\n"
            "- Companies: Workspaces/Clients/Companies/<id>.md.\n"
            "- People: Workspaces/Clients/People/.\n"
            "- Brand knowledge: Workspaces/Brands & Sites/<id>/, including brand, claims, audiences, "
            "channels, sites and results.\n"
            "- Empire continuity, sources and whole-life knowledge: existing wiki/ and owner-selected areas.\n\n"
            "These paths work without AI Marketing Hub Home. Missing Home means marketing methods and "
            "native Home validation are unavailable, not that ordinary Empire work is blocked. "
            "Use home-adapter list/check to discover optional bindings. Home-ready does not mean "
            "Home-installed or project-ready. Do not invent business facts to satisfy a validator.\n\n"
            "Whole-life projects may use these Project Manager records with draft context. "
            "Read any migration.json sidecar before task work. A migrated legacy project marked "
            "task_inventory_state: preserved-unimported (including in that sidecar) retains its "
            "original work ledger as canonical until an explicit reviewed task import; do not create "
            "a competing _tasks ledger or claim that legacy tasks are absent. "
            "Do not falsely mark non-marketing work as Home-ready or fabricate a company, brand, "
            "brain or workflow. Reuse existing facts through links; preserve legacy aliases during "
            "any separately reviewed migration. Keep credentials outside Markdown.\n").encode()


def bundle(root: Path, contents: dict[str, bytes], operation_type: str,
           operation_id: str, generated_at: str, replace: dict[str, bytes] | None = None) -> dict:
    writes, expected, observed = [], {}, {}
    replace = replace or {}
    for path, payload in contents.items():
        old = read_vault_regular(root, path)
        if old == payload:
            observed[path] = sha256_bytes(old)
        elif old is None:
            writes.append({"path": path, "mode": "create", "content": payload.decode()})
            expected[path] = None
        elif path in replace and old == replace[path]:
            writes.append({"path": path, "mode": "replace", "content": payload.decode()})
            expected[path] = sha256_bytes(old)
        else:
            adapter.fail(f"existing content differs; preserve it and review explicitly: {path}")
    return {"schema": "claude-empire.transaction.v1", "operation_id": operation_id,
            "operation_type": operation_type, "generated_at": generated_at,
            "expected_hashes": expected, "read_preconditions": observed, "writes": writes}


def prepare_bundle(root: Path, operation_id: str, generated_at: str) -> dict:
    profile = {"schema": PROFILE_SCHEMA, "version": 1, "home_required": False,
               "entry": ENTRY, "canonical_slots": list(SLOTS)}
    contents = {PROFILE: encoded(profile), ENTRY: entry_text()}
    for path in SLOTS:
        contents[f"{path}/.empire-slot.json"] = encoded({"schema": PROFILE_SCHEMA, "canonical_slot": path})
    if read_vault_regular(root, "AGENTS.md") is None:
        contents["AGENTS.md"] = b"# Empire operating entry\n\nStart here and read [EMPIRE-ENTRY.md](EMPIRE-ENTRY.md).\n"
    return bundle(root, contents, "home-profile", operation_id, generated_at)


def composed_agents() -> bytes:
    return ("# Empire operating entry with optional AI Marketing Hub Home\n\n"
            "All sessions start at this Empire root. Read [EMPIRE-ENTRY.md](EMPIRE-ENTRY.md).\n\n"
            f"Preserved owner instructions: [{OWNER_CONTRACT}]({OWNER_CONTRACT}).\n\n"
            f"Pinned Home operating contract: [{HOME_CONTRACT}]({HOME_CONTRACT}).\n\n"
            "## Scope and composition\n\n"
            "System/developer instructions and the owner's current explicit direction remain primary. "
            "The owner contract governs Empire-wide work and orchestration. Home's contract governs "
            "its selected marketing methods, native records, evidence and review requirements. "
            "Activate Home procedures only after an enabled binding passes home-adapter check; "
            "without one, continue standalone Empire work. "
            "Home's Secretary is a procedure entry role inside the existing session; it does not "
            "replace Empire's operator or change the session root. Home's disabled role dispatch "
            "means its native role cards do not spawn agents. External host delegation, when "
            "authorized, is recorded as external orchestration and never claimed as Home dispatch. "
            "No wider execution or external-action authority is conferred. Surface remaining "
            "contradictions instead of choosing a more permissive rule.\n\n"
            "Canonical business facts and tasks live in the Home-compatible paths described by "
            "EMPIRE-ENTRY.md, even when Home is absent. Keep one authoritative record and links. "
            "The Home capability installation remains optional and separately licensed.\n").encode()


def composition_valid(root: Path, pin: dict) -> bool:
    payload = read_vault_regular(root, COMPOSITION)
    if payload is None:
        return False
    try:
        state = json.loads(payload)
    except (ValueError, TypeError):
        return False
    if not isinstance(state, dict) or state.get("schema") != COMPOSITION_SCHEMA or state.get("commit") != pin["commit"]:
        return False
    home = read_vault_regular(root, HOME_CONTRACT)
    owner = read_vault_regular(root, OWNER_CONTRACT)
    actual = read_vault_regular(root, "AGENTS.md")
    if home is None or owner is None or actual is None:
        return False
    return (sha256_bytes(home.replace(b"\r\n", b"\n")) == pin["files"]["AGENTS.md"]
            and state.get("home_agents_sha256") == sha256_bytes(home)
            and state.get("owner_agents_sha256") == sha256_bytes(owner)
            and state.get("generated_agents_sha256") == sha256_bytes(composed_agents())
            and actual == composed_agents())


def compose_bundle(root: Path, source: str | Path, operation_id: str, generated_at: str) -> dict:
    adapter.read(root, PROFILE)
    adapter.read(root, ENTRY)
    source_root = adapter.safe_root(source)
    report = adapter.inspect_home(source_root)
    if report["mismatches"]:
        adapter.fail("composition requires a qualified separately obtained Home source")
    pin = json.loads(adapter.PIN.read_text(encoding="utf-8"))
    home = adapter.read(source_root, "AGENTS.md")
    if sha256_bytes(home.replace(b"\r\n", b"\n")) != pin["files"]["AGENTS.md"]:
        adapter.fail("composition source must contain the original pinned Home contract")
    existing_state = read_vault_regular(root, COMPOSITION)
    if existing_state is not None:
        if not composition_valid(root, pin):
            adapter.fail("managed composition was edited; preserve and review before replacing")
        owner = adapter.read(root, OWNER_CONTRACT)
    else:
        owner = read_vault_regular(root, "AGENTS.md") or b""
        if owner.replace(b"\r\n", b"\n") == home.replace(b"\r\n", b"\n"):
            owner = b"# Empire owner instructions\n\nNo separate owner contract was supplied before composition.\n"
    state = {"schema": COMPOSITION_SCHEMA, "commit": pin["commit"], "release": pin["release"],
             "home_agents_sha256": sha256_bytes(home), "owner_agents_sha256": sha256_bytes(owner),
             "generated_agents_sha256": sha256_bytes(composed_agents())}
    prior_root = read_vault_regular(root, "AGENTS.md")
    contents = {HOME_CONTRACT: home, OWNER_CONTRACT: owner, COMPOSITION: encoded(state), "AGENTS.md": composed_agents()}
    return bundle(root, contents, "home-compose", operation_id, generated_at,
                  {"AGENTS.md": prior_root} if prior_root is not None else {})


def scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def note(fields: dict, body: str) -> bytes:
    # JSON values are a safe YAML subset; this public scaffold has no YAML dependency.
    frontmatter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
    return f"---\n{frontmatter}\n---\n\n{body}\n".encode()


def scaffold_bundle(root: Path, specification: str | Path, operation_id: str, generated_at: str) -> dict:
    try:
        source = Path(specification)
        if source.stat().st_size > 65536:
            adapter.fail("record specification exceeds 64 KiB")
        spec = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        adapter.fail("record specification must be readable JSON")
    if not isinstance(spec, dict) or spec.get("schema") != "legends.home-records/v1":
        adapter.fail("unknown record specification")
    if set(spec) - {"schema", "project_id", "title", "description", "company_id", "brand_id", "kind"}:
        adapter.fail("unknown specification fields; no automatic facts or readiness")
    project = slug(spec.get("project_id"))
    kind = spec.get("kind", "whole-life")
    if kind not in ("whole-life", "marketing"):
        adapter.fail("kind must be whole-life or marketing")
    title, description = spec.get("title", project), spec.get("description", "")
    if not isinstance(title, str) or not isinstance(description, str) or len(title) > 256 or len(description) > 8192:
        adapter.fail("title/description must be bounded strings")
    if any(ord(c) < 32 and c not in "\n\t" for c in title + description):
        adapter.fail("record text contains control characters")
    try:
        date = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        adapter.fail("generated-at must be ISO-8601")
    base = f"Projects/Active/{project}"
    identity = str(uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve()) + "/" + base))
    overview = {"pm-project": True, "id": identity, "title": title, "description": description,
                "color": "#64748b", "icon": "folder", "taskIds": [], "customFields": [],
                "teamMembers": [], "savedViews": [], "createdAt": generated_at, "updatedAt": generated_at}
    context = {"type": "project-context", "status": "draft", "project": f"[[{base}/{project}]]",
               "client": None, "brand": None, "site": None, "audience": None, "brain": None,
               "workflow": None, "lead_agent": "orchestrator", "created": date, "updated": date,
               "tags": [kind]}
    contents = {f"{base}/{project}.md": note(overview, f"# {title}\n\n{description}\n\n## Tasks\n\nNo tasks have been created."),
                f"{base}/_tasks/.empire-slot.json": encoded({"schema": PROFILE_SCHEMA, "project_id": identity})}
    company, brand = spec.get("company_id"), spec.get("brand_id")
    if kind == "whole-life" and (company is not None or brand is not None):
        adapter.fail("whole-life scaffold does not fabricate marketing business records")
    if company is not None:
        company = slug(company)
        target = f"Workspaces/Clients/Companies/{company}"
        context["client"] = f"[[{target}]]"
        contents[target + ".md"] = note({"type": "company", "status": "draft", "relationship": None,
            "industry": None, "website": None, "people": [], "brands": [], "created": date, "tags": []},
            f"# {company}\n\nBusiness facts have not yet been supplied.")
    if brand is not None:
        brand = slug(brand)
        target = f"Workspaces/Brands & Sites/{brand}"
        context["brand"] = f"[[{target}/brand]]"
        context["site"] = f"[[{target}/sites]]"
        # No audience is selected until a real segment is established.
        for name, record_type in (("brand", "brand"), ("offer-and-claims", "brand-claims"),
                                  ("audiences", "audiences"), ("channels", "channels"), ("sites", "sites")):
            fields = {"type": record_type, "status": "draft", "created": date, "tags": []}
            if name == "brand":
                fields.update({"company": context["client"], "primary_site": None, "reviewed": None})
            contents[f"{target}/{name}.md"] = note(fields,
                                                     f"# {name}\n\nNo confirmed business facts recorded.")
        contents[f"{target}/results/README.md"] = b"# Results\n\nNo measured results recorded.\n"
    contents[f"{base}/context.md"] = note(context, "# Project context\n\nDraft only. Select real business evidence and methods before native Home work. Ordinary Empire work remains available.")
    references = {}
    for path in list(contents):
        if not path.startswith("Workspaces/"):
            continue
        old = read_vault_regular(root, path)
        if old is None or old == contents[path]:
            continue
        if not path.endswith("/results/README.md"):
            expected_type = re.search(rb'^type: "([a-z-]+)"$', contents[path], re.MULTILINE).group(1)
            try:
                text = old.decode("utf-8").replace("\r\n", "\n")
            except UnicodeError:
                adapter.fail("existing canonical record is not UTF-8")
            front = text.split("\n---\n", 1)[0] if text.startswith("---\n") else ""
            types = re.findall(r'^type:\s*[\"\']?([a-z-]+)[\"\']?\s*$', front, re.MULTILINE)
            if types != [expected_type.decode()]:
                adapter.fail(f"existing canonical record has unknown or conflicting type: {path}")
        references[path] = sha256_bytes(old)
        del contents[path]
    result = bundle(root, contents, "home-records", operation_id, generated_at)
    result["read_preconditions"].update(references)
    return result


def write_allowed(operation: str, path: str, mode: str) -> bool:
    if operation == "home-profile":
        return mode == "create" and path in {PROFILE, ENTRY, "AGENTS.md", *(f"{p}/.empire-slot.json" for p in SLOTS)}
    if operation == "home-compose":
        return (path == "AGENTS.md" and mode in ("create", "replace")) or (
            path in {HOME_CONTRACT, OWNER_CONTRACT, COMPOSITION} and mode == "create")
    if operation != "home-records" or mode != "create":
        return False
    identifier = r"[a-z][a-z0-9-]{0,63}"
    return bool(re.fullmatch(rf"Projects/Active/({identifier})/(\1\.md|context\.md|_tasks/\.empire-slot\.json)", path)
                or re.fullmatch(rf"Workspaces/Clients/Companies/{identifier}\.md", path)
                or re.fullmatch(rf"Workspaces/Brands & Sites/{identifier}/(brand\.md|offer-and-claims\.md|audiences\.md|channels\.md|sites\.md|results/README\.md)", path))
