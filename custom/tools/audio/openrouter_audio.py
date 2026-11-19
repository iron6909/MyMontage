"""custom/tools/audio/openrouter_audio.py — one speech clip from OpenRouter.

Protocol as verified on 2026-11-19:

  https://openrouter.ai/docs/guides/overview/multimodal/tts
  https://openrouter.ai/docs/api/api-reference/tts/create-speech

      POST {base}/audio/speech
      {"model": "...", "input": "...", "voice": "...", "speed": 1.0,
       "response_format": "mp3" | "pcm"}
      -> 200 audio/*: "Raw audio bytestream. Content-Type varies by requested
         format (audio/mpeg for mp3, audio/pcm for pcm — 16-bit little-endian)."
      -> 4xx/5xx: JSON {"error": {"code", "message"}} — the *only* JSON on this
         route, so a success body is never parsed as JSON.

      GET {base}/models?output_modalities=speech
      -> {"data": [{"id", "supported_voices", ...}]}  (39 entries at verification)

`response_format` defaults to pcm in the API; this tool defaults to mp3 and
normalises with ffmpeg, because raw pcm carries no header and its sample rate is
provider-specific (24 kHz for Microsoft MAI, 44.1 kHz for HeyGen). Asking for pcm
therefore requires `--pcm-sample-rate`.

DEFAULT_MODEL `mistralai/voxtral-mini-tts-2603` is the model in both official
examples and present in the speech catalogue. Voice ids are model-specific, so
`--voice` is checked against that model's `supported_voices` before anything is
sent — an unsupported voice is either rejected or silently ignored by providers,
and reporting success for ignored audio would be a lie.

`speed` is documented as "honored by models that support it; other providers
either ignore it or return a 400 for a non-default value" — this tool passes it
through and labels it as unverified rather than pretending it was applied.

No key, or no way to bound the spend? Stay offline: `--dry-run` prints the exact
request and the ffmpeg command, `--mock-response FILE` replays captured audio
bytes through the real convert-and-write path. Both report `mode: dry-run` /
`mode: mock` so neither can be mistaken for a live run.

    python3 -m custom.tools.audio.openrouter_audio --help
    python3 -m custom.tools.audio.openrouter_audio --text "Hello there" \
        --voice en_paul_neutral --output out/line.wav
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from custom.tools.common.openrouter_client import (
    ENV_API_KEY,
    MissingApiKey,
    OpenRouterClient,
    OpenRouterError,
    format_report,
)

SPEECH_PATH = "audio/speech"
SPEECH_MODELS_PATH = "models?output_modalities=speech"
# Verified present in the speech catalogue and used by the official TTS example.
DEFAULT_MODEL = "mistralai/voxtral-mini-tts-2603"
DEFAULT_RESPONSE_FORMAT = "mp3"
ENV_FFMPEG = "FFMPEG"
MAX_DETAIL = 400

# One container back to canonical audio, so pcm/mp3 never lands as a mislabelled
# file: ffmpeg fails loudly instead of leaving raw bytes with a .wav extension.
_OUTPUT_SPEC: dict[str, tuple[str, list[str]]] = {
    ".wav": ("wav", ["-c:a", "pcm_s16le"]),
    ".mp3": ("mp3", ["-c:a", "libmp3lame", "-q:a", "2"]),
}


def _output_spec(target: Path) -> tuple[str, list[str]]:
    spec = _OUTPUT_SPEC.get(target.suffix.lower())
    if spec is None:
        raise ValueError(
            f"--output {target} must end with .wav or .mp3 "
            f"(got {target.suffix or 'no suffix'})"
        )
    return spec


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """First ffmpeg that actually runs.

    Same order as custom/scripts/setup_local.sh: a binary earlier on PATH that
    cannot start (Homebrew's, on this machine) is worse than no binary at all.
    """
    for candidate in (
        explicit or os.environ.get(ENV_FFMPEG),
        "/usr/bin/ffmpeg",
        shutil.which("ffmpeg"),
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
    payload: dict[str, Any] = {
        "model": args.model,
        "input": args.text,
        "response_format": args.response_format,
    }
    if args.voice:
        payload["voice"] = args.voice
    if args.speed is not None:
        payload["speed"] = args.speed
    return payload


def _catalogue(client: OpenRouterClient, timeout: float) -> list[dict[str, Any]]:
    data, _meta = client.get_json(SPEECH_MODELS_PATH, auth=False, timeout=timeout)
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise OpenRouterError(f"unexpected catalogue payload from {SPEECH_MODELS_PATH}")
    return [item for item in items if isinstance(item, dict)]


def _body_problem(data: bytes) -> str | None:
    """Reject the bodies that are errors wearing a 200: nothing, or JSON."""
    if not data:
        return "response body is empty"
    if data.lstrip()[:1] in (b"{", b"["):
        detail = " ".join(data.decode("utf-8", "replace").split())[:MAX_DETAIL]
        return f"expected audio bytes, got JSON: {detail}"
    return None


def _convert(
    ffmpeg: str,
    data: bytes,
    *,
    response_format: str,
    pcm_sample_rate: int | None,
    target: Path,
    timeout: float = 180.0,
) -> None:
    """Decode `data` with ffmpeg into canonical audio, then rename into place."""
    container, codec = _output_spec(target)
    if container == "mp3" and response_format == "mp3":
        codec = ["-c:a", "copy"]  # already mp3: remux, never re-encode twice
    source = (
        ["-f", "s16le", "-ac", "1", "-ar", str(pcm_sample_rate)]
        if response_format == "pcm"
        else ["-f", "mp3"]
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    command = [
        ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y",
        *source, "-i", "pipe:0", *codec, "-f", container, str(tmp),
    ]
    try:
        done = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
        if done.returncode != 0:
            detail = " ".join(done.stderr.decode("utf-8", "replace").split())[:MAX_DETAIL]
            raise RuntimeError(f"ffmpeg exited {done.returncode}: {detail or '<no stderr>'}")
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg wrote no audio to {tmp}")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _dry_run_report(args: argparse.Namespace, payload: dict[str, Any], out: Path) -> None:
    print("mode: dry-run (no network, nothing written)")
    print(f"POST {OpenRouterClient(base_url=None).base_url}/{SPEECH_PATH}")
    print(f"ffmpeg: {find_ffmpeg(args.ffmpeg) or '<no working ffmpeg found>'}")
    print(f"output: {out}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m custom.tools.audio.openrouter_audio",
        description="Synthesize speech via OpenRouter (POST /api/v1/audio/speech).",
        epilog=(
            f"default model {DEFAULT_MODEL} is present in "
            f"GET /api/v1/{SPEECH_MODELS_PATH}; voice ids are model-specific"
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="text to synthesize (the API's `input`)")
    source.add_argument("--prompt", dest="text", help="alias of --text")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"speech model slug (default: {DEFAULT_MODEL})"
    )
    parser.add_argument("--voice", help="voice id; must appear in the model's supported_voices")
    parser.add_argument("--speed", type=float, help="playback speed; only some providers honor it")
    parser.add_argument(
        "--response-format",
        choices=("mp3", "pcm"),
        default=DEFAULT_RESPONSE_FORMAT,
        help=f"audio format the API returns (default: {DEFAULT_RESPONSE_FORMAT})",
    )
    parser.add_argument(
        "--pcm-sample-rate",
        type=int,
        help="sample rate of pcm output; required with --response-format pcm (providers differ)",
    )
    parser.add_argument("--output", required=True, help="file path ending in .wav or .mp3")
    parser.add_argument("--ffmpeg", help=f"ffmpeg binary (default: probe PATH, then ${ENV_FFMPEG})")
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request timeout (s)")
    parser.add_argument("--max-attempts", type=int, default=3)
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument(
        "--dry-run", action="store_true", help="print the request and exit; no network"
    )
    offline.add_argument(
        "--mock-response",
        metavar="FILE",
        help="replay this file of raw audio bytes instead of calling the API",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    out = Path(args.output).expanduser()
    try:
        if out.is_dir():
            raise ValueError(f"--output {out} is a directory; pass a file path")
        _output_spec(out)
        if not (args.text or "").strip():
            raise ValueError("--text/--prompt is empty; nothing to synthesize")
        if args.speed is not None and args.speed <= 0:
            raise ValueError(f"--speed must be > 0 (got {args.speed})")
        if args.response_format == "pcm" and not args.pcm_sample_rate:
            raise ValueError(
                "--response-format pcm needs --pcm-sample-rate: raw pcm has no header and "
                "providers differ (24 kHz mono for Microsoft MAI, 44.1 kHz for HeyGen)"
            )
        payload = build_payload(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _dry_run_report(args, payload, out)
        return 0

    ffmpeg = find_ffmpeg(args.ffmpeg)
    if ffmpeg is None:
        print(
            "error: no working ffmpeg found; set --ffmpeg or FFMPEG=/path/to/ffmpeg "
            "(a broken binary earlier on PATH is skipped, not trusted)",
            file=sys.stderr,
        )
        return 2

    if args.mock_response:
        try:
            data = Path(args.mock_response).read_bytes()
        except OSError as exc:
            print(f"error: cannot read mock response {args.mock_response}: {exc}", file=sys.stderr)
            return 2
        meta: dict[str, Any] = {
            "method": "POST",
            "path": SPEECH_PATH,
            "status": "mock",
            "elapsed_s": 0.0,
            "mode": "mock",
        }
    else:
        client = OpenRouterClient(timeout=args.timeout, max_attempts=args.max_attempts)
        # The catalogue is auth-free and cheap; it is the only place that says
        # whether this model exists and which voices it accepts.
        try:
            entries: list[dict[str, Any]] | None = _catalogue(client, min(args.timeout, 60.0))
        except OpenRouterError as exc:
            print(f"warning: cannot read {SPEECH_MODELS_PATH} ({exc}); sending anyway", file=sys.stderr)
            entries = None
        if entries is not None:
            entry = next((item for item in entries if item.get("id") == payload["model"]), None)
            if entry is None:
                print(
                    f"error: {payload['model']!r} is not in GET {client.base_url}/"
                    f"{SPEECH_MODELS_PATH} ({len(entries)} entries); nothing was sent",
                    file=sys.stderr,
                )
                return 2
            voices = [v for v in entry.get("supported_voices") or [] if isinstance(v, str)]
            voice = payload.get("voice")
            if voice and voices and voice not in voices:
                listing = ", ".join(voices[:12]) + (" ..." if len(voices) > 12 else "")
                print(
                    f"error: {payload['model']} does not list voice {voice!r}, and providers "
                    f"either reject or silently ignore an unknown voice, so nothing was sent. "
                    f"It lists {len(voices)}: {listing}",
                    file=sys.stderr,
                )
                return 2
        try:
            data, meta = client.post_raw(SPEECH_PATH, payload)
        except MissingApiKey as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(f"hint: export {ENV_API_KEY}, or use --dry-run / --mock-response", file=sys.stderr)
            return 2
        except OpenRouterError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    problem = _body_problem(data)
    if problem is not None:
        print(f"error: {problem}", file=sys.stderr)
        return 1

    try:
        _convert(
            ffmpeg,
            data,
            response_format=args.response_format,
            pcm_sample_rate=args.pcm_sample_rate,
            target=out,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: cannot write {out}: {exc}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:16]
    print(f"mode: {meta.get('mode', 'live')}")
    print(f"model: {payload['model']}")
    print(f"input: {len(payload['input'])} chars")
    print(f"voice: {payload.get('voice', '(omitted; provider default)')}")
    if "speed" in payload:
        print(f"speed: {payload['speed']} (server-side; docs: providers may ignore it or 400)")
    print(
        f"response_format: {args.response_format} -> {out.suffix.lstrip('.')} via {ffmpeg}"
    )
    # Raw audio carries no usage block, so nothing about cost is claimed here.
    print(format_report(meta, {}))
    print(f"audio: {out} ({out.stat().st_size} bytes, sha256 {digest})")
    print(f"total: {time.monotonic() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
