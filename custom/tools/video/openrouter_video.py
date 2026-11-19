"""custom/tools/video/openrouter_video.py — one clip from OpenRouter's async Video API.

Protocol (re-read from the official page on 2026-11-19; the saved copy is
.task/evidence/moz26_video_generation_docs.md):

  https://openrouter.ai/docs/guides/overview/multimodal/video-generation

      POST {base}/videos
          {"model", "prompt", "duration", "aspect_ratio", "frame_images": [
             {"type": "image_url", "image_url": {"url": ...}, "frame_type": "first_frame"}]}
      -> 202 {"id", "polling_url", "status": "pending"}

      GET {base}/videos/{id}
      -> {"status": "pending" | "in_progress" | "completed" | "failed" |
                    "cancelled" | "expired",
          "unsigned_urls": [...], "usage": {"cost", "is_byok"}, "error"?}

      GET {base}/videos/{id}/content?index=0   -> the mp4 bytes.
          `unsigned_urls` are *not* presigned, so the same Authorization header is
          needed; the content route is also directly callable, which is what this
          tool uses (see `_content_path`).

      GET {base}/videos/models
      -> {"data": [{"id", "supported_durations", "supported_aspect_ratios",
                    "supported_frame_images", "pricing_skus", ...}]}

DEFAULT_MODEL is `google/veo-3.1`: the slug in the official examples, re-verified
in GET /videos/models right before this file was written (35 entries, also
containing minimax/hailuo-3). Duration, aspect ratio and first-frame support are
validated against that catalogue *before* anything is sent — an unsupported value
is a 400 at best and silently ignored at worst.

Video generation is billed per output second (Veo's published example is about
$0.50/s), so the live path spends real money:

    python3 -m custom.tools.video.openrouter_video --help
    python3 -m custom.tools.video.openrouter_video --prompt "a red fox" \
        --duration 4 --aspect-ratio 16:9 --output out/fox.mp4

No key, or no approved spend? Stay offline: `--dry-run` prints the request, and
`--mock-dir DIR` replays captured submit/poll/content files through the real
poll, download and ffprobe path. Both report `mode: dry-run` / `mode: mock`, so a
rehearsal can never be read as a live run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from custom.tools.common.openrouter_client import (
    ENV_API_KEY,
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
    check_media_url,
    format_report,
    usage_report,
)

VIDEOS_PATH = "videos"
MODELS_PATH = "videos/models"
# Verified in GET /api/v1/videos/models (35 entries, 2026-11-19 08:50 +08).
DEFAULT_MODEL = "google/veo-3.1"
DEFAULT_POLL_INTERVAL = 30.0
DEFAULT_POLL_TIMEOUT = 900.0
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
ENV_FFPROBE = "FFPROBE"
MAX_DETAIL = 400
PENDING_STATUS = frozenset({"pending", "in_progress"})
FAILED_STATUS = frozenset({"failed", "cancelled", "expired"})
# The job id goes straight into a URL path, so it is held to the alphabet a job id
# can contain — no dots, no slashes, no query characters.
JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def find_ffprobe(explicit: str | None = None) -> str | None:
    """First ffprobe that actually runs.

    Same order and same reason as find_ffmpeg in custom/tools/audio: a binary
    earlier on PATH that cannot start (Homebrew's, on this machine) is worse than
    no binary at all.
    """
    for candidate in (
        explicit or os.environ.get(ENV_FFPROBE),
        "/usr/bin/ffprobe",
        shutil.which("ffprobe"),
    ):
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "-version"], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Only what was asked for; the API's own defaults fill the rest."""
    payload: dict[str, Any] = {"model": args.model, "prompt": args.prompt}
    if args.duration is not None:
        payload["duration"] = args.duration
    if args.aspect_ratio:
        payload["aspect_ratio"] = args.aspect_ratio
    frame = (args.first_frame or "").strip()
    if frame:
        try:
            check_media_url(frame)
        except OpenRouterError as exc:
            raise ValueError(f"--image-url/--first-frame {exc}") from None
        # Official image-to-video shape; `frame_type` is what selects the mode.
        payload["frame_images"] = [
            {"type": "image_url", "image_url": {"url": frame}, "frame_type": "first_frame"}
        ]
    return payload


def _catalogue(client: OpenRouterClient, timeout: float) -> list[dict[str, Any]]:
    data, _meta = client.get_json(MODELS_PATH, auth=False, timeout=timeout)
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise OpenRouterError(f"unexpected catalogue payload from {MODELS_PATH}")
    return [item for item in items if isinstance(item, dict)]


def unsupported_reason(payload: dict[str, Any], entries: list[dict[str, Any]]) -> str | None:
    """None when the request is inside the catalogue, else why it is not."""
    entry = next((item for item in entries if item.get("id") == payload["model"]), None)
    if entry is None:
        return (
            f"{payload['model']!r} is not in GET /api/v1/{MODELS_PATH} "
            f"({len(entries)} entries); nothing was sent"
        )
    for field, key in (
        ("supported_durations", "duration"),
        ("supported_aspect_ratios", "aspect_ratio"),
    ):
        allowed = entry.get(field)
        if key in payload and isinstance(allowed, list) and allowed and payload[key] not in allowed:
            return (
                f"{payload['model']} does not support {key}={payload[key]!r}; it lists {allowed}. "
                "Nothing was sent"
            )
    if payload.get("frame_images"):
        allowed = entry.get("supported_frame_images")
        if isinstance(allowed, list) and allowed and "first_frame" not in allowed:
            return (
                f"{payload['model']} does not accept first_frame images; it lists {allowed}. "
                "Nothing was sent"
            )
    return None


def _status_bucket(status: str) -> str:
    if status == "completed":
        return "completed"
    if status in PENDING_STATUS:
        return "pending"
    if status in FAILED_STATUS:
        return "failed"
    raise OpenRouterError(
        f"unexpected job status {status or '<missing>'!r} "
        f"(documented: pending, in_progress, completed, failed)"
    )


def _job_id(job: Any) -> str:
    raw = job.get("id") if isinstance(job, dict) else None
    if not isinstance(raw, str) or not JOB_ID.fullmatch(raw):
        preview = json.dumps(job, ensure_ascii=False)[:MAX_DETAIL]
        raise OpenRouterError(f"submit response carries no usable job id: {preview}")
    return raw


def _error_text(job: dict[str, Any]) -> str:
    error = job.get("error")
    if isinstance(error, dict):
        error = error.get("message") or json.dumps(error, ensure_ascii=False)
    return " ".join(str(error or "no error field").split())[:MAX_DETAIL]


def poll_job(
    fetch: Callable[[str], dict[str, Any]],
    job_id: str,
    *,
    interval: float,
    deadline: float,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Poll GET /videos/{id} until a terminal status. Returns the completed job."""
    started = time.monotonic()
    while True:
        job = fetch(job_id)
        status = str(job.get("status") or "") if isinstance(job, dict) else ""
        out(f"poll {time.monotonic() - started:6.1f}s: {status or '<no status>'}")
        if not isinstance(job, dict):
            raise OpenRouterError(f"poll for {job_id} returned no JSON object")
        bucket = _status_bucket(status)
        if bucket == "completed":
            return job
        if bucket == "failed":
            raise OpenRouterError(f"job {job_id} {status}: {_error_text(job)}")
        if time.monotonic() - started >= deadline:
            raise OpenRouterError(
                f"job {job_id} still {status} after {deadline:g}s; giving up "
                "(the job itself may still finish on the server)"
            )
        sleep(interval)


def _content_path(job_id: str) -> str:
    """`videos/{id}/content?index=0` for an already-validated job id.

    The path is rebuilt from the base URL instead of following `unsigned_urls`, so
    a URL handed back by the server never decides where the bytes come from. The
    docs say this route is directly callable and `index` defaults to 0.
    """
    return f"{VIDEOS_PATH}/{job_id}/content?index=0"


def probe_video(ffprobe: str, path: Path, *, timeout: float = 120.0) -> dict[str, Any]:
    """First video stream of `path`, else RuntimeError with ffprobe's own words."""
    command = [
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_type,codec_name,width,height:format=format_name,duration",
        "-of", "json", str(path),
    ]
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot run {ffprobe}: {exc}") from None
    detail = " ".join(done.stderr.decode("utf-8", "replace").split())[:MAX_DETAIL]
    if done.returncode != 0:
        raise RuntimeError(f"ffprobe exited {done.returncode}: {detail or '<no stderr>'}")
    try:
        info = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        raise RuntimeError(f"ffprobe printed no JSON: {detail or '<empty stdout>'}") from None
    streams = [s for s in info.get("streams") or [] if isinstance(s, dict)]
    video = [s for s in streams if s.get("codec_type") == "video"]
    if not video:
        raise RuntimeError(
            f"no video stream in the downloaded content ({detail or 'ffprobe said nothing'})"
        )
    container = info.get("format") if isinstance(info.get("format"), dict) else {}
    return {
        **video[0],
        "format": container.get("format_name"),
        "container_duration": container.get("duration"),
    }


def write_verified(ffprobe: str, data: bytes, target: Path) -> dict[str, Any]:
    """ffprobe the bytes in a temp file beside the target, then rename into place.

    A body that is not a video never lands at --output, and a crash never leaves a
    half-written clip: the rename is the only step that publishes the file.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        info = probe_video(ffprobe, tmp)
        os.replace(tmp, target)
        return info
    finally:
        tmp.unlink(missing_ok=True)


class _Mock:
    """Replays captured job files through the real poll, download and write path.

    DIR holds `submit.json`, one or more `poll*.json` (played in order, the last
    one repeating) and `content.bin`.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        if not directory.is_dir():
            raise ValueError(f"--mock-dir {directory} is not a directory")
        self.submit = self._read_json(directory / "submit.json")
        polls = sorted(directory.glob("poll*.json"))
        if not polls:
            raise ValueError(f"{directory} has no poll*.json to replay")
        self.polls = [self._read_json(path) for path in polls]
        self.seen = 0
        self.content = directory / "content.bin"

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read mock {path}: {exc}") from None

    def submit_job(self) -> dict[str, Any]:
        return self.submit

    def poll_job(self, job_id: str) -> dict[str, Any]:
        job = self.polls[min(self.seen, len(self.polls) - 1)]
        self.seen += 1
        return job

    def read_content(self) -> bytes:
        try:
            return self.content.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot read mock content {self.content}: {exc}") from None


def _dry_run_report(args: argparse.Namespace, payload: dict[str, Any], out: Path) -> None:
    base = OpenRouterClient(base_url=None).base_url
    print("mode: dry-run (no network, no job, nothing written)")
    print(f"POST {base}/{VIDEOS_PATH}")
    print(f"poll GET {base}/{VIDEOS_PATH}/<jobId> every {args.poll_interval:g}s, deadline {args.poll_timeout:g}s")
    print(f"get  GET {base}/{VIDEOS_PATH}/<jobId>/content?index=0")
    print(f"model catalogue: GET {base}/{MODELS_PATH} (checked before the POST)")
    print(f"ffprobe: {find_ffprobe(args.ffprobe) or '<no working ffprobe found>'}")
    print(f"output: {out}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m custom.tools.video.openrouter_video",
        description="Generate one video via OpenRouter (POST /api/v1/videos, async).",
        epilog=(
            f"default model {DEFAULT_MODEL} is present in GET /api/v1/{MODELS_PATH}; "
            "video output is billed per second, so verify spend before a live run"
        ),
    )
    parser.add_argument("--prompt", required=True, help="video prompt")
    parser.add_argument(
        "--image-url",
        "--first-frame",
        dest="first_frame",
        metavar="URL",
        help="https image used as the first frame (official frame_images[first_frame])",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"video model slug (default: {DEFAULT_MODEL})"
    )
    parser.add_argument("--duration", type=int, help="output duration in seconds")
    parser.add_argument("--aspect-ratio", help="e.g. 16:9, 9:16, 1:1")
    parser.add_argument("--output", required=True, help="file path ending in .mp4; written atomically")
    parser.add_argument("--ffprobe", help=f"ffprobe binary (default: probe ${ENV_FFPROBE}, /usr/bin, PATH)")
    parser.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL,
        help=f"seconds between status polls (default: {DEFAULT_POLL_INTERVAL:g})",
    )
    parser.add_argument(
        "--poll-timeout", type=float, default=DEFAULT_POLL_TIMEOUT,
        help=f"give up after this many seconds of polling (default: {DEFAULT_POLL_TIMEOUT:g})",
    )
    parser.add_argument(
        "--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
        help=f"download cap in bytes (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request timeout (s)")
    parser.add_argument("--max-attempts", type=int, default=3)
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument(
        "--dry-run", action="store_true", help="print the request and exit; no network"
    )
    offline.add_argument(
        "--mock-dir",
        metavar="DIR",
        help="replay submit.json/poll*.json/content.bin through the real poll and write path",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    out = Path(args.output).expanduser()
    try:
        if out.is_dir():
            raise ValueError(f"--output {out} is a directory; pass a file path")
        if out.suffix.lower() != ".mp4":
            raise ValueError(
                f"--output {out} must end with .mp4 (got {out.suffix or 'no suffix'}); "
                "the content route returns an mp4"
            )
        if not (args.prompt or "").strip():
            raise ValueError("--prompt is empty; nothing to generate")
        if args.duration is not None and args.duration <= 0:
            raise ValueError(f"--duration must be > 0 (got {args.duration})")
        if args.poll_interval < 0 or args.poll_timeout < 0:
            raise ValueError("--poll-interval and --poll-timeout must be >= 0")
        payload = build_payload(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _dry_run_report(args, payload, out)
        return 0

    ffprobe = find_ffprobe(args.ffprobe)
    if ffprobe is None:
        print(
            "error: no working ffprobe found; set --ffprobe or "
            f"{ENV_FFPROBE}=/path/to/ffprobe (a broken binary earlier on PATH is skipped)",
            file=sys.stderr,
        )
        return 2

    mock: _Mock | None = None
    meta: dict[str, Any]
    if args.mock_dir:
        try:
            mock = _Mock(Path(args.mock_dir).expanduser())
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        meta = {
            "method": "POST",
            "path": VIDEOS_PATH,
            "status": "mock",
            "elapsed_s": 0.0,
            "mode": "mock",
        }
        submit_job = mock.submit_job
        fetch_job = mock.poll_job

        def download_job(job_id: str) -> tuple[bytes, dict[str, Any]]:
            return mock.read_content(), meta

    else:
        client = OpenRouterClient(timeout=args.timeout, max_attempts=args.max_attempts)
        try:
            entries = _catalogue(client, min(args.timeout, 60.0))
        except OpenRouterError as exc:
            print(
                f"warning: cannot read {MODELS_PATH} ({exc}); sending anyway",
                file=sys.stderr,
            )
            entries = None
        if entries is not None:
            reason = unsupported_reason(payload, entries)
            if reason is not None:
                print(f"error: {reason}", file=sys.stderr)
                return 2

        def submit_job() -> dict[str, Any]:
            job, _meta = client.post_json(VIDEOS_PATH, payload)
            return job

        def fetch_job(job_id: str) -> dict[str, Any]:
            job, _meta = client.get_json(f"{VIDEOS_PATH}/{job_id}")
            return job

        def download_job(job_id: str) -> tuple[bytes, dict[str, Any]]:
            return client.get_raw(
                _content_path(job_id), max_bytes=args.max_bytes, accept="video/mp4"
            )

    # The submit response is only trusted for its id: the content route is
    # rebuilt from it, so a malformed id is refused before any download.
    try:
        job_id = _job_id(submit_job())
    except MissingApiKey as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(f"hint: export {ENV_API_KEY}, or use --dry-run / --mock-dir", file=sys.stderr)
        return 2
    except OpenRouterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"job: {job_id}")

    try:
        final = poll_job(
            fetch_job,
            job_id,
            interval=args.poll_interval,
            deadline=args.poll_timeout,
        )
    except MissingApiKey as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OpenRouterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    share = final.get("unsigned_urls")
    if mock is None and isinstance(share, list) and share:
        # Reported, never followed: the download below stays on the base URL.
        print(f"server reported {len(share)} output url(s); downloading index 0")

    try:
        data, meta = download_job(job_id)
    except MissingApiKey as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OpenRouterError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not data:
        print(f"error: downloaded content for job {job_id} is empty", file=sys.stderr)
        return 1

    try:
        info = write_verified(ffprobe, data, out)
    except (OSError, RuntimeError) as exc:
        print(f"error: cannot write {out}: {exc}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    print(f"mode: {meta.get('mode', 'live')}")
    print(f"model: {payload['model']}")
    print(f"prompt: {len(payload['prompt'])} chars")
    if "duration" in payload:
        print(f"duration: {payload['duration']}s (requested)")
    if "aspect_ratio" in payload:
        print(f"aspect_ratio: {payload['aspect_ratio']} (requested)")
    if "frame_images" in payload:
        print(f"first_frame: {payload['frame_images'][0]['image_url']['url']}")
    print(f"status: {final.get('status')}")
    print(f"ffprobe: {ffprobe}")
    # Only the usage the server sent is reported; no cost is ever invented.
    print(format_report(meta, usage_report(final)))
    print(
        f"video: {out} ({len(data)} bytes, {info.get('codec_name')} "
        f"{info.get('width')}x{info.get('height')}, container {info.get('format')}, "
        f"{info.get('container_duration')}s, sha256 {digest})"
    )
    print(f"total: {time.monotonic() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
