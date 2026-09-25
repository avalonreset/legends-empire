"""Portable core for claude-empire.

The package deliberately has no third-party runtime dependencies. Host-specific
plugins and skills call this core instead of deriving mutable vault state from
the plugin installation directory.
"""

from __future__ import annotations

__version__ = "3.0.0"
