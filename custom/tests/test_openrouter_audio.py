"""Offline checks for the OpenRouter speech tool and the client's raw POST.

Plain asserts, no pytest, and no network: the two responses a live run needs
(catalogue + audio bytes) are replayed through a fake opener. Run from the
repository root:

    python3 -m custom.tests.test_openrouter_audio

Conversion tests need a working ffmpeg (the tool probes for one); they are
skipped with a printed note when none runs.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import math
import struct
import tempfile
import wave
from argparse import Namespace
from pathlib import Path

from custom.tools.audio import openrouter_audio as oa
from custom.tools.common.openrouter_client import (
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
)

KEY = "sk-test-not-a-real-key"


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
    kwargs.setdefault("api_key", KEY)
    kwargs.setdefault("sleep", lambda _s: None)
    client = OpenRouterClient(opener=opener, **kwargs)
    return client, opener


def _use_fake_client(results):
    """Point main() at a fake opener so a live run can be exercised offline."""
    opener = FakeOpener(*results)

    def factory(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("max_attempts", None)
        return OpenRouterClient(opener=opener, api_key=KEY, sleep=lambda _s: None, **kwargs)

    oa.OpenRouterClient = factory  # type: ignore[assignment]
    return opener


def _restore(original):
    oa.OpenRouterClient = original


def _catalogue(*voices: str, model: str = oa.DEFAULT_MODEL) -> bytes:
    return json.dumps(
        {"data": [{"id": model, "supported_voices": list(voices)}]}
    ).encode()


def _pcm_sine(seconds: float = 0.25, rate: int = 24000) -> bytes:
    frames = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(frames)
    )


def _ffmpeg_or_skip(name: str) -> str | None:
    ffmpeg = oa.find_ffmpeg()
    if ffmpeg is None:
        print(f"skip {name}: no working ffmpeg")
    return ffmpeg


# -- client: raw POST ------------------------------------------------------


def test_post_raw_returns_bytes_and_authorizes():
    audio = b"ID3\x03\x00\x00\x00" + b"\x00" * 32
    client, opener = _client((200, audio))
    data, meta = client.post_raw("audio/speech", {"model": "m", "input": "hi"})
    assert data == audio, data[:16]
    assert (meta["status"], meta["mode"], meta["attempt"]) == (200, "live", 1)
    sent = opener.requests[0]
    assert sent.get_header("Authorization") == f"Bearer {KEY}"
    assert sent.get_header("Content-type") == "application/json"
    assert json.loads(sent.data)["input"] == "hi"


def test_post_raw_quotes_the_error_body_without_the_key():
    client, _ = _client((400, b'{"error":{"code":400,"message":"voice not supported"}}'))
    try:
        client.post_raw("audio/speech", {"model": "m", "input": "hi"})
    except OpenRouterError as exc:
        assert exc.status == 400 and not exc.retryable, exc
        assert "voice not supported" in str(exc), exc
        assert KEY not in str(exc), exc
    else:
        raise AssertionError("a 400 must raise")


def test_post_raw_retries_429_then_succeeds():
    delays = []
    opener = FakeOpener((429, b'{"error":"slow down"}'), (200, b"\xff\xfb\x90\x00"))
    client = OpenRouterClient(opener=opener, api_key=KEY, sleep=delays.append)
    data, meta = client.post_raw("audio/speech", {"model": "m", "input": "hi"})
    assert data.startswith(b"\xff\xfb") and meta["attempt"] == 2
    assert delays == [1], delays


def test_post_raw_is_size_capped():
    client, _ = _client((200, b"x" * 10))
    try:
        client.post_raw("audio/speech", {"model": "m", "input": "hi"}, max_bytes=4)
    except OpenRouterError as exc:
        assert "exceeds the 4 byte limit" in str(exc), exc
    else:
        raise AssertionError("an oversized body must be rejected")


def test_dropped_connection_is_a_status_not_a_traceback():
    opener = FakeOpener(*[http.client.RemoteDisconnected("server closed")] * 2)
    client = OpenRouterClient(opener=opener, api_key=KEY, max_attempts=2, sleep=lambda _s: None)
    try:
        client.post_raw("audio/speech", {"model": "m", "input": "hi"})
    except OpenRouterError as exc:
        assert exc.retryable and "connection failed" in str(exc), exc
        assert isinstance(exc, OpenRouterError), type(exc)
    else:
        raise AssertionError("a dropped connection must raise OpenRouterError")
    assert len(opener.requests) == 2


def test_post_raw_sends_nothing_without_a_key():
    client, opener = _client(api_key=None)
    client.api_key = None  # "no key" must not depend on the ambient environment
    try:
        client.post_raw("audio/speech", {"model": "m", "input": "hi"})
    except MissingApiKey as exc:
        assert "OPENROUTER_API_KEY" in str(exc), exc
    else:
        raise AssertionError("a keyless POST must raise MissingApiKey")
    assert opener.requests == []


# -- tool: request shape --------------------------------------------------


def test_payload_only_carries_what_was_asked_for():
    bare = oa.build_payload(
        Namespace(model="m", text="hi", response_format="mp3", voice=None, speed=None)
    )
    assert bare == {"model": "m", "input": "hi", "response_format": "mp3"}, bare
    full = oa.build_payload(
        Namespace(model="m", text="hi", response_format="pcm", voice="Kore", speed=1.25)
    )
    assert full["voice"] == "Kore" and full["speed"] == 1.25, full


def test_output_must_be_wav_or_mp3():
    for name in ("x.ogg", "x.wav.tmp", "x"):
        try:
            oa._output_spec(Path(name))
        except ValueError as exc:
            assert ".wav or .mp3" in str(exc), exc
            continue
        raise AssertionError(f"accepted an unsupported output name: {name}")
    assert oa._output_spec(Path("x.WAV"))[0] == "wav"


def test_json_and_empty_bodies_are_not_audio():
    assert oa._body_problem(b"") is not None
    assert "got JSON" in (oa._body_problem(b'  {"error": "boom"}') or "")
    assert oa._body_problem(b"ID3\x03") is None


# -- tool: argument and error paths ---------------------------------------


def test_pcm_without_a_sample_rate_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = oa.main(
                ["--text", "hi", "--response-format", "pcm", "--output", str(out)]
            )
        assert code == 2, code
        assert "--pcm-sample-rate" in stderr.getvalue(), stderr.getvalue()
        assert not out.exists()


def test_empty_text_and_bad_speed_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            assert oa.main(["--text", "   ", "--output", str(out)]) == 2
            assert oa.main(["--text", "hi", "--speed", "0", "--output", str(out)]) == 2
        assert not out.exists()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                oa.main(["--output", str(out)])
        except SystemExit as exc:  # --text/--prompt is a required group
            assert exc.code == 2, exc.code
        else:
            raise AssertionError("a run with no text must not start")


def test_dry_run_touches_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dry.wav"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = oa.main(
                ["--text", "hello", "--voice", "en_paul_neutral", "--output", str(out), "--dry-run"]
            )
        assert code == 0, code
        assert not out.exists()
        text = buffer.getvalue()
        assert "mode: dry-run" in text and "audio/speech" in text, text
        assert oa.DEFAULT_MODEL in text and "en_paul_neutral" in text, text


def test_mock_json_body_fails_and_leaves_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "response.json"
        mock.write_text('{"error": {"message": "no audio"}}', encoding="utf-8")
        out = Path(tmp) / "out.wav"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = oa.main(["--text", "hi", "--mock-response", str(mock), "--output", str(out)])
        assert code == 1, code
        assert "got JSON" in stderr.getvalue(), stderr.getvalue()
        assert not out.exists()


def test_mock_missing_file_is_an_error():
    with tempfile.TemporaryDirectory() as tmp:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = oa.main(
                ["--text", "hi", "--mock-response", str(Path(tmp) / "absent.mp3"),
                 "--output", str(Path(tmp) / "x.wav")]
            )
        assert code == 2, code
        assert "cannot read mock response" in stderr.getvalue()


# -- tool: conversion ----------------------------------------------------


def test_mock_pcm_is_converted_to_a_real_wav():
    if _ffmpeg_or_skip("test_mock_pcm_is_converted_to_a_real_wav") is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "speech.pcm"
        mock.write_bytes(_pcm_sine(0.25, 24000))
        out = Path(tmp) / "nested" / "line.wav"
        code = oa.main(
            ["--text", "hi", "--response-format", "pcm", "--pcm-sample-rate", "24000",
             "--mock-response", str(mock), "--output", str(out)]
        )
        assert code == 0, code
        assert out.read_bytes().startswith(b"RIFF")
        with wave.open(str(out)) as handle:
            assert (handle.getnchannels(), handle.getframerate()) == (1, 24000)
            assert abs(handle.getnframes() - 6000) <= 240, handle.getnframes()
        assert [p.name for p in out.parent.iterdir()] == ["line.wav"], "temp file was left"


def test_mock_pcm_to_mp3_then_remux():
    if _ffmpeg_or_skip("test_mock_pcm_to_mp3_then_remux") is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "speech.pcm"
        mock.write_bytes(_pcm_sine(0.2, 24000))
        first = Path(tmp) / "encode.mp3"
        assert oa.main(
            ["--text", "hi", "--response-format", "pcm", "--pcm-sample-rate", "24000",
             "--mock-response", str(mock), "--output", str(first)]
        ) == 0
        assert first.stat().st_size > 0
        # mp3 in, mp3 out: remuxed, not silently re-encoded into a wav.
        second = Path(tmp) / "remux.mp3"
        assert oa.main(
            ["--text", "hi", "--mock-response", str(first), "--output", str(second)]
        ) == 0
        head = second.read_bytes()[:4]
        assert head[:3] == b"ID3" or head[0] == 0xFF, head


def test_garbage_audio_fails_without_writing():
    if _ffmpeg_or_skip("test_garbage_audio_fails_without_writing") is None:
        return
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "junk.mp3"
        mock.write_bytes(b"this is not an mp3 bytestream" * 8)
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = oa.main(
                ["--text", "hi", "--mock-response", str(mock), "--output", str(out)]
            )
        assert code == 1, code
        assert "ffmpeg" in stderr.getvalue(), stderr.getvalue()
        assert not out.exists()


def test_empty_body_fails_before_ffmpeg():
    with tempfile.TemporaryDirectory() as tmp:
        mock = Path(tmp) / "empty.mp3"
        mock.write_bytes(b"")
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = oa.main(
                ["--text", "hi", "--mock-response", str(mock), "--output", str(out)]
            )
        assert code == 1, code
        assert "empty" in stderr.getvalue(), stderr.getvalue()
        assert not out.exists()


# -- tool: live path, with the network faked ------------------------------


def test_live_run_checks_the_catalogue_then_writes_audio():
    if _ffmpeg_or_skip("test_live_run_checks_the_catalogue_then_writes_audio") is None:
        return
    original = oa.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "live.wav"
        opener = _use_fake_client(
            [(200, _catalogue("en_paul_neutral")), (200, _pcm_sine(0.2, 24000))]
        )
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                code = oa.main(
                    ["--text", "hi", "--voice", "en_paul_neutral", "--response-format", "pcm",
                     "--pcm-sample-rate", "24000", "--output", str(out)]
                )
        finally:
            _restore(original)
        text = buffer.getvalue()
        assert code == 0, text
        assert out.read_bytes().startswith(b"RIFF")
        assert "mode: live" in text and "not reported by the server" in text, text
        assert "GET" not in text, text
        assert len(opener.requests) == 2, len(opener.requests)


def test_unknown_voice_and_model_never_reach_the_speech_endpoint():
    original = oa.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        try:
            opener = _use_fake_client([(200, _catalogue("en_paul_neutral"))])
            with contextlib.redirect_stderr(stderr):
                bad_voice = oa.main(["--text", "hi", "--voice", "nope", "--output", str(out)])
            assert bad_voice == 2, bad_voice
            assert "does not list voice" in stderr.getvalue(), stderr.getvalue()
            assert len(opener.requests) == 1, "a rejected voice must not trigger a POST"

            stderr = io.StringIO()
            opener = _use_fake_client([(200, _catalogue("en_paul_neutral", model="other/tts"))])
            with contextlib.redirect_stderr(stderr):
                bad_model = oa.main(["--text", "hi", "--output", str(out)])
            assert bad_model == 2, bad_model
            assert "is not in GET" in stderr.getvalue(), stderr.getvalue()
            assert len(opener.requests) == 1, "an unknown model must not trigger a POST"
        finally:
            _restore(original)
        assert not out.exists()


def test_catalogue_failure_warns_and_still_posts():
    if _ffmpeg_or_skip("test_catalogue_failure_warns_and_still_posts") is None:
        return
    original = oa.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.wav"
        stderr = io.StringIO()
        try:
            _use_fake_client(
                [(503, b'{"error":"upstream"}')] * 3 + [(200, _pcm_sine(0.1, 24000))]
            )
            with contextlib.redirect_stderr(stderr):
                code = oa.main(
                    ["--text", "hi", "--response-format", "pcm", "--pcm-sample-rate", "24000",
                     "--output", str(out), "--max-attempts", "3"]
                )
        finally:
            _restore(original)
        assert code == 0, code
        assert "warning: cannot read" in stderr.getvalue(), stderr.getvalue()
        assert out.read_bytes().startswith(b"RIFF")


def test_help_lists_the_documented_flags():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            oa.main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0, exc.code
    text = buffer.getvalue()
    for flag in ("--text", "--prompt", "--model", "--voice", "--speed", "--output",
                 "--response-format", "--pcm-sample-rate"):
        assert flag in text, f"{flag} missing from --help"


def test_no_working_ffmpeg_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        stderr = io.StringIO()
        original = oa.find_ffmpeg
        oa.find_ffmpeg = lambda explicit=None: None  # type: ignore[assignment]
        try:
            with contextlib.redirect_stderr(stderr):
                code = oa.main(["--text", "hi", "--output", str(Path(tmp) / "x.wav")])
        finally:
            oa.find_ffmpeg = original  # type: ignore[assignment]
        assert code == 2, code
        assert "no working ffmpeg" in stderr.getvalue(), stderr.getvalue()


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for name, test in tests:
        test()
        print(f"ok {name}")
    print(f"{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
