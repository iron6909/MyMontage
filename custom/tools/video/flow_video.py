"""Generate video via Google Flow (Omni Flash / Veo) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login
3. Keep Chrome running:    sw window hide  (never minimize)

`sw` reaches that Chrome on its own loopback port (9300 + crc32(profile) % 90), so
every call hands it `custom.tools.common.proxy_env.sw_env()`: the full parent env
with loopback kept out of `HTTP(S)_PROXY`.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

# pi-lens-ignore: reportMissingImports -- resolved from the repo root, like every entry here
from custom.plugins.studiowright.client import (
    output_files,
    parse_sw_json,
    recover_download,
)

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
from custom.tools.common.proxy_env import sw_env

# pi-lens-ignore: reportMissingImports -- resolved from the repo root
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
    return Path(inputs.get("output_path") or "out/flow_video")


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
            "timeout_seconds": {
                "type": "number",
                "description": "Abandon the sw call after this many seconds (default: 2x the model estimate).",
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

    def build_command(self, inputs: dict[str, Any]) -> list[str]:
        """The exact `sw` argv for these inputs (used by --plan and by execute)."""
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        cmd += [
            "video", inputs["prompt"],
            "--model", inputs.get("model", "omni-flash"),
            "--duration", str(inputs.get("duration", 8)),
            "--aspect", inputs.get("aspect_ratio", "16:9"),
            "--resolution", inputs.get("resolution", "720p"),
            "--size", inputs.get("size", "1080p"),
            "--out", str(_out_dir(inputs)),
            "--json",
        ]
        if inputs.get("start_frame"):
            cmd += ["--start", inputs["start_frame"]]
        if inputs.get("end_frame"):
            cmd += ["--end", inputs["end_frame"]]
        ref_paths = inputs.get("ref_paths") or []
        if ref_paths:
            cmd += ["--mode", "ingredients"]
            for ref in ref_paths:
                cmd += ["--ref", ref]
        if inputs.get("dry_run"):
            cmd.append("--dry-run")
        if inputs.get("max_credits") is not None:
            # sw --max-credits is INTEGER; a float would be rejected by click.
            cmd += ["--max-credits", str(int(inputs["max_credits"]))]
        return cmd

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs["prompt"]
        model = inputs.get("model", "omni-flash")
        duration = inputs.get("duration", 8)
        aspect = inputs.get("aspect_ratio", "16:9")
        resolution = inputs.get("resolution", "720p")
        size = inputs.get("size", "1080p")
        dry_run = inputs.get("dry_run", False)
        profile = inputs.get("profile")

        sw = _sw_binary()
        if not sw:
            return ToolResult(
                success=False,
                error="StudioWright (sw) not found. " + self.install_instructions,
            )

        cmd = self.build_command(inputs)
        out_dir = _out_dir(inputs)
        out_dir.mkdir(parents=True, exist_ok=True)

        timeout_s = float(
            inputs.get("timeout_seconds")
            or max(300, int(self.estimate_runtime(inputs)) * 2)
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
                env=sw_env(),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"flow_video timed out after {timeout_s:.0f}s. "
                      "Chrome may be minimized — use `sw window hide` instead.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"flow_video failed to start: {exc}")

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            # The clip is already generated; only sw's inline download failed.
            files = [] if dry_run else recover_download(sw, "video", start, out_dir, size, profile)
            if files:
                return ToolResult(
                    success=True,
                    data={
                        "provider": "google_flow",
                        "model": model,
                        "prompt": prompt,
                        "duration_seconds": duration,
                        "aspect_ratio": aspect,
                        "resolution": resolution,
                        "output": files[0],
                        "dry_run": False,
                        "recovered": True,
                        "cost_status": "flow_credits",
                    },
                    artifacts=files,
                    cost_usd=None,
                    duration_seconds=round(time.time() - start, 2),
                    model=model,
                )
            return ToolResult(
                success=False,
                error=f"sw video exited {result.returncode}: {stderr or stdout}",
            )

        data = parse_sw_json(stdout)

        output_file: str | None = None
        if not dry_run:
            mp4s = output_files(data, out_dir, ("*.mp4",))
            output_file = mp4s[0] if mp4s else None

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
