"""custom/tools/image/openrouter_image.py — one image from OpenRouter's Image API.

Protocol (verified 2026-11-19 — see custom/tools/common/openrouter_client.py for
the doc URLs and the live catalogue check):

    POST {base}/images
    {"model": "openai/gpt-image-2", "prompt": "...", "aspect_ratio": "16:9"}
    -> {"data": [{"b64_json": "...", "media_type": "image/png"}], "usage": {"cost": ...}}

`/api/v1/images/generations` is the same POST route per the compatibility docs;
this tool uses the primary `/api/v1/images` path only.

DEFAULT_MODEL is `openai/gpt-image-2`, verified present in `GET /images/models`
(70 entries at verification time) and used in the official image-generation
example. The model list is the authority — pass `--model` with any slug it lists.

    python3 -m custom.tools.image.openrouter_image --help
    python3 -m custom.tools.image.openrouter_image --prompt "a red fox" \
        --model openai/gpt-image-2 --aspect-ratio 16:9 --output out/fox.png

No key, or no way to bound the spend? Stay offline: `--dry-run` prints the exact
request, `--mock-response FILE` replays a captured response through the real
parse-and-write path. Neither touches the network, and both are reported as
`mode: dry-run` / `mode: mock` so they can never be mistaken for a live run.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
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
    usage_report,
)

# Verified against GET https://openrouter.ai/api/v1/images/models (2026-11-19):
# 70 entries, this slug present, aspect_ratio enum includes 16:9.
DEFAULT_MODEL = "openai/gpt-image-2"
DEFAULT_ASPECT_RATIO = "16:9"
IMAGES_PATH = "images"


def _sniff_media_type(data: bytes) -> str:
    """Guess a media type from the bytes, for responses that omit `media_type`."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if b"<svg" in data[:512].lower():
        return "image/svg+xml"
    return "application/octet-stream"


def _entry_bytes(entry: dict[str, Any], client: OpenRouterClient) -> bytes:
    """Decode one `data[]` entry: documented `b64_json`, or a remote media URL."""
    b64 = entry.get("b64_json")
    if isinstance(b64, str) and b64.strip():
        text = b64.strip()
        if text.startswith("data:"):
            header, _, payload = text.partition(",")
            if ";base64" not in header:
                raise OpenRouterError(f"b64_json data URL is not base64-encoded: {header!r}")
            text = payload
        try:
            return base64.b64decode("".join(text.split()), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise OpenRouterError(f"b64_json is not valid base64: {exc}") from None
    url = entry.get("url") or entry.get("image_url")
    if isinstance(url, str) and url.strip():
        return client.download_media(url.strip())
    raise OpenRouterError(
        "response entry carries neither b64_json nor url: "
        + (", ".join(sorted(entry)) or "<empty>")
    )


def _atomic_write(path: Path, data: bytes) -> None:
    """Write next to the target and rename, so a crash never leaves a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
        "aspect_ratio": args.aspect_ratio,
    }
    if args.quality:
        payload["quality"] = args.quality
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m custom.tools.image.openrouter_image",
        description="Generate one image via OpenRouter (POST /api/v1/images).",
        epilog=f"default model {DEFAULT_MODEL} is verified in GET /api/v1/images/models",
    )
    parser.add_argument("--prompt", required=True, help="image prompt")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"image model slug (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--aspect-ratio", default=DEFAULT_ASPECT_RATIO, help="e.g. 1:1, 16:9, 9:16, auto"
    )
    parser.add_argument("--output", required=True, help="file path; written atomically")
    parser.add_argument("--quality", choices=("auto", "low", "medium", "high"))
    parser.add_argument("--timeout", type=float, default=180.0, help="per-request timeout (s)")
    parser.add_argument("--max-attempts", type=int, default=3)
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument(
        "--dry-run", action="store_true", help="print the request and exit; no network"
    )
    offline.add_argument(
        "--mock-response",
        metavar="JSON",
        help="replay this documented-shape response instead of calling the API",
    )
    args = parser.parse_args(argv)

    started = time.monotonic()
    payload = build_payload(args)
    out = Path(args.output).expanduser()
    client = OpenRouterClient(timeout=args.timeout, max_attempts=args.max_attempts)
    base = client.base_url

    if args.dry_run:
        print("mode: dry-run (no network, nothing written)")
        print(f"POST {base}/{IMAGES_PATH}")
        print(f"output: {out}")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if out.is_dir():
        print(f"error: --output {out} is a directory; pass a file path", file=sys.stderr)
        return 2

    if args.mock_response:
        try:
            response = json.loads(Path(args.mock_response).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read mock response {args.mock_response}: {exc}", file=sys.stderr)
            return 2
        meta: dict[str, Any] = {
            "method": "POST",
            "path": IMAGES_PATH,
            "status": "mock",
            "elapsed_s": 0.0,
            "mode": "mock",
        }
    else:
        try:
            response, meta = client.post_json(IMAGES_PATH, payload)
        except MissingApiKey as exc:
            print(f"error: {exc}", file=sys.stderr)
            print(
                f"hint: export {ENV_API_KEY}, or use --dry-run / --mock-response",
                file=sys.stderr,
            )
            return 2
        except OpenRouterError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    entries = response.get("data") if isinstance(response, dict) else None
    if not isinstance(entries, list) or not entries:
        print(
            "error: response has no data[] images: "
            + json.dumps(response, ensure_ascii=False)[:MAX_PREVIEW],
            file=sys.stderr,
        )
        return 1
    entry = entries[0] if isinstance(entries[0], dict) else {}
    try:
        data = _entry_bytes(entry, client)
    except OpenRouterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not data:
        print("error: decoded image is empty", file=sys.stderr)
        return 1

    media_type = entry.get("media_type") or _sniff_media_type(data)
    try:
        _atomic_write(out, data)
    except OSError as exc:
        print(f"error: cannot write {out}: {exc}", file=sys.stderr)
        return 1

    print(f"mode: {meta.get('mode', 'live')}")
    print(f"model: {payload['model']}")
    print(f"aspect_ratio: {payload['aspect_ratio']}")
    print(format_report(meta, usage_report(response)))
    print(
        f"image: {out} ({len(data)} bytes, {media_type}, "
        f"sha256 {hashlib.sha256(data).hexdigest()[:16]})"
    )
    print(f"total: {time.monotonic() - started:.2f}s")
    return 0


MAX_PREVIEW = 300

if __name__ == "__main__":
    raise SystemExit(main())
