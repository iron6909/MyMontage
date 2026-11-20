"""Loopback CDP preflight checks for the Flow entry point.

Plain asserts, no pytest, no network egress, no systemd. Run from the
repository root:

    python3 -m custom.tests.test_flow_cdp_preflight

Nothing here starts a browser, calls `systemctl`, sleeps for real or reads
`$HOME/.harness/providers.env` or any other credential store: the probe, the
service runner and the sleep are injected, and every URL is fake. The remote
checks use `flow.invalid` (RFC 6761) and never resolve it.

What is pinned:

* The Flow CLI leaves the independent harness preflight unused.
* A dead loopback endpoint gets exactly one `systemctl --user start
  chrome-cdp.service`, one ~3 s wait, a second probe, and then a non-zero exit.
* A non-loopback `CHROME_CDP_URL` is never "fixed" by starting the local unit.
* Credentials and query strings from the URL never reach the status, the error
  text or the JSON report.
* The shipped `chrome-cdp.service` template keeps every switch as its own
  argument, binds loopback only, and carries no secret.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

# custom/ has no pyright execution environment: these resolve from the repo root
# at runtime, which is how every documented entry point runs.
# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.plugins.studiowright import cli

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.common import chrome_cdp

UNIT_FILE = Path(__file__).resolve().parent.parent / "config" / "systemd" / "chrome-cdp.service"
LOCAL_URL = "http://127.0.0.1:9222"
REMOTE_URL = "http://flow.invalid:9222"
# Fake userinfo/query, assembled from parts so this file cannot be mistaken for
# a committed credential. They must never show up in status, error or JSON.
_FAKE_USERINFO = "svc" + ":" + "fake-moz31-auth"
_FAKE_QUERY = "fake-moz31-query"
AUTHED_URL = f"http://{_FAKE_USERINFO}@flow.invalid:9222/json/version?token={_FAKE_QUERY}"
LEAK_MARKERS = ("fake-moz31-auth", "fake-moz31-query")


class Probe:
    """A scripted fake probe: answers from a list, then repeats the last answer."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float = 0.0) -> bool:
        self.calls.append(url)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class Runner:
    """A fake `systemctl` runner: records argv and returns a canned result."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


class Clock:
    """A fake sleep: records the requested delay instead of waiting."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def test_endpoint_defaults_and_loopback_detection() -> None:
    assert chrome_cdp.DEFAULT_CDP_URL == LOCAL_URL
    with patch.dict(os.environ, {chrome_cdp.ENV_CDP_URL: ""}):
        assert chrome_cdp.cdp_url() == LOCAL_URL
    with patch.dict(os.environ, {chrome_cdp.ENV_CDP_URL: REMOTE_URL}):
        assert chrome_cdp.cdp_url() == REMOTE_URL
    for url in (LOCAL_URL, "http://localhost:9222", "http://[::1]:9222",
                "http://127.0.0.53:9222"):
        assert chrome_cdp.is_loopback(url), url
    for url in (REMOTE_URL, "http://0.0.0.0:9222", "http://192.168.1.9:9222", "nonsense"):
        assert not chrome_cdp.is_loopback(url), url


def test_redact_drops_credentials_and_query() -> None:
    shown = chrome_cdp.redact(AUTHED_URL)
    for marker in LEAK_MARKERS:
        assert marker not in shown, shown
    assert shown.startswith("http://***@flow.invalid:9222/"), shown
    assert shown.endswith("/json/version?***"), shown
    assert chrome_cdp.redact("nonsense") == "<unparsable url>"
    # The real probe refuses a non-http(s) endpoint without touching the network.
    assert not chrome_cdp.probe_cdp("ws://127.0.0.1:9222/devtools/browser/abc")
    assert not chrome_cdp.probe_cdp("http://127.0.0.1:1/")


def test_reachable_endpoint_starts_nothing() -> None:
    probe, runner, clock = Probe(True), Runner(), Clock()
    status = chrome_cdp.ensure_cdp(LOCAL_URL, probe=probe, runner=runner, sleep=clock)
    assert status.ok and status.probes == 1, status.as_dict()
    assert not status.service_started and status.service_returncode is None
    assert runner.calls == [] and clock.slept == []


def test_dead_loopback_starts_the_unit_once_then_succeeds() -> None:
    probe, runner, clock = Probe(False, True), Runner(), Clock()
    status = chrome_cdp.ensure_cdp(LOCAL_URL, probe=probe, runner=runner, sleep=clock)
    assert status.ok and status.probes == 2, status.as_dict()
    assert status.service_started and status.service_returncode == 0
    assert runner.calls == [["systemctl", "--user", "start", "chrome-cdp.service"]]
    assert clock.slept == [chrome_cdp.WAIT_SECONDS], clock.slept


def test_dead_loopback_after_retry_is_explicit() -> None:
    probe = Probe(False)
    runner = Runner(returncode=5, stderr="Failed to start chrome-cdp.service: Unit not found.")
    clock = Clock()
    status = chrome_cdp.ensure_cdp(LOCAL_URL, probe=probe, runner=runner, sleep=clock)
    assert not status.ok and status.local
    assert len(runner.calls) == 1, runner.calls          # exactly one attempt, never a loop
    assert clock.slept == [chrome_cdp.WAIT_SECONDS]      # one ~3 s wait, not a backoff storm
    assert status.service_returncode == 5
    assert "Unit not found." in (status.error or "")
    assert "chrome-cdp-install" in (status.error or ""), status.error


def test_remote_url_never_starts_the_local_unit() -> None:
    probe, runner, clock = Probe(False), Runner(), Clock()
    with patch.dict(os.environ, {chrome_cdp.ENV_CDP_URL: AUTHED_URL}):
        status = chrome_cdp.ensure_cdp(probe=probe, runner=runner, sleep=clock)
    assert not status.ok and not status.local
    assert runner.calls == [], runner.calls
    assert clock.slept == [], clock.slept
    report = json.dumps(status.as_dict())
    for marker in LEAK_MARKERS:
        assert marker not in report and marker not in (status.error or ""), marker
    assert chrome_cdp.ENV_CDP_URL in (status.error or ""), status.error


def test_live_flow_uses_its_backend_without_the_harness_endpoint() -> None:
    from tools.base_tool import ToolResult

    calls = []

    class Tool:
        name, provider, model = "flow_image", "google_flow", None

        def execute(self, inputs):
            calls.append(inputs)
            return ToolResult(success=False, error="backend was invoked")

    buffer = io.StringIO()
    with patch.object(cli, "_tool", return_value=Tool()), \
            patch.object(chrome_cdp, "ensure_cdp", side_effect=AssertionError("unused endpoint")), \
            patch.dict(os.environ, {"CHROME_CDP_URL": REMOTE_URL}), \
            contextlib.redirect_stdout(buffer):
        code = cli.main(["image", "--prompt", "x"])
    report = json.loads(buffer.getvalue())
    assert code == 1 and report["error"] == "backend was invoked", report
    assert len(calls) == 1, calls


def test_unit_template_is_loopback_only_and_secret_free() -> None:
    text = UNIT_FILE.read_text(encoding="utf-8")
    joined = re.sub(r"\\\s*\n\s*", " ", text)   # what systemd does to continuations
    exec_line = next(line for line in joined.splitlines() if line.startswith("ExecStart="))
    args = exec_line.split("=", 1)[1].split()

    for flag in ("--remote-debugging-port=9222",
                 "--remote-debugging-address=127.0.0.1",
                 "--remote-allow-origins=*",
                 "--user-data-dir=%h/.harness/chrome-flow-profile",
                 "--password-store=basic",
                 "--no-first-run",
                 "--no-default-browser-check"):
        assert args.count(flag) == 1, (flag, args)   # one separate argument each
    assert args[-1] == "https://labs.google/fx/tools/flow", args[-1]
    assert "Restart=always" in text

    binds = [arg for arg in args if arg.startswith("--remote-debugging-address=")]
    assert binds == ["--remote-debugging-address=127.0.0.1"], binds
    assert "--no-sandbox" not in text
    assert not re.search(r"(?i)bearer|api[_-]?key|secret|token|password=", text), \
        "the template must not carry a secret"


if __name__ == "__main__":
    checks = (
        test_endpoint_defaults_and_loopback_detection,
        test_redact_drops_credentials_and_query,
        test_reachable_endpoint_starts_nothing,
        test_dead_loopback_starts_the_unit_once_then_succeeds,
        test_dead_loopback_after_retry_is_explicit,
        test_remote_url_never_starts_the_local_unit,
        test_live_flow_uses_its_backend_without_the_harness_endpoint,
        test_unit_template_is_loopback_only_and_secret_free,
    )
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"{len(checks)} CDP preflight checks passed")
