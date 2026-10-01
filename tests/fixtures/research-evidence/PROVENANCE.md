# Synthetic producer parity fixtures

Generated from the actual concurrent producer implementations on 2026-10-01. No client data.

- dataforseo: actual producer `legends_dataforseo.evidence`, source SHA-256 `f7bce4556a27a80ba15fc8e307dea5315c656fc69dbe8c41fa5505a10cacf68b`. Exported and producer-verified locally, no network or paid requests.
- firecrawl: actual producer `legends_firecrawl.evidence`, source SHA-256 `1af9bcf897538a66e58db429f2779f4733e5a1161bafa846d845c9e4bf8d20d8`. Exported and producer-verified locally, no network or paid requests.

The legacy DataForSEO case removes the optional note hash and recalculates identity to represent the pre-extension v1 contract. Other fixtures are unmodified exporter outputs. Regenerate when either contract changes; do not silently reinterpret a new schema.

Additional Firecrawl unknown/null/nested-error/in-progress fixtures were generated from the strict producer source SHA-256 `82c0d94d0259841b41d48f01688efb0704301c7853400ab71f1d23350a60c489` and verified by that producer.
