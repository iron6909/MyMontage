"""Offline checks for the ~/.harness/providers.env fallback.

Plain asserts, no pytest, no network. Run from the repository root:

    python3 -m custom.tests.test_providers_env

Every check runs inside a throwaway HOME with obviously fake values, so the real
$HOME/.harness/providers.env is never opened and no real credential can exist in
this process. Nothing here prints a value it loaded.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from custom.tools.common import providers_env
from custom.tools.common.openrouter_client import (
    DEFAULT_BASE_URL,
    ENV_API_KEY,
    ENV_BASE_URL,
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
    resolve_api_key,
    resolve_base_url,
)

FAKE_KEY = "sk-or-v1-FAKE-moz30-not-a-real-key-0000"
FAKE_BASE = "https://fake-proxy.invalid/api/v1"
ENV_ONLY_BASE = "https://from-the-environment.invalid/v1"


class Sandbox:
    """A temporary HOME with both settings cleared out of the environment."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.home = Path(self._dir.name)
        self._saved = {name: os.environ.get(name) for name in (ENV_API_KEY, ENV_BASE_URL, "HOME")}
        self._saved_cwd = os.getcwd()

    def __enter__(self) -> Sandbox:
        os.environ["HOME"] = str(self.home)
        for name in (ENV_API_KEY, ENV_BASE_URL):
            os.environ.pop(name, None)
        return self

    def __exit__(self, *_exc) -> bool:
        os.chdir(self._saved_cwd)
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._dir.cleanup()
        return False

    def write(self, text: str) -> Path:
        target = self.home / providers_env.HARNESS_DIR / providers_env.PROVIDERS_ENV_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target


class StubOpener:
    """Stands in for urllib: replays queued results and records every request."""

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.requests = []

    def __call__(self, request, timeout, limit=None):
        self.requests.append(request)
        result = self.results[min(len(self.requests), len(self.results)) - 1]
        if isinstance(result, BaseException):
            raise result
        return result


def test_path_is_under_the_current_home():
    with Sandbox() as box:
        expected = box.home / ".harness" / "providers.env"
        assert providers_env.providers_env_path() == expected, providers_env.providers_env_path()


def test_environment_wins_over_the_file():
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}={FAKE_KEY}\n{ENV_BASE_URL}={FAKE_BASE}\n")
        os.environ[ENV_API_KEY] = "sk-or-v1-from-the-environment"
        os.environ[ENV_BASE_URL] = ENV_ONLY_BASE
        assert resolve_api_key() == "sk-or-v1-from-the-environment"
        assert resolve_base_url() == ENV_ONLY_BASE
        # an explicit argument still outranks both sources
        assert resolve_api_key("sk-or-v1-explicit") == "sk-or-v1-explicit"
        assert resolve_base_url("https://explicit.invalid/v1") == "https://explicit.invalid/v1"


def test_file_fills_only_the_missing_environment():
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}={FAKE_KEY}\n{ENV_BASE_URL}={FAKE_BASE}\n")
        assert resolve_api_key() == FAKE_KEY
        assert resolve_base_url() == FAKE_BASE
        client = OpenRouterClient()
        assert client.api_key == FAKE_KEY
        assert client.base_url == FAKE_BASE
        assert "key=set" in repr(client)


def test_file_is_read_from_any_cwd():
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}={FAKE_KEY}\n")
        # an unrelated tree stands in for a second worktree
        other = box.home / "some-other-worktree" / "deep" / "nested"
        other.mkdir(parents=True)
        os.chdir(other)
        assert resolve_api_key() == FAKE_KEY
        assert resolve_base_url() == DEFAULT_BASE_URL


def test_loaded_key_authorizes_the_request():
    opener = StubOpener((200, b'{"data": []}'))
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}={FAKE_KEY}\n")
        OpenRouterClient(opener=opener).get_json("images/models")
    assert len(opener.requests) == 1
    assert opener.requests[0].get_header("Authorization") == f"Bearer {FAKE_KEY}"


def test_missing_file_keeps_the_current_behaviour():
    with Sandbox():
        assert resolve_api_key() is None
        assert resolve_base_url() == DEFAULT_BASE_URL
        opener = StubOpener((200, b"{}"))
        try:
            OpenRouterClient(opener=opener).post_json("images", {"model": "m", "prompt": "p"})
        except MissingApiKey as exc:
            assert ENV_API_KEY in str(exc), exc
            assert "providers.env" in str(exc), exc
            assert FAKE_KEY not in str(exc), exc
        else:
            raise AssertionError("a key-less live call must raise MissingApiKey")
        assert opener.requests == [], "nothing may be sent without a key"


def test_file_without_the_key_does_not_invent_one():
    with Sandbox() as box:
        box.write(f"{ENV_BASE_URL}={FAKE_BASE}\n")
        assert resolve_api_key() is None
        assert resolve_base_url() == FAKE_BASE


def test_unreadable_path_is_not_fatal():
    with Sandbox() as box:
        # a directory where the file belongs: read_text raises, we degrade
        (box.home / ".harness" / "providers.env").mkdir(parents=True)
        assert providers_env.load_local_env((ENV_API_KEY,)) == {}
        assert resolve_api_key() is None
        assert resolve_base_url() == DEFAULT_BASE_URL


def test_malformed_lines_are_skipped_by_number_only():
    text = "\n".join(
        [
            "this line has no equals sign",  # 1
            f"{ENV_API_KEY}={FAKE_KEY}",  # 2
            f"export {ENV_BASE_URL}='{FAKE_BASE}'",  # 3
            "  # a comment",  # 4
            "",  # 5
            "9INVALID=value",  # 6 — not a legal key
            f"{ENV_API_KEY}=",  # 7 — an empty value is not a setting
        ]
    )
    values, skipped = providers_env.parse_providers_env(text, (ENV_API_KEY, ENV_BASE_URL))
    assert values == {ENV_API_KEY: FAKE_KEY, ENV_BASE_URL: FAKE_BASE}, values
    assert skipped == [1, 6, 7], skipped
    assert FAKE_KEY not in repr(skipped) and FAKE_BASE not in repr(skipped)


def test_no_shell_expansion_or_injection():
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}=$(whoami)`id`{FAKE_KEY}\n")
        # the value is taken literally: nothing was executed or expanded
        assert resolve_api_key() == f"$(whoami)`id`{FAKE_KEY}"


def test_other_keys_never_leave_the_file():
    with Sandbox() as box:
        box.write(f"SOME_UNRELATED_TOKEN=should-not-be-returned\n{ENV_API_KEY}={FAKE_KEY}\n")
        assert providers_env.load_local_env((ENV_API_KEY,)) == {ENV_API_KEY: FAKE_KEY}
        assert "SOME_UNRELATED_TOKEN" not in os.environ
        # read-only: loading never mutates the process environment
        assert os.environ.get(ENV_API_KEY) is None


def test_loaded_key_never_appears_in_reports_or_errors():
    with Sandbox() as box:
        box.write(f"{ENV_API_KEY}={FAKE_KEY}\n")
        opener = StubOpener((401, f"Bearer {FAKE_KEY} rejected".encode()))
        client = OpenRouterClient(opener=opener)
        assert FAKE_KEY not in repr(client), repr(client)
        try:
            client.get_json("images/models")
        except OpenRouterError as exc:
            assert FAKE_KEY not in str(exc), exc
            assert "HTTP 401" in str(exc), exc
        else:
            raise AssertionError("a 401 must raise")


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for name, test in tests:
        test()
        print(f"ok {name}")
    print(f"{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
