"""Generate and edit music via Google Flow Music (Lyria) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow Music subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login --url https://www.flowmusic.app/
3. Keep Chrome running:    sw window hide  (never minimize)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

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


def _sw_binary() -> str | None:
    return shutil.which("sw")


def _mm_ss_to_seconds(ts: str) -> int:
    """Convert 'M:SS' string to total seconds, e.g. '1:30' -> 90."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return int(ts)


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
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Music style / mood description (e.g. 'moody indie folk, warm male vocal').",
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
            "format": {
                "type": "string",
                "enum": ["mp3", "wav", "m4a"],
                "default": "mp3",
            },
            "output_path": {
                "type": "string",
                "description": "Directory where audio will be saved (default: out/flow_music).",
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

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        sw = _sw_binary()
        if not sw:
            return ToolResult(
                success=False,
                error="StudioWright (sw) not found. " + self.install_instructions,
            )

        prompt = inputs["prompt"]
        action = inputs.get("action", "create")
        song_id = inputs.get("song_id")
        model = inputs.get("model", "lyria-3.5")
        lyrics = inputs.get("lyrics")
        lyrics_file = inputs.get("lyrics_file")
        length = inputs.get("length", "1:30")
        bpm = inputs.get("bpm")
        title = inputs.get("title")
        fmt = inputs.get("format", "mp3")
        profile = inputs.get("profile")

        out_dir = Path(inputs.get("output_path") or "out/flow_music")
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build command
        cmd: list[str] = [sw]
        if profile:
            cmd += ["--profile", profile]

        if action == "create":
            cmd += ["music", prompt]
        elif action == "stems":
            cmd += ["music", "stems", song_id or "last"]
        elif action == "extend":
            cmd += ["music", "extend", song_id or "last"]
            if prompt:
                cmd += ["--prompt", prompt]
        elif action == "cover":
            cmd += ["music", "cover", song_id or "last", "--prompt", prompt]
        elif action == "variation":
            cmd += ["music", "variation", song_id or "last"]
        elif action == "trim":
            cmd += ["music", "trim", song_id or "last"]
        elif action == "replace":
            cmd += ["music", "replace", song_id or "last"]
            if prompt:
                cmd += ["--prompt", prompt]
        else:
            cmd += ["music", prompt]

        if action == "create":
            cmd += ["--model", model, "--length", length, "--format", fmt]
            if lyrics:
                cmd += ["--lyrics", lyrics]
            if lyrics_file:
                cmd += ["--lyrics-file", lyrics_file]
            if bpm:
                cmd += ["--bpm", str(bpm)]
            if title:
                cmd += ["--title", title]

        cmd += ["--out", str(out_dir), "--json"]

        timeout_s = max(180, int(self.estimate_runtime(inputs)) * 3)
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
                error=f"flow_music timed out after {timeout_s}s. "
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

        data: dict = {}
        for line in stdout.splitlines():
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                continue

        # Collect outputs
        outputs: list[str] = []
        if isinstance(data, dict) and isinstance(data.get("outputs"), list):
            outputs = [str(p) for p in data["outputs"]]
        elif isinstance(data, dict) and data.get("output"):
            outputs = [str(data["output"])]
        else:
            # Fallback glob
            for ext in ("*.mp3", "*.wav", "*.m4a"):
                outputs += sorted(str(p) for p in out_dir.glob(ext))

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
                "length": length,
                "format": fmt,
                "output": outputs[0],
                "outputs": outputs,
                "sw_data": data,
                "cost_status": "flow_credits",
            },
            artifacts=outputs,
            cost_usd=None,
            duration_seconds=duration_elapsed,
            model=model,
        )
