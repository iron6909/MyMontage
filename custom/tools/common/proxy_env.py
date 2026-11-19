"""The environment handed to a StudioWright (`sw`) child process.

`sw` reaches its own Chrome over loopback with `urllib`, which honours
`HTTP(S)_PROXY` and `no_proxy`. A proxy exported without a loopback bypass sends
that request off-box, the port looks dead, and `sw` fails with "Chrome did not
open its debugging port" on a machine where the harness 9222 endpoint answers.

So every `sw` child gets the *whole* parent env — `sw` needs Chrome, profile and
proxy variables this repo knows nothing about — with loopback added to both
`no_proxy` spellings:

    from custom.tools.common.proxy_env import sw_env
    subprocess.run(cmd, env=sw_env(), ...)

Nothing else is rewritten. `CHROME_CDP_URL` in particular is passed through
untouched: a remote endpoint stays remote, and the local 9222 default is a
preflight default, never something this function invents.
"""

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
