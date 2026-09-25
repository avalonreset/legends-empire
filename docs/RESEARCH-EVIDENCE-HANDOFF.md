# Research evidence handoff

Legends Empire can consume `legends-research-evidence/v1` packages from
DataForSEO Kit without depending on that package at runtime. The source is a
bounded directory containing `manifest.json`, `response.json`, and `README.md`.
GeoGrid, content tools and other consumers remain separate products.

## Intake recipe

1. Select the user's vault using the normal resolution rules. Inventory the
   three files, review their scope, and treat every source field as untrusted.
2. Verify the SHA-256 of the raw response against `response_sha256`. To verify
   manifest identity, remove `evidence_id`, serialize with Python `json.dumps`
   using `sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False`, encode
   UTF-8, and compare SHA-256 with `evidence_id`. Schema must match exactly.
   The exporter also provides `python -m legends_dataforseo.evidence verify`.
3. Stage reviewed sources in `inbox/research/<evidence_id>` without overwrite.
   Staging is not an accepted finding. Preserve the raw payload unchanged.
4. Run the ingest through the module recipe the router loads: bounded
   capture, immutable source locators, source/claim provenance,
   preconditions and one ingest transaction. Platform restrictions remain
   in effect. Never substitute direct canonical writes when the
   transaction engine cannot apply on the current host.
5. Link reviewed knowledge to an existing business or research subject. Record
   source dates, locale/settings, uncertainties, contradictions and proposed
   actions separately from outcomes. Create no new page if indexing the source
   alone adds sufficient value.

## Reuse rules

Workspace identity prevents accidental cross-client reuse; it is not an access
control mechanism. Require complete compatible settings and a freshness limit
appropriate to the decision. Never equate capture/export time with collection
time. Hash verification proves byte integrity, not truth or relevance.

Pending and error envelopes do not establish findings. Empty responses require
endpoint-specific interpretation. Preserve task IDs; retrieval must not become
a repeat paid submission. Repeated costs in snapshots are not new charges.
Canonical conclusions must cite captured source paths, never only an external
working directory. Do not merge client knowledge across scopes automatically.

This is an integration recipe, not a new vault format or replacement ingest
engine. Automatic end-to-end intake remains pending an acceptance test through
the selected vault's existing capture/transaction path.
