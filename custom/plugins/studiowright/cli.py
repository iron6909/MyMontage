"""Repo-root CLI entry for the Google Flow tools (StudioWright route).

Run from the REPOSITORY ROOT — that is what fixes the old
`ModuleNotFoundError: No module named 'custom'`:

    python3 -m custom.plugins.studiowright image --prompt "a red fox" --output out/fox
    python3 -m custom.plugins.studiowright video --prompt "drone shot" --plan
    python3 -m custom.plugins.studiowright music --prompt "lofi" --dry-run

    make -f custom/Makefile flow ARGS="image --help"

Every run prints ONE standard status object and exits 0 (ok/dry_run/plan) or 1
(error):

    {"status": "ok", "tool": "flow_image", "output": "<path>", "files": [...],
     "attempts": 1, "duration_seconds": 1.2, "command": [...], ...}

`--plan` prints the `sw` argv and runs nothing: no browser, no credits, works
with `sw` absent. `--dry-run` lets the tool dry-run itself (client-side for
image/music; video asks Flow for the credit quote and submits nothing).

A live run (no `--plan`, no `--dry-run`) first passes the loopback CDP
preflight in `custom/tools/common/chrome_cdp.py`: when `CHROME_CDP_URL` — or the
local default `http://127.0.0.1:9222` — does not answer, the preflight starts
`chrome-cdp.service` once, waits ~3 s and probes again. Still dead is a
non-zero `status: "error"` naming the redacted endpoint. A non-loopback
`CHROME_CDP_URL` is never "fixed" by starting the local unit.

Retries only happen on a timeout — matching each tool's
RetryPolicy(retryable_errors=["timeout"]). A retry re-submits a job, which can
spend credits twice, so the default is 0.
"""

from __future__ import annotations

import argparse
import json
import time
from importlib import import_module
from pathlib import Path
from typing import Any

# pi-lens-ignore: reportMissingImports -- resolved from the repo root, like every entry here
from custom.tools.common.chrome_cdp import ensure_cdp

# tool name -> "module:class". Imported lazily so `--help` works even when an
# optional dependency of one tool is missing.
_TOOLS = {
    "image": "custom.tools.image.flow_image:FlowImage",
    "video": "custom.tools.video.flow_video:FlowVideo",
    "music": "custom.tools.audio.flow_music:FlowMusic",
}


def _tool(name: str):
    module, cls = _TOOLS[name].split(":")
    return getattr(import_module(module), cls)()


def _common(parser: argparse.ArgumentParser, *, require_prompt: bool = True) -> None:
    parser.add_argument("--prompt", required=require_prompt,
                        help="generation prompt (music: required for create/cover, not for edits)")
    parser.add_argument("--output", default=None,
                        help="output directory (default: out/flow_<tool>)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="seconds before the sw call is abandoned")
    parser.add_argument("--retries", type=int, default=0,
                        help="extra attempts after a timeout (re-submits: may spend credits)")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="dry run: no submission, no credits")
    parser.add_argument("--plan", action="store_true",
                        help="print the sw command and exit (runs nothing)")
    parser.add_argument("--profile", default=None,
                        help="StudioWright Chrome profile (multi-account)")


def _image_parser(sub: argparse.ArgumentParser) -> None:
    _common(sub)
    sub.add_argument("--model", default=None,
                     help="nano-banana-2.1 (default) | nano-banana-pro | nano-banana-2-lite")
    sub.add_argument("--aspect", default=None, help="16:9 (default) | 4:3 | 1:1 | 3:4 | 9:16")
    sub.add_argument("--count", type=int, default=None, help="1-4 images (default 1)")
    sub.add_argument("--size", default=None, help="download size label: 1K | 2K (default) | 4K")
    sub.add_argument("--ref", action="append", default=None, dest="refs",
                     help="reference image path or Flow media id (repeatable)")


def _video_parser(sub: argparse.ArgumentParser) -> None:
    _common(sub)
    sub.add_argument("--model", default=None,
                     help="omni-flash (default) | omni-flash-1.2 | veo-lite | veo-fast | veo-quality")
    sub.add_argument("--duration", type=int, default=None, help="seconds (default 8)")
    sub.add_argument("--aspect", default=None, help="16:9 (default) | 9:16")
    sub.add_argument("--resolution", default=None, help="360p | 720p (default)")
    sub.add_argument("--size", default=None, help="download size label: 720p | 1080p (default) | 4K")
    sub.add_argument("--start", default=None, dest="start_frame", help="start frame path or media id")
    sub.add_argument("--end", default=None, dest="end_frame", help="end frame path or media id")
    sub.add_argument("--ref", action="append", default=None, dest="refs",
                     help="ingredient image path or media id (repeatable, switches to ingredients mode)")
    sub.add_argument("--max-credits", type=int, default=None, help="refuse if Flow's price is higher")


def _music_parser(sub: argparse.ArgumentParser) -> None:
    _common(sub, require_prompt=False)
    sub.add_argument("--action", default=None,
                     help="create (default) | extend | cover | replace | variation | trim | stems")
    sub.add_argument("--song-id", default=None, dest="song_id",
                     help="Flow Music song id, URL or 'last' (edit actions)")
    sub.add_argument("--model", default=None, help="lyria-3.5 (default) | lyria-3-pro")
    sub.add_argument("--lyrics", default=None, help="lyrics text")
    sub.add_argument("--lyrics-file", default=None, dest="lyrics_file", help="lyrics file path")
    sub.add_argument("--length", default=None, help="M:SS, 1:00-3:00 (default 1:30)")
    sub.add_argument("--bpm", type=int, default=None, help="tempo (default: Flow decides)")
    sub.add_argument("--title", default=None, help="song title")
    sub.add_argument("--format", default=None, dest="fmt", help="mp3 (default) | wav | m4a")
    sub.add_argument("--start", default=None, help="trim/replace start (M:SS)")
    sub.add_argument("--end", default=None, help="trim/replace end (M:SS)")


def _image_inputs(args: argparse.Namespace) -> dict[str, Any]:
    extra = {"model": args.model, "aspect_ratio": args.aspect, "count": args.count,
             "size": args.size, "ref_paths": args.refs}
    return _with_common(args, extra)


def _video_inputs(args: argparse.Namespace) -> dict[str, Any]:
    extra = {"model": args.model, "duration": args.duration, "aspect_ratio": args.aspect,
             "resolution": args.resolution, "size": args.size, "start_frame": args.start_frame,
             "end_frame": args.end_frame, "ref_paths": args.refs, "max_credits": args.max_credits}
    return _with_common(args, extra)


def _music_inputs(args: argparse.Namespace) -> dict[str, Any]:
    extra = {"action": args.action, "song_id": args.song_id, "model": args.model,
             "lyrics": args.lyrics, "lyrics_file": args.lyrics_file, "length": args.length,
             "bpm": args.bpm, "title": args.title, "format": args.fmt,
             "start": args.start, "end": args.end}
    return _with_common(args, extra)


def _with_common(args: argparse.Namespace, extra: dict[str, Any]) -> dict[str, Any]:
    inputs = {k: v for k, v in extra.items() if v is not None}
    inputs["dry_run"] = bool(args.dry_run)
    if args.output:
        inputs["output_path"] = args.output
    if args.timeout:
        inputs["timeout_seconds"] = args.timeout
    if args.profile:
        inputs["profile"] = args.profile
    if args.prompt:
        inputs["prompt"] = args.prompt
    return inputs


_BUILDERS = {"image": _image_inputs, "video": _video_inputs, "music": _music_inputs}
_SCHEMAS = {"image": _image_parser, "video": _video_parser, "music": _music_parser}


def _status(status: str, tool, inputs: dict[str, Any], **fields: Any) -> dict[str, Any]:
    report = {
        "status": status,
        "tool": tool.name,
        "provider": tool.provider,
        "dry_run": bool(inputs.get("dry_run")),
        "command": fields.pop("command", None),
    }
    report.update(fields)
    return report


def _emit(report: dict[str, Any]) -> int:
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["status"] in ("ok", "dry_run", "plan") else 1


def run(name: str, args: argparse.Namespace) -> int:
    tool = _tool(name)
    inputs = _BUILDERS[name](args)

    if args.plan:
        return _emit(_status("plan", tool, inputs, command=tool.build_command(inputs)))

    # Live runs drive a browser over CDP. --plan/--dry-run never reach this, and
    # a non-loopback CHROME_CDP_URL is reported instead of being started locally.
    if not args.dry_run:
        cdp = ensure_cdp()
        if not cdp.ok:
            return _emit(_status("error", tool, inputs, error=cdp.error, cdp=cdp.as_dict()))

    attempts = 0
    started = time.time()
    while True:
        attempts += 1
        # pi-lens-ignore: python-sql-injection -- BaseTool.execute, not a SQL cursor
        result = tool.execute(inputs)
        # Retry only on a timeout, matching the tool's RetryPolicy.
        timed_out = "timed out" in (result.error or "").lower()
        if result.success or not timed_out or attempts > args.retries:
            break

    command = result.data.get("command")
    if not result.success:
        return _emit(_status("error", tool, inputs, attempts=attempts, error=result.error,
                             duration_seconds=round(time.time() - started, 2), command=command))

    files = [p for p in (result.artifacts or []) if Path(p).is_file()]
    dry_run = bool(result.data.get("dry_run"))
    if not dry_run and not files:
        return _emit(_status("error", tool, inputs, attempts=attempts,
                             error="tool reported success but wrote no media file on disk",
                             duration_seconds=round(time.time() - started, 2), command=command))

    return _emit(_status("dry_run" if dry_run else "ok", tool, inputs, attempts=attempts,
                         output=files[0] if files else None, files=files,
                         model=result.model, recovered=bool(result.data.get("recovered")),
                         duration_seconds=round(time.time() - started, 2),
                         detail=result.data, command=command))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m custom.plugins.studiowright",
        description="Google Flow image/video/music generation through the StudioWright ('sw') CLI. "
                    "Run from the repository root.",
        epilog="Examples: image --prompt 'a red fox' --output out/fox | "
               "video --prompt 'drone shot' --duration 6 | music --prompt 'lofi' --length 1:30",
    )
    subs = parser.add_subparsers(dest="tool", required=True, metavar="{image,video,music}")
    for name, schema in _SCHEMAS.items():
        sub = subs.add_parser(name, help=f"generate {name} via Google Flow")
        schema(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args.tool, args)


if __name__ == "__main__":
    raise SystemExit(main())
