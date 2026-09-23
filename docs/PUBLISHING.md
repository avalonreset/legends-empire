# Public release process

Public distribution: https://github.com/avalonreset/legends-obsidian
Upstream: https://github.com/AgriciDaniel/claude-obsidian (MIT).

Never push a contributor checkout with vault state. Use the explicit release
allowlist to create a clean staging tree, retain attribution, review it, commit
that tree, then run `python scripts/claude-obsidian.py release build --output PATH`.
Run `release audit PATH` before upload. Excluded local vaults remain untouched.

Run `make test` on POSIX and the documented portable suite on Windows.
Publish only after owner authorization, using title `legends-obsidian vX.Y.Z`.
Install from the reviewed ZIP; no Python package manager is required.
