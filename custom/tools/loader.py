"""custom/tools/loader.py — Bootstrap script for custom tool registration.

Run this ONCE at startup (before registry.discover()) to register all tools
in custom/tools/ into the shared OpenMontage registry.

Usage (from the REPO ROOT):
    python -c "import custom.tools.loader"

Or add to your startup script:
    python -m custom.tools.loader

The script:
1. Ensures `custom/` is on sys.path so `custom.tools.*` is importable.
2. Walks `custom.tools` and registers every concrete BaseTool — the same
   `registry.register_module` the upstream `tools` package uses, requiring zero
   modifications to upstream code. One module is skipped (`_SKIP`): the patched
   Piper reference copy, which must not shadow the real provider.
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

# Imported by discovery but deliberately not registered: this is the patched
# *reference copy* of the upstream Piper tool that custom/scripts/setup_local.sh
# mounts onto tools/audio/piper_tts.py. Registering it would shadow the real
# provider with a snapshot that can go stale.
_SKIP = {"custom.tools.audio.piper_tts"}


def load() -> list[str]:
    """Register all custom tools into the shared registry.

    Returns the list of tool names that were registered.
    Safe to call multiple times — the registry deduplicates.
    """
    package = importlib.import_module(_CUSTOM_PKG)
    registered: list[str] = []
    for info in pkgutil.walk_packages(package.__path__, f"{_CUSTOM_PKG}."):
        if info.name in _SKIP:
            continue
        registered.extend(registry.register_module(importlib.import_module(info.name)))
    return registered


if __name__ == "__main__":
    names = load()
    print(f"[custom.tools.loader] Registered {len(names)} custom tool(s): {names}")
