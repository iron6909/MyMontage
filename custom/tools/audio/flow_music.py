"""Generate and edit music via Google Flow Music (Lyria) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow Music subscription.

Prerequisites
-------------
1. Install StudioWright:   make -f custom/Makefile sw-install
2. Authenticate once:      sw auth login --url https://www.flowmusic.app/
3. Keep Chrome running:    sw window hide  (never minimize)

Entry point (from the repository root):
    python3 -m custom.plugins.studiowright music --help

`sw` reaches its Chrome on its own loopback port (9300 + crc32(profile) % 90), so
every call hands it `custom.tools.common.proxy_env.sw_env()`: the full parent env
with loopback kept out of `HTTP(S)_PROXY`.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

# pi-lens-ignore: reportMissingImports -- resolved from the repo root, like every entry here
from custom.plugins.studiowright.client import (
    invoke,
    prompt_command,
    prompt_text,
    timeout_seconds,
)
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

# `sw music <action>`: create takes the sound prompt, the rest edit a song.
_EDIT_ACTIONS = ("extend", "cover", "variation", "trim", "replace")
_NEEDS_PROMPT = ("create", "cover")
_NEEDS_RANGE = ("trim", "replace")
# Only these actions take --lyrics / --lyrics-file / --title (stems, variation
# and trim do not; an unknown flag is a click UsageError).
_TEXT_FLAGS = ("create", "extend", "cover", "replace")


def _sw_binary() -> str | None:
    return shutil.which("sw")


def _out_dir(inputs: dict[str, Any]) -> Path:
    return Path(inputs.get("output_path") or "out/flow_music").expanduser()


def _mm_ss_to_seconds(ts: str) -> int:
    """Convert 'M:SS' string to total seconds, e.g. '1:30' -> 90."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = map(int, parts)
        if minutes < 0 or not 0 <= seconds < 60:
            raise ValueError(f"invalid music time {ts!r}; use M:SS or seconds")
        return minutes * 60 + seconds
    return int(ts)


def _edit_flags(action: str, inputs: dict[str, Any]) -> list[str]:
    """The flags one `sw music <edit>` subcommand accepts, in click's order."""
    flags = []
    if action in _NEEDS_RANGE:
        flags += ["--start", inputs["start"], "--end", inputs["end"]]
    if action in ("cover", "extend", "replace") and inputs.get("prompt"):
        flags += ["--prompt", inputs["prompt"]]
    return flags


def _text_flags(action: str, inputs: dict[str, Any]) -> list[str]:
    """--lyrics / --lyrics-file / --title / --bpm where sw accepts them."""
    if action not in _TEXT_FLAGS:
        return []
    flags: list[str] = []
    for flag, key in (("--lyrics", "lyrics"), ("--lyrics-file", "lyrics_file"), ("--title", "title")):
        if inputs.get(key):
            flags += [flag, inputs[key]]
    if action == "create" and inputs.get("bpm"):
        flags += ["--bpm", str(int(inputs["bpm"]))]
    return flags


class FlowMusic(BaseTool):
    """Google Flow Music (Lyria 3.5 / 3 Pro) generation via StudioWright CLI."""

    name = "flow_music"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "google_flow_music"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Install StudioWright and authenticate for Flow Music:\n"
        "  make -f custom/Makefile sw-install\n"
        "  sw auth login --url https://www.flowmusic.app/\n"
        "  sw window hide          # keep Chrome running in background\n"
        "Requires a Google account with an active Flow Music plan."
    )
    agent_skills = ["lyria", "acestep"]

    capabilities = [
        "generate_background_music",
        "generate_song_with_lyrics",
        "instrumental",
        "edit_song",
        "extend_song",
        "stems_extraction",
        "cover_song",
    ]
    supports = {
        "instrumental": True,
        "vocals": True,
        "custom_lyrics": True,
        "style_control": True,
        "bpm_control": True,
        "length_control": True,
        "stem_separation": True,
        "extend": True,
        "cover": True,
        "variation": True,
        "trim": True,
        "dry_run": True,
        "no_api_key": True,
        "wav_output": True,
        "mp3_output": True,
    }
    best_for = [
        "Lyria 3.5 and Lyria 3 Pro music via your own Google Flow Music subscription",
        "songs with custom lyrics (any language) or instrumentals",
        "stem extraction (drums, bass, vocals, other)",
        "zero-API-key access to Google's Lyria music models",
        "conversational music production via sw music chat",
    ]
    not_good_for = [
        "headless / server environments (needs a running Chrome)",
        "sub-60-second output (minimum length is 1:00)",
        "accounts without an active Google Flow Music plan",
    ]
    fallback_tools = ["google_music", "elevenlabs_soundtrack", "comfyui_music"]

    input_schema = {
        "type": "object",

        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Music style / mood description (e.g. 'moody indie folk, warm male vocal'). "
                    "Required for create and cover; optional for extend and replace."
                ),
            },
            "action": {
                "type": "string",
                "enum": ["create", "extend", "cover", "replace", "variation", "trim", "stems"],
                "default": "create",
                "description": (
                    "create: generate a new song. "
                    "extend/cover/replace/variation/trim/stems: edit an existing song (requires song_id)."
                ),
            },
            "song_id": {
                "type": "string",
                "description": "Flow Music song ID, URL, or 'last'. Required for edit actions.",
            },
            "model": {
                "type": "string",
                "enum": ["lyria-3.5", "lyria-3-pro"],
                "default": "lyria-3.5",
            },
            "lyrics": {
                "type": "string",
                "description": "Lyrics text (omit for instrumental).",
            },
            "lyrics_file": {
                "type": "string",
                "description": "Path to a lyrics text file.",
            },
            "length": {
                "type": "string",
                "default": "1:30",
                "description": "Song length in M:SS format (1:00–3:00).",
            },
            "bpm": {
                "type": "integer",
                "description": "Beats per minute.",
            },
            "title": {
                "type": "string",
                "description": "Song title shown in Flow Music.",
            },
            "start": {
                "type": "string",
                "description": "trim/replace: first second to keep or replace (M:SS).",
            },
            "end": {
                "type": "string",
                "description": "trim/replace: last second to keep or replace (M:SS).",
            },
            "format": {
                "type": "string",
                "enum": ["mp3", "wav", "m4a"],
                "default": "mp3",
            },
            "output_path": {
                "type": "string",
                "description": "Directory where audio will be saved (default: out/flow_music).",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Abandon the sw call after this many seconds (default: 3x the estimate).",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Preflight check, client-side: sw music has no remote pricing call.",
            },
            "profile": {
                "type": "string",
                "description": "StudioWright profile name (multi-account).",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "Path to generated audio file."},
            "outputs": {"type": "array", "items": {"type": "string"},
                        "description": "All output files (stems: multiple files)."},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "length": {"type": "string"},
            "format": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100, network_required=True
    )
    retry_policy = RetryPolicy()
    idempotency_key_fields = ["prompt", "model", "length", "lyrics", "action"]
    side_effects = [
        "opens/uses Google Chrome browser session",
        "consumes Google Flow Music credits",
        "writes audio file(s) to output directory",
    ]
    user_visible_verification = [
        "Listen to generated track for quality and style adherence.",
    ]

    latency_p50_seconds = 45.0

    def get_status(self) -> ToolStatus:
        if not _sw_binary():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # Flow Music credits, not USD

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        action = inputs.get("action", "create")
        if action in ("stems",):
            return 60.0
        length = inputs.get("length", "1:30")
        try:
            secs = _mm_ss_to_seconds(length)
        except (ValueError, TypeError):
            secs = 90
        # roughly: 30s overhead + length/4
        return 30.0 + secs / 4.0

    def build_command(self, inputs: dict[str, Any]) -> list[str]:
        action = inputs.get("action", "create")
        if action not in ("create", "stems", *_EDIT_ACTIONS):
            raise ValueError(f"unknown music action {action!r}")
        if action in _NEEDS_PROMPT:
            prompt_text(inputs)
        if action in _NEEDS_RANGE:
            if not inputs.get("start") or not inputs.get("end"):
                raise ValueError(f"action={action} needs both start and end, such as 0:05 and 0:35")
            start = _mm_ss_to_seconds(inputs["start"])
            end = _mm_ss_to_seconds(inputs["end"])
            if start < 0 or end <= start:
                raise ValueError("the end time must be after a nonnegative start time")
        fmt = inputs.get("format", "mp3")
        if fmt not in ("mp3", "wav", "m4a"):
            raise ValueError("music format must be mp3, wav or m4a")
        timeout_seconds(inputs, 1260)
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        cmd += ["music", action, "--format", fmt, *_text_flags(action, inputs),
                "--out", str(_out_dir(inputs)), "--json"]
        if action == "create":
            length = inputs.get("length", "1:30")
            if not 60 <= _mm_ss_to_seconds(length) <= 180:
                raise ValueError("music length must be between 1:00 and 3:00")
            cmd += ["--model", inputs.get("model", "lyria-3.5"), "--length", length]
            return prompt_command(cmd, inputs["prompt"])
        cmd += _edit_flags(action, inputs)
        return prompt_command(cmd, inputs.get("song_id") or "last")

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            cmd = self.build_command(inputs)
            action = inputs.get("action", "create")
            timeout = timeout_seconds(inputs, 1980 if action == "stems" else 1380)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
        model = inputs.get("model", "lyria-3.5")
        metadata = {
            "provider": self.provider, "model": model, "prompt": inputs.get("prompt"),
            "action": action, "song_id": inputs.get("song_id") or ("last" if action != "create" else None),
            "length": inputs.get("length", "1:30"), "format": inputs.get("format", "mp3"),
        }
        if inputs.get("dry_run"):
            return ToolResult(
                success=True, data={**metadata, "dry_run": True, "command": cmd},
                cost_usd=None, model=model,
            )
        if cmd[0] == "sw":
            return ToolResult(success=False, error="StudioWright (sw) not found. " + self.install_instructions)
        return invoke(
            cmd, kind="music", inputs=inputs, output=_out_dir(inputs), timeout=timeout,
            metadata=metadata, expected=4 if action == "stems" else 1,
        )
