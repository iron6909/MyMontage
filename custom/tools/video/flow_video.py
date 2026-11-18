"""Generate video via Google Flow (Omni Flash / Veo) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login
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
        "  uv tool install git+https://github.com/umutkocak/studiowright\n"
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
                "enum": [4, 6, 8, 10],
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
                "enum": ["720p", "1080p", "4K", "270p"],
                "default": "1080p",
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
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Show credit cost without submitting.",
            },
            "max_credits": {
                "type": "number",
                "description": "Refuse if estimated cost exceeds this many credits.",
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
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
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

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        sw = _sw_binary()
        if not sw:
            return ToolResult(
                success=False,
                error="StudioWright (sw) not found. " + self.install_instructions,
            )

        prompt = inputs["prompt"]
        model = inputs.get("model", "omni-flash")
        duration = inputs.get("duration", 8)
        aspect = inputs.get("aspect_ratio", "16:9")
        resolution = inputs.get("resolution", "720p")
        size = inputs.get("size", "1080p")
        dry_run = inputs.get("dry_run", False)
        max_credits = inputs.get("max_credits")
        start_frame = inputs.get("start_frame")
        end_frame = inputs.get("end_frame")
        ref_paths: list[str] = inputs.get("ref_paths") or []
        profile = inputs.get("profile")

        out_dir = Path(inputs.get("output_path") or "out/flow_video")
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [sw]
        if profile:
            cmd += ["--profile", profile]
        cmd += [
            "video", prompt,
            "--model", model,
            "--duration", str(duration),
            "--aspect", aspect,
            "--resolution", resolution,
            "--size", size,
            "--out", str(out_dir),
            "--json",
        ]
        if start_frame:
            cmd += ["--start", start_frame]
        if end_frame:
            cmd += ["--end", end_frame]
        for ref in ref_paths:
            cmd += ["--ref", ref]
        if dry_run:
            cmd.append("--dry-run")
        if max_credits is not None:
            cmd += ["--max-credits", str(max_credits)]

        timeout_s = max(300, int(self.estimate_runtime(inputs)) * 2)
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
                error=f"flow_video timed out after {timeout_s}s. "
                      "Chrome may be minimized — use `sw window hide` instead.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"flow_video failed to start: {exc}")

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            return ToolResult(
                success=False,
                error=f"sw video exited {result.returncode}: {stderr or stdout}",
            )

        data: dict = {}
        for line in stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

        output_file: str | None = data.get("output") or data.get("path")
        if not output_file and not dry_run:
            # Fallback: newest mp4 in out_dir
            mp4s = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
            output_file = str(mp4s[-1]) if mp4s else None

        if not output_file and not dry_run:
            return ToolResult(
                success=False,
                error=f"flow_video returned no output file. stdout={stdout!r}",
            )

        duration_elapsed = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "provider": "google_flow",
                "model": model,
                "prompt": prompt,
                "duration_seconds": duration,
                "aspect_ratio": aspect,
                "resolution": resolution,
                "output": output_file,
                "dry_run": dry_run,
                "sw_data": data,
                "cost_status": "flow_credits",
            },
            artifacts=[output_file] if output_file else [],
            cost_usd=None,
            duration_seconds=duration_elapsed,
            model=model,
        )
