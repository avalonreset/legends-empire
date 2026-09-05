# Release verification — 2.1.1

Checked September 5, 2026. This is evidence for the initial Legends distribution,
not a promise that every future host or model version behaves identically.

## Upstream parity

Daniel Agrici's public latest tag was `v2.1.1` at
`9f8c1199047eac2c3828496279fbb7ba9540b90b`. His public main was
`ad67087cad22ad84cc3288f915588ae42c0c2b44`; this distribution includes that
additional recovery, checkpoint, and response hardening.

An independent comparison verified the complete 78-file protected surface:
77 files are byte-identical. The sole runtime patch makes ZIP audit reads
binary-safe on Windows. Its regression failed before the fix and passed after
it. All 15 original skill trees, operational scripts, hooks, and templates are
unchanged. Baseline and patch digests are in `config/upstream-parity.json`.

## Host checks

| Host evaluated | Discovery / package proof | Model-driven read-only query |
|---|---|---|
| Grok CLI 1.0.13 | Actual CLI discovered all 16 skills | Passed; loaded router and query skill, returned correct facts and heading citation |
| Codex CLI 0.153.2 | Actual app-server discovered all 16 candidate skills, enabled, without discovery errors | Passed with existing subscription authentication; loaded both skills and returned correct facts and citation |
| Claude Code 2.1.209 | Actual plugin and marketplace validators passed | Not completed; authentication required refresh in the evaluation environment |
| Gemini CLI 0.31.0 | Actual installed skill loader parsed all 16 skills | Not completed; the evaluation profile lacked working authentication |

The synthetic query fixture had three files. Before/after hashes matched for
the completed model checks. Both answers correctly disclosed the absence of
independent source/provenance-ledger support. No private user vault was used.

The Codex live check used a process-local unconfined runtime with shell tools
and web search disabled and a read-only task. The isolated Windows sandbox
helper in that environment failed before reading files; this check does not
certify that helper. No persistent host settings were changed.

Claude/Gemini adapter compatibility is supported by package/discovery evidence;
it is not represented as completed authenticated model-workflow proof. See
[the host matrix](AGENTS-MATRIX.md) for official discovery paths.

## Platform and package checks

- Full upstream `make test` passed on Linux, including 38 Python/shell test
  files and the executable capability and package contracts.
- Native Windows portable tests passed with their explicitly declared POSIX
  skips; full vault mutations are not supported there by upstream.
- Installer fixtures exercise all seven host layouts, existing-path
  preservation, idempotence, redirected parents, spaces/brackets, and wrapper
  exit codes. The documented comma-separated PowerShell host list has its own
  regression test.
- GitHub CI retains Ubuntu/macOS Python 3.11–3.14, a separate native-Windows
  portable job, and reproducible release checks. Inspect the
  [exact commit's checks](https://github.com/avalonreset/legends-obsidian/actions)
  for current results.
- Two clean release builds were byte-identical and each passed the upstream
  archive audit. Release assets include SHA-256 checksums.
- Gitleaks 8.30.1 found no secrets in the selected source, extracted public
  package, or new distribution history. The scanner binary checksum was
  verified against its official release.
- The release source is promoted from the audited allowlisted package. Private
  contributor vaults, local accounts/configuration, personal machine paths,
  private working history, and runtime state are excluded.

The original MIT license, Daniel's attribution, and upstream acknowledgments
remain intact. CFF 1.2.0 citation metadata also passed schema validation.
