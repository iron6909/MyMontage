"""`python3 -m custom.plugins.studiowright ...` — same entry as cli.py."""

from __future__ import annotations

from custom.plugins.studiowright.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
