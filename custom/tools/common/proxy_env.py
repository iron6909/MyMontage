"""Pass the parent environment to StudioWright with loopback in both no-proxy variables."""

from __future__ import annotations

import os

LOOPBACK_NO_PROXY = ("127.0.0.1", "localhost", "::1")


def no_proxy_value(*current: str | None) -> str:
    """Merge the no_proxy spellings, keep every existing host, add loopback."""
    items: list[str] = []
    seen: set[str] = set()
    for value in current:
        for item in (value or "").replace(";", ",").split(","):
            item = item.strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                items.append(item)
    for host in LOOPBACK_NO_PROXY:
        if host not in seen:
            seen.add(host)
            items.append(host)
    return ",".join(items)


def sw_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """A full copy of `base` (default: `os.environ`) with loopback in no_proxy."""
    env = dict(os.environ if base is None else base)
    merged = no_proxy_value(env.get("NO_PROXY"), env.get("no_proxy"))
    env["NO_PROXY"] = merged
    env["no_proxy"] = merged
    return env
