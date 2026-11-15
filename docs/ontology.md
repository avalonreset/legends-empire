# Ontology Engine Guide

The Ontology Engine provides an answerable vault index over structured markdown entities and facts. It offers deterministic fact resolution, strict frontmatter validation, provenance tracking, and reversible transaction proposals.

---

## 1. Overview & Core Commands

The ontology workflow consists of read-only queries and two-phase transaction bundles:

1. **`ontology index [--check]`**: Scans the vault, checks frontmatter consistency, resolves conflicts, and writes the derived index (`.vault-meta/ontology/index.json`). With `--check`, verifies whether the stored index matches the vault state, `as_of` date, and configured roots.
2. **`ontology resolve <term>`** (or `--term <term>`): Resolves an entity ID (case-insensitive), alias, or title to its primary vault note.
3. **`ontology need --keys <key1,key2>`**: Validates fact existence, freshness, and certification before executing automated actions.
4. **`ontology ask "<question>"`** (or `--question "<question>"`): Suggests relevant entities, facts, and verification states for conversational queries.
5. **`ontology propose [--scope <prefix>]`**: Emits reversible transaction bundle(s) proposing `entity_id`, `entity_type`, and `aliases` for untagged canonical entity notes, and reports any per-note `skipped` or `skipped_aliases` entries without aborting the run.
6. **`ontology certify --keys ... --class owner|evidence [--secrets-root <dir>]`**: Prepares a transaction bundle adding facts to owner or evidence certification (verifying any `secret-ref:` pointers against `--secrets-root` by filename and variable name only).

Applying mutating bundles is always gated through the transaction engine:
```bash
claude-empire transaction inspect --bundle <bundle.json>
claude-empire transaction apply --bundle <bundle.json> --approved-plan-sha256 <sha256>
```

---

## 2. Frontmatter Vocabulary

All keys are flat, ensuring compatibility with standard Obsidian properties and Markdown linters.

| Key | Type | Description |
|---|---|---|
| `entity_id` | `string` | Unique vault-wide identifier (`^[a-z][a-z0-9-]{1,47}$`). Marks this note as the entity's **primary note**. |
| `about` | `string` | An existing `entity_id`. Declares that this note provides secondary facts about that entity. Cannot be combined with `entity_id`. |
| `aliases` | `list` | Alternative names and search terms. Normalized using NFKC + casefold + whitespace collapsing. |
| `fact.<field>` | `string` | Single-line fact scalar (max 500 characters, field name `^[a-z][a-z0-9_]{0,47}$`). Max 40 facts per note. |
| `evidence.<field>` | `string` | Evidence pointer: `raw:sha256:<hex>`, `bank:<ws>/<rec>`, or `note:[[Target]]#<anchor>`. |
| `certified_owner` | `list` | List of `fact.<field>` keys approved by the vault owner. |
| `certified_evidence` | `list` | List of `fact.<field>` keys verified against evidence locators. |
| `certified_on` | `ISO date` | Date (`YYYY-MM-DD`) when certification was applied. |
| `review_after` | `ISO date` | Expiration date (`YYYY-MM-DD`). After this date, certified facts become `stale`. |
| `fact_source` | `string` | Optional human-readable pointer or description. |

### Secret Pointers & Redaction
- Raw secrets (tokens, API keys, passwords, private keys, JWTs) are strictly forbidden in frontmatter values.
- Secret values may only be referenced via pointers:
  ```yaml
  fact.stripe_key: secret-ref:credentials.env#STRIPE_SECRET_KEY
  ```
- If a raw secret is encountered in hand-edited files, the ontology index automatically redacts it from output and reports finding `ontology_secret_like_value`.

---

## 3. Exit Code Precedence & Verification

The `ontology need` command acts as an automated prerequisite gate:

| Exit Code | State | Severity Precedence |
|---|---|---|
| **12** | `conflict` | **Highest** (disagreeing uncertified facts) |
| **11** | `missing` | Fact key does not exist in any scanned note |
| **13** | `stale` | Fact is expired (`review_after < as_of`) or evidence hash mismatch |
| **10** | `draft` | Fact is present but not certified |
| **0** | `certified` | All requested facts are certified and fresh |

When multiple keys are queried with mixed states, the worst severity wins:
$$\mathbf{12 > 11 > 13 > 10 > 0}$$

### Resolve Exit Codes
- **0**: Exactly one entity match found.
- **3**: Ambiguous query (multiple entities match the term or alias).
- **4**: Not found.

### Index Check Exit Codes
- **0**: Index is fresh and matches current vault state.
- **14**: Index is missing or stale.

---

## 4. What "Certified" Means

1. **`draft`**: The fact exists in a note, but has neither owner sign-off nor cryptographic evidence attached.
2. **`certified-evidence`**: The fact is backed by a verified evidence locator:
   - `raw:sha256:<hex>`: Backed by a file in `.raw/captured/` whose content recomputes to the exact SHA-256 digest.
   - `bank:<workspace>/<record>`: Backed by a verified research evidence bank record.
   - `note:[[Target]]#<anchor>`: Backed by a heading in another vault note (self-referencing and instruction file targets are disallowed).
3. **`certified-owner`**: Explicitly reviewed and approved by the owner via a digest file.
4. **`stale`**: A previously certified fact whose `review_after` date has passed or whose evidence file failed hash verification.

Autonomous agents must never invent or self-certify facts. Owner certification requires an explicit review digest pinned in the transaction plan.
