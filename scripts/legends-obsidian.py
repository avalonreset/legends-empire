#!/usr/bin/env python3
"""Provider-neutral entry point for Daniel Agrici's preserved upstream core."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claude_obsidian.cli import main

raise SystemExit(main())
