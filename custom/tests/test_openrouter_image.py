"""Offline checks for the OpenRouter client and image tool.

Plain asserts, no pytest, no network. Run from the repository root:

    python3 -m custom.tests.test_openrouter_image

Covers the parts that fail silently: response decoding, untrusted media URLs,
atomic writes, and the retry/status/redaction behaviour of the client.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import tempfile
import urllib.error
from pathlib import Path

from custom.tools.common.openrouter_client import (
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
    check_media_url,
    format_report,
    usage_report,
)
from custom.tools.image import openrouter_image as oi

# A real 1x1 PNG: genuine bytes, not a placeholder, so media_type sniffing and
# size reporting are exercised on something a renderer would accept.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/wFq2MpPAAAAAElFTkSuQmCC"
)


class FakeOpener:
    """Stands in for urllib: replays queued results and records the requests."""

    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def __call__(self, request, timeout, limit=None):
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _client(*results, **kwargs):
    opener = FakeOpener(*results)
    kwargs.setdefault("api_key", "sk-test-not-a-real-key")
    kwargs.setdefault("sleep", lambda _s: None)
    return OpenRouterClient(opener=opener, **kwargs), opener


def _response(b64: str | None = None, **extra) -> bytes:
    entry = {"media_type": "image/png"}
    if b64 is not None:
        entry["b64_json"] = b64
    entry.update(extra)
    return json.dumps({"created": 1, "data": [entry], "usage": {"cost": 0.04}}).encode()


def test_decode_bare_b64():
    client, _ = _client()
    assert oi._entry_bytes({"b64_json": base64.b64encode(PNG_1X1).decode()}, client) == PNG_1X1


def test_decode_data_url():
    client, _ = _client()
    data_url = "data:image/png;base64," + base64.b64encode(PNG_1X1).decode()
    assert oi._entry_bytes({"b64_json": data_url}, client) == PNG_1X1
    try:
        oi._entry_bytes({"b64_json": "data:image/png,notbase64"}, client)
    except OpenRouterError as exc:
        assert "not base64" in str(exc), exc
    else:
        raise AssertionError("a non-base64 data URL must be rejected")


def test_decode_remote_url_without_credentials():
    client, opener = _client((200, PNG_1X1))
    assert oi._entry_bytes({"url": "https://cdn.example.com/a.png"}, client) == PNG_1X1
    assert opener.requests[-1].get_header("Authorization") is None
    assert opener.requests[-1].full_url == "https://cdn.example.com/a.png"


def test_entry_without_media_is_an_error():
    client, _ = _client()
    try:
        oi._entry_bytes({"media_type": "image/png"}, client)
    except OpenRouterError as exc:
        assert "neither b64_json nor url" in str(exc), exc
    else:
        raise AssertionError("an entry with no image payload must be rejected")


def test_untrusted_media_urls_are_refused():
    bad = [
        "http://cdn.example.com/a.png",  # plaintext
        "file:///etc/passwd",
        "https://user:pw@cdn.example.com/a.png",  # credentials in the URL
        "https://localhost/a.png",
        "https://127.0.0.1/a.png",
        "https://10.0.0.5/a.png",
        "https://169.254.169.254/latest/meta-data",  # link-local / cloud metadata
        "https://[::1]/a.png",
        "https:///a.png",  # no host at all
    ]
    for url in bad:
        try:
            check_media_url(url)
        except OpenRouterError:
            continue
        raise AssertionError(f"accepted an untrusted media URL: {url}")
    assert check_media_url("https://cdn.example.com/a.png") == "cdn.example.com"


def test_media_download_is_size_capped():
    client, _ = _client((200, b"x" * 10))
    try:
        client.download_media("https://cdn.example.com/a.png", max_bytes=4)
    except OpenRouterError as exc:
        assert "exceeds the 4 byte limit" in str(exc), exc
    else:
        raise AssertionError("an oversized download must be rejected")


def test_atomic_write_replaces_and_leaves_no_temp():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "nested" / "image.png"
        oi._atomic_write(target, b"first")
        oi._atomic_write(target, PNG_1X1)
        assert target.read_bytes() == PNG_1X1
        assert [p.name for p in target.parent.iterdir()] == ["image.png"]


def test_retry_then_success_on_429():
    delays = []
    client, opener = _client((429, b'{"error":"slow down"}'), (200, _response("aGk=")), sleep=delays.append)
    payload, meta = client.post_json("images", {"model": "m", "prompt": "p"})
    assert payload["usage"]["cost"] == 0.04
    assert (meta["status"], meta["attempt"], meta["mode"]) == (200, 2, "live")
    assert delays == [1], delays
    assert opener.requests[0].get_header("Authorization").startswith("Bearer ")


def test_5xx_is_bounded_and_reported():
    client, opener = _client(*[(503, b'{"error":"upstream"}')] * 3, max_attempts=3)
    try:
        client.post_json("images", {"model": "m", "prompt": "p"})
    except OpenRouterError as exc:
        assert exc.status == 503 and exc.retryable, exc
        assert "HTTP 503" in str(exc), exc
    else:
        raise AssertionError("an exhausted 5xx must raise")
    assert len(opener.requests) == 3


def test_4xx_other_than_429_is_not_retried():
    client, opener = _client((400, b'{"error":"bad aspect_ratio"}'))
    try:
        client.post_json("images", {"model": "m", "prompt": "p"})
    except OpenRouterError as exc:
        assert exc.status == 400 and not exc.retryable, exc
        assert "bad aspect_ratio" in str(exc), exc
    else:
        raise AssertionError("a 400 must raise")
    assert len(opener.requests) == 1


def test_invalid_json_reports_status():
    client, _ = _client((200, b"<html>proxy error</html>"))
    try:
        client.get_json("images/models", auth=False)
    except OpenRouterError as exc:
        assert exc.status == 200 and "invalid JSON" in str(exc), exc
    else:
        raise AssertionError("invalid JSON must raise")


def test_timeout_is_reported_and_bounded():
    client, opener = _client(TimeoutError(), TimeoutError(), max_attempts=2)
    try:
        client.get_json("images/models", auth=False)
    except OpenRouterError as exc:
        assert exc.retryable and "timed out" in str(exc), exc
    else:
        raise AssertionError("a timeout must raise")
    assert len(opener.requests) == 2

    client, _ = _client(urllib.error.URLError(TimeoutError()), max_attempts=1)
    try:
        client.get_json("images/models", auth=False, timeout=5)
    except OpenRouterError as exc:
        assert "timed out after 5s" in str(exc), exc
    else:
        raise AssertionError("a urlopen-wrapped timeout must read as a timeout")


def test_missing_key_sends_nothing():
    client, opener = _client(api_key="")
    client.api_key = None
    try:
        client.post_json("images", {"model": "m", "prompt": "p"})
    except MissingApiKey as exc:
        assert "OPENROUTER_API_KEY" in str(exc), exc
    else:
        raise AssertionError("a keyless POST must raise MissingApiKey")
    assert opener.requests == []


def test_usage_is_reported_only_when_the_server_sent_it():
    assert usage_report({"data": []}) == {}
    assert usage_report({"usage": {"cost": 0.0, "total_tokens": 3}}) == {"cost": 0.0, "total_tokens": 3}
    bare = format_report({"method": "POST", "path": "images", "status": 200, "elapsed_s": 1.5}, {})
    assert "1.50s" in bare and "not reported" in bare, bare
    priced = format_report(
        {"method": "POST", "path": "images", "status": 200, "elapsed_s": 2.0},
        usage_report({"usage": {"total_tokens": 4175, "cost": 0.04}}),
    )
    assert "total_tokens=4175" in priced and "cost=$0.0400" in priced, priced


def test_error_bodies_never_leak_the_key():
    key = "sk-test-not-a-real-key"
    client, _ = _client((401, b'Authorization: Bearer sk-test-not-a-real-key rejected'))
    assert key not in repr(client)
    try:
        client.get_json("images/models")
    except OpenRouterError as exc:
        assert key not in str(exc), exc
        assert "HTTP 401" in str(exc), exc
    else:
        raise AssertionError("a 401 must raise")


def test_cli_dry_run_touches_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "dry.png"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = oi.main(
                ["--prompt", "a red fox", "--aspect-ratio", "16:9", "--output", str(target), "--dry-run"]
            )
        assert code == 0
        assert not target.exists()
        out = buffer.getvalue()
        assert "mode: dry-run" in out and "/images" in out and "openai/gpt-image-2" in out, out


def test_cli_mock_response_writes_the_image():
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "response.json"
        mock.write_bytes(_response(base64.b64encode(PNG_1X1).decode()))
        target = Path(tmp) / "out" / "mock.png"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = oi.main(["--prompt", "a red fox", "--output", str(target), "--mock-response", str(mock)])
        out = buffer.getvalue()
        assert code == 0, out
        assert target.read_bytes() == PNG_1X1
        assert "mode: mock" in out and "cost=$0.0400" in out and "1x1" not in out, out
        assert "image/png" in out and "total:" in out, out


def test_cli_reports_bad_and_missing_input():
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "x.png"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            empty = oi.main(["--prompt", "p", "--output", str(target), "--mock-response",
                             str(Path(tmp) / "absent.json")])
            assert empty == 2, empty
            assert "cannot read mock response" in stderr.getvalue()

            nothing = Path(tmp) / "no-image.json"
            nothing.write_text(json.dumps({"data": []}), encoding="utf-8")
            assert oi.main(["--prompt", "p", "--output", str(target),
                            "--mock-response", str(nothing)]) == 1
            assert not target.exists()


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for name, test in tests:
        test()
        print(f"ok {name}")
    print(f"{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
