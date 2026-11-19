"""Env passthrough and sw-vs-9222 port diagnostics for the Flow tools.

Plain asserts, no pytest, no network egress, no browser, no systemd. Run from the
repository root:

    python3 -m custom.tests.test_flow_sw_env

What is pinned:

* The `sw` child env is the whole parent env, with both `no_proxy` spellings
  merged and loopback added — existing bypass hosts survive, the caller's dict is
  not mutated, and a real child process sees the result.
* A non-loopback `CHROME_CDP_URL` is passed through unchanged: it is never
  rewritten to the local 9222 default, and never invented when unset.
* StudioWright's own browser port is computed from the profile
  (9300 + crc32(profile) % 90, "default" -> 9319), and a port failure names that
  port instead of blaming the endpoint the preflight already cleared.
* All three tools really hand `sw` that env.
* `--help` / `--plan` / `--dry-run` spawn neither `sw` nor `systemctl`.

Nothing here reads `~/.harness/providers.env`, a cookie store or a real port:
every probe and every child is injected or fake.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.plugins.studiowright import cli

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tests.test_flow_cdp_preflight import Probe

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.audio import flow_music

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.common import chrome_cdp, proxy_env

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.image import flow_image

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.video import flow_video

LOCAL_URL = "http://127.0.0.1:9222"
REMOTE_URL = "http://flow.invalid:9223"          # RFC 6761: never resolved
SW_PORT = 9319                                   # crc32("default") % 90 + 9300
SW_ARGS = ["--json"]                             # a plausible sw argv, never run

# The verbatim studioWright 0.2.13 text (chrome.py `ensure_running`), which names
# no port at all — reading it as "9222 is closed" is the bug this module pins.
UPSTREAM_PORT_ERROR = (
    "Error: Chrome did not open its debugging port. If a StudioWright Chrome "
    "window is already open (for example from `sw auth login`), close it and try again."
)


def test_no_proxy_merge_keeps_existing_hosts() -> None:
    assert proxy_env.no_proxy_value() == "127.0.0.1,localhost,::1"
    assert proxy_env.no_proxy_value("") == "127.0.0.1,localhost,::1"
    merged = proxy_env.no_proxy_value("internal.invalid; .corp.example", "extra.invalid")
    assert merged == "internal.invalid,.corp.example,extra.invalid,127.0.0.1,localhost,::1"
    kept = proxy_env.no_proxy_value("127.0.0.1,LOCALHOST,::1,foo.invalid", None)
    assert kept == "127.0.0.1,LOCALHOST,::1,foo.invalid", kept   # no dup, no rewrite


def test_sw_env_passes_everything_through_and_adds_loopback() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = {
            "HTTP_PROXY": "http://proxy.invalid:8080",
            "HTTPS_PROXY": "http://proxy.invalid:8080",
            "no_proxy": "internal.invalid",
            "HOME": f"{tmp}/home",
            "STUDIOWRIGHT_HOME": f"{tmp}/sw",
            "MOZ35_MARKER": "passed-through",
        }
        env = proxy_env.sw_env(base)
    assert env["MOZ35_MARKER"] == "passed-through"
    assert env["HOME"] == base["HOME"] and env["STUDIOWRIGHT_HOME"] == base["STUDIOWRIGHT_HOME"]
    assert env["HTTP_PROXY"] == base["HTTP_PROXY"]
    assert env["HTTPS_PROXY"] == base["HTTPS_PROXY"]
    bypass = env["NO_PROXY"].split(",")
    assert set(proxy_env.LOOPBACK_NO_PROXY) <= set(bypass), env["NO_PROXY"]
    assert "internal.invalid" in bypass, env["NO_PROXY"]
    assert env["no_proxy"] == env["NO_PROXY"], "both spellings must agree"
    assert "NO_PROXY" not in base, "the caller's env must not be mutated"


def test_child_process_sees_the_passthrough_and_the_loopback_bypass() -> None:
    keys = ("MOZ35_MARKER", "HTTP_PROXY", "NO_PROXY", "no_proxy")
    script = ("import json, os\n"
              f"print(json.dumps({{key: os.environ.get(key) for key in {keys!r}}}))")
    child_env = proxy_env.sw_env({"MOZ35_MARKER": "passed-through",
                                  "HTTP_PROXY": "http://proxy.invalid:8080"})
    done = subprocess.run([sys.executable, "-c", script], env=child_env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["MOZ35_MARKER"] == "passed-through"
    assert seen["HTTP_PROXY"] == "http://proxy.invalid:8080"
    assert seen["NO_PROXY"] == seen["no_proxy"], seen
    assert set(proxy_env.LOOPBACK_NO_PROXY) <= set(seen["no_proxy"].split(",")), seen


def test_remote_chrome_cdp_url_is_never_rewritten() -> None:
    env = proxy_env.sw_env({chrome_cdp.ENV_CDP_URL: REMOTE_URL, "NO_PROXY": "foo.invalid"})
    assert env[chrome_cdp.ENV_CDP_URL] == REMOTE_URL, "a remote endpoint stays remote"
    assert chrome_cdp.ENV_CDP_URL not in proxy_env.sw_env({}), "never invent an endpoint"
    with patch.dict(os.environ, {chrome_cdp.ENV_CDP_URL: REMOTE_URL}):
        assert chrome_cdp.cdp_url() == REMOTE_URL
        assert proxy_env.sw_env()[chrome_cdp.ENV_CDP_URL] == REMOTE_URL
    with patch.dict(os.environ, {chrome_cdp.ENV_CDP_URL: ""}):
        assert chrome_cdp.cdp_url() == chrome_cdp.DEFAULT_CDP_URL == LOCAL_URL
        # An empty value stays empty: the local 9222 default is a preflight
        # default, never something injected into the child env.
        assert proxy_env.sw_env()[chrome_cdp.ENV_CDP_URL] == ""


def test_sw_port_is_computed_like_studiowright() -> None:
    assert (chrome_cdp.SW_PORT_BASE, chrome_cdp.SW_PORT_SPAN) == (9300, 90)
    assert chrome_cdp.sw_debug_port() == SW_PORT, 'chrome.py debug_port("default") is 9319'
    assert chrome_cdp.sw_debug_port("default") == SW_PORT
    assert chrome_cdp.sw_debug_port("music") == 9380, "port pinned against 0.2.13"
    for name in ("", "default", "av", "a much longer profile name"):
        assert 9300 <= chrome_cdp.sw_debug_port(name) < 9390, name


def test_sw_port_status_probes_that_port_only() -> None:
    probe = Probe(False)
    status = chrome_cdp.sw_port_status(probe=probe)
    assert status == {"profile": "default", "port": SW_PORT,
                      "url": f"http://127.0.0.1:{SW_PORT}", "reachable": False}, status
    assert probe.calls == [f"http://127.0.0.1:{SW_PORT}"], probe.calls
    assert chrome_cdp.sw_port_status("music", probe=Probe(True))["reachable"] is True


def test_port_failure_note_keeps_9222_and_9319_apart() -> None:
    cdp_ok = chrome_cdp.CdpStatus(ok=True, url=LOCAL_URL, local=True, probes=1)
    sw = {"profile": "default", "port": SW_PORT,
          "url": f"http://127.0.0.1:{SW_PORT}", "reachable": False}
    note = chrome_cdp.sw_port_note(cdp_ok, sw)
    assert LOCAL_URL in note and str(SW_PORT) in note, note
    assert "answered" in note and "did not answer" in note, note
    assert chrome_cdp.looks_like_sw_port_failure(UPSTREAM_PORT_ERROR)
    assert chrome_cdp.looks_like_sw_port_failure("ConnectionRefusedError(111)")
    assert not chrome_cdp.looks_like_sw_port_failure("flow_image returned no output files")


def test_tools_hand_sw_the_merged_env() -> None:
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"files": [f"{out_dir}/sample-{len(calls)}.bin"]}), "")

    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"MOZ35_MARKER": "passed-through"}), \
            patch("subprocess.run", fake_run):
        for module, tool in ((flow_image, flow_image.FlowImage()),
                             (flow_video, flow_video.FlowVideo()),
                             (flow_music, flow_music.FlowMusic())):
            calls.clear()
            out_dir = f"{tmp}/{tool.name}"
            with patch.object(module, "_sw_binary", lambda: "/fake/sw"):
                result = tool.execute({"prompt": "x", "output_path": out_dir})
            assert result.success, (tool.name, result.error)
            assert len(calls) == 1, (tool.name, calls)
            child_env = calls[0]["env"]
            assert child_env["MOZ35_MARKER"] == "passed-through", tool.name
            bypass = child_env["NO_PROXY"].split(",")
            assert set(proxy_env.LOOPBACK_NO_PROXY) <= set(bypass), (tool.name, bypass)
            assert child_env["no_proxy"] == child_env["NO_PROXY"], tool.name


def test_cli_names_sws_port_when_sw_cannot_reach_its_own_chrome() -> None:
    profiles: list[str | None] = []

    def fake_status(profile=None) -> dict:
        profiles.append(profile)
        return {"profile": profile or "default", "port": SW_PORT,
                "url": f"http://127.0.0.1:{SW_PORT}", "reachable": False}

    class FailingTool:
        name, provider = "flow_image", "google_flow"

        def build_command(self, inputs):
            return ["sw", *SW_ARGS]

        def execute(self, inputs):
            return SimpleNamespace(success=False, error=f"sw image exited 1: {UPSTREAM_PORT_ERROR}",
                                   data={}, artifacts=[], model="nano-banana-2.1")

    originals = (cli._tool, cli.ensure_cdp, cli.sw_port_status)
    cli._tool = lambda name: FailingTool()
    cli.ensure_cdp = lambda **kwargs: chrome_cdp.CdpStatus(
        ok=True, url=LOCAL_URL, local=True, probes=1)
    cli.sw_port_status = fake_status
    try:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["image", "--prompt", "x", "--profile", "stage"])
    finally:
        cli._tool, cli.ensure_cdp, cli.sw_port_status = originals

    report = json.loads(buffer.getvalue())
    assert code == 1 and report["status"] == "error", report
    assert report["cdp"]["ok"] is True, "9222 answered, so it is not the dead port"
    assert (report["sw_cdp"]["port"], report["sw_cdp"]["profile"]) == (SW_PORT, "stage"), report
    assert report["sw_cdp"]["reachable"] is False
    assert str(SW_PORT) in report["error"], report["error"]
    assert LOCAL_URL in report["error"], report["error"]
    assert profiles == ["stage"], profiles


def test_help_plan_and_dry_run_spawn_nothing() -> None:
    def bomb(*args, **kwargs):
        raise AssertionError(f"--help/--plan/--dry-run must spawn nothing: {args}")

    original = cli.ensure_cdp
    cli.ensure_cdp = bomb
    try:
        with patch("subprocess.run", bomb), contextlib.redirect_stdout(io.StringIO()):
            try:
                cli.main(["--help"])
            except SystemExit as requested:
                assert requested.code == 0, requested
            statuses = []
            for argv in (["image", "--prompt", "x", "--plan"],
                         ["video", "--prompt", "x", "--plan"],
                         ["music", "--prompt", "x", "--plan"],
                         ["image", "--prompt", "x", "--dry-run"],
                         ["music", "--prompt", "x", "--dry-run"]):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = cli.main(argv)
                report = json.loads(buffer.getvalue())
                assert code == 0, (argv, report)
                assert report["status"] in ("plan", "dry_run"), (argv, report)
                statuses.append(report["status"])
    finally:
        cli.ensure_cdp = original
    assert statuses == ["plan", "plan", "plan", "dry_run", "dry_run"], statuses


if __name__ == "__main__":
    checks = (
        test_no_proxy_merge_keeps_existing_hosts,
        test_sw_env_passes_everything_through_and_adds_loopback,
        test_child_process_sees_the_passthrough_and_the_loopback_bypass,
        test_remote_chrome_cdp_url_is_never_rewritten,
        test_sw_port_is_computed_like_studiowright,
        test_sw_port_status_probes_that_port_only,
        test_port_failure_note_keeps_9222_and_9319_apart,
        test_tools_hand_sw_the_merged_env,
        test_cli_names_sws_port_when_sw_cannot_reach_its_own_chrome,
        test_help_plan_and_dry_run_spawn_nothing,
    )
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"{len(checks)} sw env checks passed")
