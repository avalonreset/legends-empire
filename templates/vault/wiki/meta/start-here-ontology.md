---
type: meta
title: Start Here Ontology Engine
status: developing
created: {{generated_date}}
updated: {{generated_date}}
tags:
  - meta
  - ontology
---

# Start Here: Ontology Engine

This vault includes the Legends Empire Ontology Engine, which gives your markdown notes structured, queryable entities, facts, and provenance.

## 1. How Entities & Facts Work

In any note within your content roots, declare an `entity_id` and optional `aliases`:

```yaml
---
entity_id: example-service
title: Example Service
aliases:
  - Example Service
  - ES
fact.domain: example.com
fact.phone: 555-0100
certified_owner:
  - fact.domain
certified_on: 2026-11-14
---
```

- **`entity_id`**: Marks this note as the entity's primary note (`^[a-z][a-z0-9-]{1,47}$`).
- **`about`**: In secondary notes (receipts, supplier logs), declare facts about an existing entity.
- **`fact.<field>`**: Flat key-value scalars (max 500 characters).
- **`aliases`**: Alternative names for fast CLI resolution.

## 2. Querying Facts

Use `ontology need` in automated scripts and agent workflows before taking actions (PATH- and CWD-independent via the router, or from the module checkout as current working directory):

```bash
# Verify required facts are certified and fresh
python -m cto_legends --home <cto-legends-home> run legends-empire -- ontology need --vault <vault> --keys example-service.domain,example-service.phone
# From the module source checkout (requires the repo root as CWD):
python scripts/claude-empire.py ontology need --vault <vault> --keys example-service.domain,example-service.phone
```

Exit codes:
- **0**: All requested facts are certified and fresh.
- **10**: Draft facts present (unapproved).
- **11**: Missing facts.
- **12**: Conflicting facts across notes.
- **13**: Stale facts (expired review date or evidence mismatch).

## 3. Proposing & Certifying

`ontology propose` and `ontology certify` require `--generated-at <ISO-UTC>` and have no `--apply` flag; they write reversible transaction bundles into the derived cache under `.vault-meta/ontology/bundles/` for `transaction apply`.

1. Generate alias proposals for unindexed notes:
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-empire -- ontology propose --vault <vault> --operation-id prop-1 --generated-at 2026-11-16T00:00:00Z
   ```
2. Review and certify owner facts:
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-empire -- ontology certify --vault <vault> --keys example-service.domain --class owner --digest path/to/digest.md --operation-id cert-1 --generated-at 2026-11-16T00:00:00Z
   ```
3. Inspect and apply an emitted bundle:
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-empire -- transaction inspect --vault <vault> --bundle <bundle.json>
   python -m cto_legends --home <cto-legends-home> run legends-empire -- transaction apply --vault <vault> --bundle <bundle.json> --approved-plan-sha256 <sha256>
   ```
