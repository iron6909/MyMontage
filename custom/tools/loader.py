"""custom/tools/loader.py — Bootstrap script for custom tool registration.

Run this ONCE at startup (before registry.discover()) to register all tools
in custom/tools/ into the shared OpenMontage registry.

Usage (from the REPO ROOT):
    python -c "import custom.tools.loader"

Or add to your startup script:
    python -m custom.tools.loader

The script:
1. Ensures `custom/` is on sys.path so `custom.tools.*` is importable.
2. Calls registry.discover("custom.tools") — the same mechanism used for
   the upstream `tools` package, requiring zero modifications to upstream code.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the repo root is on sys.path so both `tools` and `custom.tools` resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.tool_registry import registry  # noqa: E402

_CUSTOM_PKG = "custom.tools"


def load() -> list[str]:
    """Register all custom tools into the shared registry.

    Returns the list of tool names that were registered.
    Safe to call multiple times — the registry deduplicates.
    """
    registered = registry.discover(_CUSTOM_PKG)
    return registered


if __name__ == "__main__":
    names = load()
    print(f"[custom.tools.loader] Registered {len(names)} custom tool(s): {names}")
