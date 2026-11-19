"""Offline checks for the OpenRouter video tool and the client's raw GET.

Plain asserts, no pytest, and no network: the responses a live run needs
(catalogue, submit, poll, content) are replayed through a fake opener, and
mock-dir runs use files on disk. Nothing here submits a paid generation job.

    python3 -m custom.tests.test_openrouter_video

Checks that need a real mp4 to ffprobe are skipped with a printed note when no
working ffmpeg/ffprobe runs on this machine.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from argparse import Namespace
from pathlib import Path

from custom.tools.common.openrouter_client import (
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
)
from custom.tools.video import openrouter_video as ov

KEY = "sk-test-not-a-real-key"
JOB = "abc123"


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

    @property
    def paths(self) -> list[str]:
        return [request.full_url.split("/api/v1/", 1)[-1] for request in self.requests]


def _client(*results, **kwargs):
    opener = FakeOpener(*results)
    kwargs.setdefault("api_key", KEY)
    kwargs.setdefault("sleep", lambda _s: None)
    return OpenRouterClient(opener=opener, **kwargs), opener


def _use_fake_client(results, api_key: str | None = KEY):
    """Point main() at a fake opener so a live run can be exercised offline."""
    opener = FakeOpener(*results)

    def factory(**kwargs):
        kwargs.pop("timeout", None)
        kwargs.pop("max_attempts", None)
        return OpenRouterClient(
            opener=opener, api_key=api_key, sleep=lambda _s: None, **kwargs
        )

    ov.OpenRouterClient = factory  # type: ignore[assignment]
    return opener


def _ffmpeg() -> str | None:
    for candidate in (os.environ.get("FFMPEG"), "/usr/bin/ffmpeg", shutil.which("ffmpeg")):
        if not candidate:
            continue
        try:
            done = subprocess.run([candidate, "-version"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return candidate
    return None


def _mp4_bytes() -> bytes | None:
    """A real 0.4 s clip, so ffprobe has something honest to inspect."""
    ffmpeg = _ffmpeg()
    if ffmpeg is None or ov.find_ffprobe() is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "clip.mp4"
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "0.4",
             "-pix_fmt", "yuv420p", str(target)],
            capture_output=True,
            timeout=120,
        )
        if done.returncode != 0 or not target.is_file():
            return None
        return target.read_bytes()


def _catalogue(model: str = ov.DEFAULT_MODEL, **overrides) -> bytes:
    entry = {
        "id": model,
        "supported_durations": [4, 6, 8],
        "supported_aspect_ratios": ["16:9", "9:16"],
        "supported_frame_images": ["first_frame", "last_frame"],
    }
    entry.update(overrides)
    return json.dumps({"data": [entry]}).encode()


def _poll(status: str, **extra) -> bytes:
    return json.dumps({"id": JOB, "status": status, **extra}).encode()


def _run(argv: list[str], expect: int) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ov.main(argv)
    assert code == expect, f"exit {code} != {expect}\nstdout:\n{out.getvalue()}\nstderr:\n{err}"
    return out.getvalue(), err.getvalue()


def _build(model="m", prompt="hi", duration=None, aspect_ratio=None, first_frame=None):
    return Namespace(
        model=model, prompt=prompt, duration=duration,
        aspect_ratio=aspect_ratio, first_frame=first_frame,
    )


# -- client: raw GET -------------------------------------------------------


def test_get_raw_returns_bytes_and_authorizes():
    payload = b"\x00\x00\x00\x18ftypmp42"
    client, opener = _client((200, payload))
    data, meta = client.get_raw("videos/abc/content?index=0", max_bytes=1024)
    assert data == payload, data[:16]
    assert (meta["status"], meta["mode"], meta["attempt"]) == (200, "live", 1)
    sent = opener.requests[0]
    assert sent.get_method() == "GET" and sent.data is None
    assert sent.get_header("Authorization") == f"Bearer {KEY}"
    assert sent.get_header("Accept") == "application/octet-stream"


def test_get_raw_sends_nothing_without_a_key():
    client, opener = _client(api_key=None)
    try:
        client.get_raw("videos/abc/content")
    except MissingApiKey as exc:
        assert "OPENROUTER_API_KEY" in str(exc), exc
    else:
        raise AssertionError("a keyless GET must raise MissingApiKey")
    assert opener.requests == []


def test_get_raw_is_size_capped_and_keeps_the_error_body_readable():
    client, _ = _client((200, b"x" * 10))
    try:
        client.get_raw("videos/abc/content", max_bytes=4)
    except OpenRouterError as exc:
        assert "exceeds the 4 byte limit" in str(exc), exc
    else:
        raise AssertionError("an oversized body must be rejected")
    client, _ = _client((404, b'{"error":{"message":"job not found"}}'))
    try:
        client.get_raw("videos/abc/content")
    except OpenRouterError as exc:
        assert exc.status == 404 and "job not found" in str(exc), exc
        assert not exc.retryable, exc
    else:
        raise AssertionError("a 404 must raise")


# -- tool: request shape ---------------------------------------------------


def test_payload_only_carries_what_was_asked_for():
    assert ov.build_payload(_build()) == {"model": "m", "prompt": "hi"}
    full = ov.build_payload(_build(duration=4, aspect_ratio="9:16"))
    assert full["duration"] == 4 and full["aspect_ratio"] == "9:16", full
    framed = ov.build_payload(_build(first_frame="https://cdn.example.com/a.png"))
    assert framed["frame_images"] == [
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.com/a.png"},
            "frame_type": "first_frame",
        }
    ], framed


def test_frame_url_must_be_a_public_https_url():
    for bad in ("http://cdn.example.com/a.png", "file:///tmp/a.png",
                "https://localhost/a.png", "https://10.0.0.7/a.png",
                "https://user:pw@cdn.example.com/a.png", "not a url"):
        try:
            ov.build_payload(_build(first_frame=bad))
        except ValueError as exc:
            assert "--image-url/--first-frame" in str(exc), exc
            continue
        raise AssertionError(f"accepted an untrusted frame url: {bad}")


def test_catalogue_decides_what_is_sendable():
    entries = [{"id": "google/veo-3.1", "supported_durations": [4, 6],
                "supported_aspect_ratios": ["16:9"], "supported_frame_images": ["first_frame"]}]
    ok = {"model": "google/veo-3.1", "prompt": "hi", "duration": 4, "aspect_ratio": "16:9"}
    assert ov.unsupported_reason(ok, entries) is None
    bad_duration = ov.unsupported_reason({**ok, "duration": 5}, entries) or ""
    assert "does not support duration=5" in bad_duration, bad_duration
    bad_ratio = ov.unsupported_reason({**ok, "aspect_ratio": "1:1"}, entries) or ""
    assert "does not support aspect_ratio='1:1'" in bad_ratio, bad_ratio
    unknown = ov.unsupported_reason({**ok, "model": "nope/none"}, entries) or ""
    assert "is not in GET" in unknown, unknown
    no_frames = ov.unsupported_reason(
        {**ok, "frame_images": [{"frame_type": "first_frame"}]},
        [{**entries[0], "supported_frame_images": ["last_frame"]}],
    ) or ""
    assert "does not accept first_frame" in no_frames, no_frames


def test_status_buckets_cover_the_documented_states():
    assert ov._status_bucket("completed") == "completed"
    for pending in ("pending", "in_progress"):
        assert ov._status_bucket(pending) == "pending"
    for failed in ("failed", "cancelled", "expired"):
        assert ov._status_bucket(failed) == "failed"
    try:
        ov._status_bucket("teleported")
    except OpenRouterError as exc:
        assert "unexpected job status" in str(exc), exc
    else:
        raise AssertionError("an undocumented status must raise")


# -- tool: local refusals --------------------------------------------------


def test_output_must_be_an_mp4_and_not_a_directory():
    with tempfile.TemporaryDirectory() as tmp:
        _, err = _run(["--prompt", "hi", "--output", str(Path(tmp) / "x.webm")], 2)
        assert "must end with .mp4" in err, err
        _, err = _run(["--prompt", "hi", "--output", tmp], 2)
        assert "is a directory" in err, err
        assert sorted(p.name for p in Path(tmp).iterdir()) == [], "something was written"


def test_empty_prompt_and_bad_duration_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "x.mp4")
        assert "empty" in _run(["--prompt", "  ", "--output", out], 2)[1]
        assert "--duration must be > 0" in _run(
            ["--prompt", "hi", "--duration", "0", "--output", out], 2
        )[1]


def test_dry_run_touches_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dry.mp4"
        text, _ = _run(
            ["--prompt", "a fox", "--duration", "4", "--aspect-ratio", "16:9",
             "--output", str(out), "--dry-run"], 0
        )
        assert not out.exists()
        assert "mode: dry-run" in text and "/videos" in text, text
        assert ov.DEFAULT_MODEL in text and "frame_images" not in text, text
        assert "content?index=0" in text, text


def test_unknown_model_never_reaches_the_submit_route():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        try:
            opener = _use_fake_client([(200, _catalogue(model="other/video"))])
            _, err = _run(["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4")], 2)
        finally:
            ov.OpenRouterClient = original
        assert "is not in GET" in err, err
        assert opener.paths == ["videos/models"], opener.paths


def test_unsupported_duration_and_ratio_never_reach_the_submit_route():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        try:
            opener = _use_fake_client([(200, _catalogue())])
            _, err = _run(
                ["--prompt", "hi", "--duration", "5", "--output", str(Path(tmp) / "x.mp4")], 2
            )
            assert "does not support duration=5" in err, err
            assert opener.paths == ["videos/models"], opener.paths
        finally:
            ov.OpenRouterClient = original


def test_missing_key_exits_with_a_hint():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        try:
            opener = _use_fake_client([(200, _catalogue())], api_key=None)
            _, err = _run(["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4")], 2)
        finally:
            ov.OpenRouterClient = original
        assert "OPENROUTER_API_KEY is not set" in err, err
        assert "hint: export OPENROUTER_API_KEY" in err, err
        assert opener.paths == ["videos/models"], opener.paths


def test_a_traversing_job_id_is_refused_before_download():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        try:
            opener = _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": "../etc", "status": "pending"}).encode()),
            ])
            _, err = _run(["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4")], 1)
        finally:
            ov.OpenRouterClient = original
        assert "no usable job id" in err, err
        assert opener.paths == ["videos/models", "videos"], opener.paths


def test_no_working_ffprobe_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        original = ov.find_ffprobe
        ov.find_ffprobe = lambda explicit=None: None  # type: ignore[assignment]
        try:
            _, err = _run(["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4")], 2)
        finally:
            ov.find_ffprobe = original  # type: ignore[assignment]
        assert "no working ffprobe" in err, err


# -- tool: polling and download -------------------------------------------


def test_live_run_polls_then_writes_a_probed_mp4():
    clip = _mp4_bytes()
    if clip is None:
        print("skip test_live_run_polls_then_writes_a_probed_mp4: no working ffmpeg/ffprobe")
        return
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "nested" / "clip.mp4"
        try:
            opener = _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": JOB, "polling_url": "x", "status": "pending"}).encode()),
                (200, _poll("in_progress")),
                (200, _poll("completed", unsigned_urls=[
                    "https://openrouter.ai/api/v1/videos/abc123/content?index=0"],
                    usage={"cost": 0.25, "is_byok": False})),
                (200, clip),
            ])
            text, _ = _run(
                ["--prompt", "a fox", "--duration", "4", "--aspect-ratio", "16:9",
                 "--output", str(out), "--poll-interval", "0"], 0
            )
        finally:
            ov.OpenRouterClient = original
        assert out.read_bytes() == clip, "the downloaded bytes were altered"
        assert [p.name for p in out.parent.iterdir()] == ["clip.mp4"], "a temp file was left"
        assert opener.paths == [
            "videos/models", "videos", f"videos/{JOB}", f"videos/{JOB}",
            f"videos/{JOB}/content?index=0",
        ], opener.paths
        assert "poll" in text and "in_progress" in text, text
        assert "mode: live" in text and "cost=$0.2500" in text, text
        assert "64x48" in text and "sha256" in text, text


def test_failed_job_writes_nothing_and_quotes_the_server():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.mp4"
        try:
            _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": JOB, "status": "pending"}).encode()),
                (200, _poll("failed", error="Content policy violation")),
            ])
            _, err = _run(["--prompt", "hi", "--output", str(out), "--poll-interval", "0"], 1)
        finally:
            ov.OpenRouterClient = original
        assert "Content policy violation" in err, err
        assert not out.exists()


def test_poll_deadline_gives_up_with_a_status():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": JOB, "status": "pending"}).encode()),
                (200, _poll("pending")),
            ])
            _, err = _run(
                ["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4"),
                 "--poll-interval", "0", "--poll-timeout", "0"], 1
            )
        finally:
            ov.OpenRouterClient = original
        assert "still pending after 0s" in err, err


def test_429_on_a_poll_is_retried():
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        clip = _mp4_bytes()
        if clip is None:
            print("skip test_429_on_a_poll_is_retried: no working ffmpeg/ffprobe")
            return
        try:
            opener = _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": JOB, "status": "pending"}).encode()),
                (429, b'{"error":{"message":"slow down"}}'),
                (200, _poll("completed")),
                (200, clip),
            ])
            text, _ = _run(
                ["--prompt", "hi", "--output", str(Path(tmp) / "x.mp4"),
                 "--poll-interval", "0", "--max-attempts", "3"], 0
            )
        finally:
            ov.OpenRouterClient = original
        assert len(opener.paths) == 5, opener.paths
        assert "mode: live" in text and "not reported by the server" in text, text


def test_json_instead_of_a_video_leaves_no_file():
    if _mp4_bytes() is None:
        print("skip test_json_instead_of_a_video_leaves_no_file: no working ffprobe")
        return
    original = ov.OpenRouterClient
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.mp4"
        try:
            _use_fake_client([
                (200, _catalogue()),
                (202, json.dumps({"id": JOB, "status": "pending"}).encode()),
                (200, _poll("completed")),
                (200, b'{"error": "not a video"}'),
            ])
            _, err = _run(["--prompt", "hi", "--output", str(out), "--poll-interval", "0"], 1)
        finally:
            ov.OpenRouterClient = original
        assert "cannot write" in err and "ffprobe" in err, err
        assert not out.exists() and sorted(p.name for p in Path(tmp).iterdir()) == [], (
            "a rejected body left something behind"
        )


# -- tool: mock replay -----------------------------------------------------


def _mock_dir(directory: Path, *, submit=None, polls=(), content=b"") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "submit.json").write_text(
        json.dumps(submit if submit is not None else {"id": JOB, "status": "pending"}),
        encoding="utf-8",
    )
    for index, poll in enumerate(polls, start=1):
        (directory / f"poll-{index}.json").write_text(json.dumps(poll), encoding="utf-8")
    if content:
        (directory / "content.bin").write_bytes(content)
    return directory


def test_mock_dir_replays_submit_poll_and_content():
    clip = _mp4_bytes()
    if clip is None:
        print("skip test_mock_dir_replays_submit_poll_and_content: no working ffmpeg/ffprobe")
        return
    with tempfile.TemporaryDirectory() as tmp:
        mock = _mock_dir(
            Path(tmp) / "mock",
            polls=[
                {"id": JOB, "status": "pending"},
                {"id": JOB, "status": "completed", "usage": {"cost": 0.5, "is_byok": False}},
            ],
            content=clip,
        )
        out = Path(tmp) / "out" / "mock.mp4"
        text, _ = _run(
            ["--prompt", "a fox", "--mock-dir", str(mock), "--output", str(out),
             "--poll-interval", "0"], 0
        )
        assert out.read_bytes() == clip
        assert "mode: mock" in text and "cost=$0.5000" in text, text
        assert text.count("poll ") == 2, text
        assert "status: completed" in text, text


def test_mock_dir_needs_its_files():
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        _, err = _run(["--prompt", "hi", "--mock-dir", str(empty),
                       "--output", str(Path(tmp) / "x.mp4")], 2)
        assert "cannot read mock" in err, err
        only_submit = _mock_dir(Path(tmp) / "only-needs-poll")
        _, err = _run(["--prompt", "hi", "--mock-dir", str(only_submit),
                       "--output", str(Path(tmp) / "y.mp4")], 2)
        assert "no poll" in err, err


def test_mock_failed_status_and_absent_content_are_errors():
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "x.mp4"
        failed = _mock_dir(Path(tmp) / "failed", polls=[{"id": JOB, "status": "expired",
                                                         "error": "job TTL exceeded"}])
        _, err = _run(["--prompt", "hi", "--mock-dir", str(failed),
                       "--output", str(out), "--poll-interval", "0"], 1)
        assert "job TTL exceeded" in err, err
        assert not out.exists()
        done = _mock_dir(Path(tmp) / "done", polls=[{"id": JOB, "status": "completed"}])
        _, err = _run(["--prompt", "hi", "--mock-dir", str(done),
                       "--output", str(out), "--poll-interval", "0"], 1)
        assert "cannot read mock content" in err, err
        assert not out.exists()


# -- tool: surface ---------------------------------------------------------


def test_help_lists_the_documented_flags():
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            ov.main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0, exc.code
    text = buffer.getvalue()
    for flag in ("--prompt", "--image-url", "--first-frame", "--model", "--duration",
                 "--aspect-ratio", "--output", "--ffprobe", "--mock-dir", "--dry-run"):
        assert flag in text, f"{flag} missing from --help"


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items()) if name.startswith("test_")]
    for name, test in tests:
        test()
        print(f"ok {name}")
    print(f"{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
