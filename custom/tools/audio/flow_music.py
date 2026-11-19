"""Generate and edit music via Google Flow Music (Lyria) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow Music subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login --url https://www.flowmusic.app/
3. Keep Chrome running:    sw window hide  (never minimize)

Entry point (from the repository root):
    python3 -m custom.plugins.studiowright music --help
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from custom.plugins.studiowright.client import output_files, parse_sw_json
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
    return Path(inputs.get("output_path") or "out/flow_music")


def _mm_ss_to_seconds(ts: str) -> int:
    """Convert 'M:SS' string to total seconds, e.g. '1:30' -> 90."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return int(ts)


def _edit_flags(action: str, inputs: dict[str, Any]) -> list[str]:
    """The flags one `sw music <edit>` subcommand accepts, in click's order."""
    if action in _NEEDS_PROMPT:
        return ["--prompt", inputs["prompt"]]
    if action in _NEEDS_RANGE:
        flags = ["--start", inputs["start"], "--end", inputs["end"]]
        return flags + (["--prompt", inputs["prompt"]] if inputs.get("prompt") else [])
    return ["--prompt", inputs["prompt"]] if inputs.get("prompt") else []


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
        "  uv tool install git+https://github.com/umutkocak/studiowright\n"
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
        "required": ["action"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "Music style / mood description (e.g. 'moody indie folk, warm male vocal'). "
                    "Required for action=create and action=cover; unused by the other edits."
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
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
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
        """The exact `sw` argv for these inputs (used by --plan and by execute)."""
        action = inputs.get("action", "create")
        song = inputs.get("song_id") or "last"
        if action == "create":
            head = ["music", inputs["prompt"],
                    "--model", inputs.get("model", "lyria-3.5"),
                    "--length", inputs.get("length", "1:30")]
        elif action == "stems":
            head = ["music", "stems", song]
        else:
            head = ["music", action, song, *_edit_flags(action, inputs)]
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        return cmd + head + [
            "--format", inputs.get("format", "mp3"),
            *_text_flags(action, inputs),
            "--out", str(_out_dir(inputs)),
            "--json",
        ]

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        action = inputs.get("action", "create")
        prompt = inputs.get("prompt")
        song_id = inputs.get("song_id")
        model = inputs.get("model", "lyria-3.5")
        fmt = inputs.get("format", "mp3")

        if action in _NEEDS_PROMPT and not prompt:
            return ToolResult(success=False, error=f"action={action} needs a prompt.")
        if action in _NEEDS_RANGE and not (inputs.get("start") and inputs.get("end")):
            return ToolResult(
                success=False,
                error=f"action={action} needs both start and end (M:SS), e.g. --start 0:05 --end 0:35.",
            )
        if action != "create" and action not in _EDIT_ACTIONS and action != "stems":
            return ToolResult(success=False, error=f"unknown action {action!r}.")

        cmd = self.build_command(inputs)

        # sw music has no remote dry-run pricing; handle client-side.
        if inputs.get("dry_run"):
            return ToolResult(
                success=True,
                data={
                    "provider": "google_flow_music",
                    "model": model,
                    "prompt": prompt,
                    "action": action,
                    "song_id": song_id or ("last" if action != "create" else None),
                    "format": fmt,
                    "dry_run": True,
                    "command": cmd,
                    "message": "Preflight check passed.",
                },
                cost_usd=None,
                duration_seconds=0.0,
                model=model,
            )

        sw = _sw_binary()
        if not sw:
            return ToolResult(
                success=False,
                error="StudioWright (sw) not found. " + self.install_instructions,
            )

        out_dir = _out_dir(inputs)
        out_dir.mkdir(parents=True, exist_ok=True)

        timeout_s = float(
            inputs.get("timeout_seconds") or max(180, int(self.estimate_runtime(inputs)) * 3)
        )
        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"flow_music timed out after {timeout_s:.0f}s. "
                      "Chrome may be minimized — use `sw window hide` instead.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"flow_music failed to start: {exc}")

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            return ToolResult(
                success=False,
                error=f"sw music exited {result.returncode}: {stderr or stdout}",
            )

        data = parse_sw_json(stdout)
        outputs = output_files(data, out_dir, ("*.mp3", "*.wav", "*.m4a"))

        if not outputs:
            return ToolResult(
                success=False,
                error=f"flow_music returned no output files. stdout={stdout!r}",
            )

        duration_elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "provider": "google_flow_music",
                "model": model,
                "prompt": prompt,
                "action": action,
                "length": inputs.get("length", "1:30"),
                "format": fmt,
                "output": outputs[0],
                "outputs": outputs,
                "sw_data": data,
                "cost_status": "flow_credits",
                "command": cmd,
            },
            artifacts=outputs,
            cost_usd=None,
            duration_seconds=duration_elapsed,
            model=model,
        )
