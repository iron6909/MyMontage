"""custom/tools/loader.py — Bootstrap script for custom tool registration.

Call load() in the process that uses the shared OpenMontage registry.
The upstream discovery pass and custom registration use distinct tool names.

Usage (from the REPO ROOT):
    python -c "from custom.tools.loader import load; print(load())"

To inspect registration in a standalone process:
    python -m custom.tools.loader

The loader registers the concrete BaseTool classes under custom.tools.
"""

from __future__ import annotations

import importlib
import pkgutil
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
    package = importlib.import_module(_CUSTOM_PKG)
    registered: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, f"{_CUSTOM_PKG}."):
        registered.extend(registry.register_module(importlib.import_module(info.name)))
    return registered


if __name__ == "__main__":
    names = load()
    print(f"[custom.tools.loader] Registered {len(names)} custom tool(s): {names}")
