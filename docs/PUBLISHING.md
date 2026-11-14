# Public release process

Public distribution: https://github.com/avalonreset/legends-empire
Upstream: https://github.com/AgriciDaniel/claude-obsidian (MIT).

Never push a contributor checkout with vault state. Use the explicit release
allowlist to create a clean staging tree, retain attribution, review it, commit
that tree, then run `python scripts/claude-empire.py release build --output PATH`.
Run `release audit PATH` before upload. Excluded local vaults remain untouched.

Run `make test` on POSIX and the documented portable suite on Windows.
Publish only after owner authorization, using title `legends-empire vX.Y.Z`.
Install from the reviewed ZIP; no Python package manager is required.

## Disclosure gate

`release build`, `release audit` and `release disclosure` scan every text for
North American phone numbers (the fictional 555-01xx range is
allowed), Tailscale CGNAT (100.64/10) addresses, emails, tokens and home paths.
A maintainer may also keep a private term list outside the repository and pass
it with `--denylist PATH` (or `LEGENDS_RELEASE_DENYLIST`). Entries are matched
after Unicode normalization, case folding, look-alike letter folding and
separator removal; `word:term` entries match whole tokens. Findings report the
entry number and a hash prefix, never the term.

Before promotion the owner runs, from the clean release tree:

```
python scripts/claude-empire.py release disclosure --require-denylist --denylist PATH --text-file CHANGELOG.md
python scripts/claude-empire.py release build --require-denylist --denylist PATH --output OUT.zip
```

`release disclosure` also scans commit messages in `<last-tag>..HEAD` (override
with `--range`). Public CI has no private list and runs the generic layer only.
Encoded payloads such as base64 are not decoded.

