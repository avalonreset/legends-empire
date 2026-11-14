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

Use `claude-empire ontology need` in automated scripts and agent workflows before taking actions:

```bash
# Verify required facts are certified and fresh
claude-empire ontology need --keys example-service.domain,example-service.phone
```

Exit codes:
- **0**: All requested facts are certified and fresh.
- **10**: Draft facts present (unapproved).
- **11**: Missing facts.
- **12**: Conflicting facts across notes.
- **13**: Stale facts (expired review date or evidence mismatch).

## 3. Proposing & Certifying

1. Generate alias proposals for unindexed notes:
   ```bash
   claude-empire ontology propose
   ```
2. Review and certify owner facts:
   ```bash
   claude-empire ontology certify --keys example-service.domain --class owner --digest path/to/digest.md
   ```
