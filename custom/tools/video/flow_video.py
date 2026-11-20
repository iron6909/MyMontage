"""Generate video via Google Flow (Omni Flash / Veo) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   make -f custom/Makefile sw-install
2. Authenticate once:      sw auth login
3. Keep Chrome running:    sw window hide  (never minimize)

`sw` reaches that Chrome on its own loopback port (9300 + crc32(profile) % 90), so
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


def _sw_binary() -> str | None:
    return shutil.which("sw")


def _out_dir(inputs: dict[str, Any]) -> Path:
    return Path(inputs.get("output_path") or "out/flow_video").expanduser()


class FlowVideo(BaseTool):
    """Google Flow video generation (Omni Flash / Veo 3.1) via StudioWright CLI."""

    name = "flow_video"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "video_generation"
    provider = "google_flow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []
    install_instructions = (
        "Install StudioWright and authenticate:\n"
        "  make -f custom/Makefile sw-install\n"
        "  sw auth login\n"
        "  sw window hide          # keep Chrome running in background\n"
        "Requires a Google account with an active Flow plan."
    )
    agent_skills = ["ai-video-gen"]

    capabilities = [
        "text_to_video",
        "image_to_video",
        "first_last_frame_to_video",
        "reference_to_video",
    ]
    supports = {
        "text_to_video": True,
        "image_to_video": True,
        "first_last_frame_to_video": True,
        "reference_to_video": True,
        "aspect_ratio": True,
        "duration_control": True,
        "dry_run": True,
        "no_api_key": True,
    }
    best_for = [
        "Omni 1.1/1.2 Flash and Veo 3.1 Lite/Fast/Quality via your Google Flow subscription",
        "image-to-video with start and optional end frames",
        "zero-API-key access to Google Flow video models",
        "dry-run credit checks before submitting",
    ]
    not_good_for = [
        "headless / server environments (needs a running Chrome)",
        "fast iteration (30–120 s per clip)",
        "accounts without an active Google Flow plan",
    ]
    fallback_tools = ["veo_video", "gemini_omni_video", "kling_video"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Video generation prompt.",
            },
            "model": {
                "type": "string",
                "enum": [
                    "omni-flash",       # Omni 1.1 Flash (default)
                    "omni-flash-1.2",   # Omni 1.2 Flash
                    "veo-lite",         # Veo 3.1 Lite
                    "veo-fast",         # Veo 3.1 Fast (retires 2026-12-05)
                    "veo-quality",      # Veo 3.1 Quality (retires 2026-12-05)
                ],
                "default": "omni-flash",
            },
            "duration": {
                "type": "integer",
                "minimum": 3,
                "maximum": 15,
                "default": 8,
                "description": "Duration in seconds (Omni 1.1: 4/6/8/10; Omni 1.2: 3–15).",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "9:16"],
                "default": "16:9",
            },
            "resolution": {
                "type": "string",
                "enum": ["360p", "720p"],
                "default": "720p",
                "description": "Generation resolution (download can be upscaled to 1080p/4K).",
            },
            "size": {
                "type": "string",
                "enum": ["original", "720p", "1080p", "4K", "270p"],
                "default": "original",
                "description": "Download size (Flow upscales from generation resolution).",
            },
            "start_frame": {
                "type": "string",
                "description": "Path or Flow media ID to use as start frame.",
            },
            "end_frame": {
                "type": "string",
                "description": "Path or Flow media ID to use as end frame.",
            },
            "ref_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Reference image paths / media IDs (ingredients mode).",
            },
            "output_path": {
                "type": "string",
                "description": "Directory where video will be saved (default: out/flow_video).",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "StudioWright subprocess time limit in seconds, including downloads.",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Show credit cost without submitting.",
            },
            "max_credits": {
                "type": "number",
                "description": "Refuse if estimated cost exceeds this many credits.",
            },
            "project": {"type": "string", "description": "Google Flow project ID."},
            "profile": {
                "type": "string",
                "description": "StudioWright profile name (multi-account).",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "output": {"type": "string", "description": "Path to generated video file."},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "duration_seconds": {"type": "integer"},
            "aspect_ratio": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=500, network_required=True
    )
    retry_policy = RetryPolicy()
    idempotency_key_fields = ["prompt", "model", "duration", "aspect_ratio", "start_frame"]
    side_effects = [
        "opens/uses Google Chrome browser session",
        "consumes Google Flow credits",
        "writes video file to output directory",
    ]
    user_visible_verification = [
        "Review generated video for quality and prompt adherence.",
        "Check credit cost with --dry-run before submitting expensive models.",
    ]

    latency_p50_seconds = 60.0

    def get_status(self) -> ToolStatus:
        if not _sw_binary():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # Flow credits, not USD

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        model = inputs.get("model", "omni-flash")
        if "quality" in model:
            return 120.0
        if "fast" in model:
            return 90.0
        if "lite" in model:
            return 75.0
        return 60.0  # omni-flash default

    def build_command(self, inputs: dict[str, Any]) -> list[str]:
        prompt = prompt_text(inputs)
        frames = [inputs.get("start_frame"), inputs.get("end_frame")]
        refs = inputs.get("ref_paths") or []
        if frames[1] and not frames[0]:
            raise ValueError("an end frame needs a start frame")
        if any(frames) and refs:
            raise ValueError("choose either start/end frames or ingredient reference images")
        model = inputs.get("model", "omni-flash")
        duration = inputs.get("duration", 8)
        if type(duration) is not int or not 3 <= duration <= 15:
            raise ValueError("duration must be an integer from 3 through 15")
        if model == "omni-flash" and duration not in (4, 6, 8, 10):
            raise ValueError("Omni 1.1 supports 4, 6, 8 or 10 seconds")
        size = inputs.get("size", "original")
        if size not in ("original", "720p", "1080p", "4K", "270p"):
            raise ValueError("video size must be original, 720p, 1080p, 4K or 270p")
        cap = inputs.get("max_credits")
        if cap is not None and (type(cap) not in (int, float) or cap < 0 or cap != int(cap)):
            raise ValueError("max_credits must be a nonnegative integer")
        timeout_seconds(inputs, 720)
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        cmd += [
            "video", "--model", model, "--duration", str(duration),
            "--aspect", inputs.get("aspect_ratio", "16:9"),
            "--resolution", inputs.get("resolution", "720p"),
            "--out", str(_out_dir(inputs)), "--json",
        ]
        if size != "original":
            cmd += ["--size", size]
        if inputs.get("project"):
            cmd += ["--project", inputs["project"]]
        for flag, frame in zip(("--start", "--end"), frames):
            if frame:
                cmd += [flag, frame]
        if refs:
            cmd += ["--mode", "ingredients"]
            for ref in refs:
                cmd += ["--ref", ref]
        if inputs.get("dry_run"):
            cmd.append("--dry-run")
        if cap is not None:
            cmd += ["--max-credits", str(int(cap))]
        return prompt_command(cmd, prompt)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            cmd = self.build_command(inputs)
            sized = inputs.get("size", "original") != "original"
            default = 90 if inputs.get("dry_run") else (2100 if sized else 840)
            timeout = timeout_seconds(inputs, default)
        except (TypeError, ValueError, OverflowError) as exc:
            return ToolResult(success=False, error=str(exc))
        if cmd[0] == "sw":
            return ToolResult(success=False, error="StudioWright (sw) not found. " + self.install_instructions)
        return invoke(
            cmd, kind="video", inputs=inputs, output=_out_dir(inputs), timeout=timeout,
            metadata={
                "provider": self.provider, "model": inputs.get("model", "omni-flash"),
                "prompt": inputs["prompt"], "duration_seconds": inputs.get("duration", 8),
                "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
                "resolution": inputs.get("resolution", "720p"),
            },
        )
