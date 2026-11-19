"""Loopback CDP preflight shared by the Google Flow tools.

Flow live runs drive a browser over the DevTools protocol. The harness expects
that browser to be a user-session Chrome on a loopback port — one systemd user
unit, `chrome-cdp.service` (template: `custom/config/systemd/chrome-cdp.service`,
installed by `make -f custom/Makefile chrome-cdp-install`). This module is the
single gate the Flow entry (`custom/plugins/studiowright/cli.py`) passes through
before it submits anything:

    from custom.tools.common.chrome_cdp import ensure_cdp
    status = ensure_cdp()          # status.error is already redacted
    if not status.ok:
        ...

    python3 -m custom.tools.common.chrome_cdp    # same check, JSON + exit code

Rules encoded here:

* The endpoint is `CHROME_CDP_URL`, else the local default `http://127.0.0.1:9222`.
* Only a loopback endpoint may start the local unit. A remote `CHROME_CDP_URL`
  is probed and, when unreachable, reported — never "fixed" by starting a local
  browser.
* Credentials never reach a log, an exception or the wire: the probe strips the
  URL's userinfo and query, and every message goes through `redact()`.
* The only command this module runs is `systemctl --user start chrome-cdp.service`.
  It never enables it, never installs a unit, never launches a browser itself
  and never touches a Chrome profile. `enable` is a deploy decision (MOZ-32).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import zlib
from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit, urlunsplit

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
ENV_CDP_URL = "CHROME_CDP_URL"
UNIT = "chrome-cdp.service"
START_COMMAND = ["systemctl", "--user", "start", UNIT]
WAIT_SECONDS = 3.0
PROBE_TIMEOUT = 2.0
START_TIMEOUT = 15.0
_VERSION_KEYS = ("webSocketDebuggerUrl", "Browser")

# StudioWright's own Chrome is a *different* loopback endpoint from the unit's
# 9222. Its port is chrome.py `debug_port()`: PORT_BASE + crc32(profile) % PORT_SPAN
# (StudioWright 0.2.13), so profile "default" -> 9319. The preflight above only
# ever gates 9222; these helpers exist so a failure can name which port is
# actually dead instead of blaming the endpoint that already answered.
SW_PORT_BASE = 9300
SW_PORT_SPAN = 90
SW_DEFAULT_PROFILE = "default"
_SW_PORT_FAILURE_MARKERS = (
    "debugging port", "connection refused", "connectionrefused",
    "connect call failed", "econnrefused", "err_connection_refused",
)


@dataclass
class CdpStatus:
    """The outcome of one preflight. `url` is redacted; `error` is log-safe."""

    ok: bool
    url: str
    local: bool
    probes: int = 0
    service_started: bool = False
    service_returncode: int | None = None
    error: str | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "url": self.url,
            "loopback": self.local,
            "probes": self.probes,
            "service": UNIT,
            "service_started": self.service_started,
            "service_returncode": self.service_returncode,
            "error": self.error,
            **self.detail,
        }


def cdp_url() -> str:
    """The endpoint to preflight: `CHROME_CDP_URL`, else the local default."""
    return (os.environ.get(ENV_CDP_URL) or "").strip() or DEFAULT_CDP_URL


def _parts(url: str):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return parts


def is_loopback(url: str) -> bool:
    """True for 127.0.0.0/8, ::1 and localhost — the only hosts we may start."""
    parts = _parts(url)
    if parts is None:
        return False
    host = (parts.hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def redact(url: str) -> str:
    """`url` with userinfo and query replaced — the only form that may be logged."""
    parts = _parts(url)
    if parts is None:
        return "<unparsable url>"
    netloc = parts.netloc.rsplit("@", 1)[-1]
    if "@" in parts.netloc:
        netloc = "***@" + netloc
    safe = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    return safe + ("?***" if parts.query else "")


def _fetch_version(url: str, timeout: float) -> bytes | None:
    """GET `<url>/json/version` on http(s) and return the body, or None.

    Built on http.client rather than urlopen so no scheme can be smuggled in,
    and so the request carries no Authorization header: a gateway that demands
    auth is reported as unreachable instead of being probed with a secret.
    """
    parts = _parts(url)
    if parts is None or parts.scheme not in ("http", "https"):
        return None
    host = parts.hostname or ""
    if not host:
        return None
    path = parts.path.rstrip("/")
    if not path.endswith("/json/version"):
        path += "/json/version"
    conn_class = HTTPSConnection if parts.scheme == "https" else HTTPConnection
    try:
        conn = conn_class(host, parts.port, timeout=timeout)
        try:
            conn.request("GET", path)
            return conn.getresponse().read(4096)
        finally:
            conn.close()
    except Exception:
        return None


def probe_cdp(url: str, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when `url` answers `/json/version` with a DevTools payload."""
    body = _fetch_version(url, timeout)
    if body is None:
        return False
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and any(key in data for key in _VERSION_KEYS)


def sw_debug_port(profile: str | None = None) -> int:
    """The loopback port `sw` starts its own Chrome on ("default" -> 9319)."""
    name = (profile or SW_DEFAULT_PROFILE).encode("utf-8")
    return SW_PORT_BASE + zlib.crc32(name) % SW_PORT_SPAN


def sw_port_status(profile: str | None = None, probe=probe_cdp) -> dict:
    """Probe sw's port. Injected probe: tests never open a socket."""
    name = profile or SW_DEFAULT_PROFILE
    port = sw_debug_port(name)
    url = f"http://127.0.0.1:{port}"
    return {"profile": name, "port": port, "url": url, "reachable": bool(probe(url))}


def looks_like_sw_port_failure(text: str) -> bool:
    """True when `sw` failed on its own browser port rather than on anything else."""
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _SW_PORT_FAILURE_MARKERS)


def sw_port_note(cdp: CdpStatus, sw: dict) -> str:
    """One sentence keeping the two loopback ports apart in a failure report."""
    state = "answered" if sw.get("reachable") else "did not answer"
    return (
        f"{cdp.url} is the harness CDP endpoint and it answered; sw does not use it. "
        f"sw starts its own Chrome on 9300 + crc32(profile) % 90, which is "
        f"{sw['port']} for profile '{sw['profile']}', and that port {state}."
    )


def _first_line(text: str, limit: int = 200) -> str:
    line = next((item.strip() for item in (text or "").splitlines() if item.strip()), "")
    return line[:limit]


def ensure_cdp(
    url: str | None = None,
    *,
    wait_seconds: float = WAIT_SECONDS,
    probe=probe_cdp,
    runner=subprocess.run,
    sleep=time.sleep,
) -> CdpStatus:
    """Probe the CDP endpoint; start the local unit once when it is loopback.

    `probe`, `runner` and `sleep` are injectable so tests never touch a socket,
    systemd or the clock.
    """
    target = (url or cdp_url()).strip()
    shown = redact(target)
    local = is_loopback(target)

    parts = _parts(target)
    if parts is None or parts.scheme not in ("http", "https"):
        return CdpStatus(
            ok=False, url=shown, local=local,
            error=f"{ENV_CDP_URL} {shown} is not an http(s) DevTools endpoint: the "
                  "preflight cannot verify it, and it will not start a browser for it.",
        )

    if probe(target):
        return CdpStatus(ok=True, url=shown, local=local, probes=1)

    if not local:
        return CdpStatus(
            ok=False, local=False, url=shown, probes=1,
            error=f"remote CDP endpoint {shown} did not answer. A non-loopback "
                  f"{ENV_CDP_URL} never starts the local {UNIT}; point it at a "
                  "reachable endpoint or unset it to use the local default.",
        )

    try:
        finished = runner(START_COMMAND, capture_output=True, text=True, timeout=START_TIMEOUT)
        code = int(getattr(finished, "returncode", 1))
        output = getattr(finished, "stderr", "") or getattr(finished, "stdout", "") or ""
    except Exception as exc:  # systemctl missing, timeout, permission — all "not started"
        code, output = 1, str(exc)

    sleep(wait_seconds)

    if probe(target):
        return CdpStatus(ok=True, url=shown, local=True, probes=2,
                         service_started=True, service_returncode=code)

    reason = (
        f"`{' '.join(START_COMMAND)}` exited {code}: {_first_line(output)}"
        if code else f"`{' '.join(START_COMMAND)}` reported success"
    )
    return CdpStatus(
        ok=False, local=True, url=shown, probes=2,
        service_started=True, service_returncode=code,
        error=f"local CDP endpoint {shown} did not answer after a {wait_seconds:g}s retry; "
              f"{reason.rstrip('.')}. Deploy the unit with `make -f custom/Makefile chrome-cdp-install` "
              f"(docs: custom/docs/FLOW.md) or set {ENV_CDP_URL} to a reachable endpoint.",
    )


if __name__ == "__main__":
    result = ensure_cdp()
    print(json.dumps(result.as_dict(), indent=1, ensure_ascii=False))
    raise SystemExit(0 if result.ok else 1)
