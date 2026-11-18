"""Generate images via Google Flow (Nano Banana) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login
3. Keep Chrome running:    sw window hide  (never minimize)
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from custom.tools._sw import output_files, parse_sw_json, recover_download
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
    """Return absolute path to the ``sw`` binary, or None if not installed."""
    return shutil.which("sw")


class FlowImage(BaseTool):
    """Google Flow image generation (Nano Banana) via StudioWright CLI."""

    name = "flow_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "google_flow"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API  # browser-driven, treated as external API

    dependencies = []  # sw binary checked dynamically
    install_instructions = (
        "Install StudioWright and authenticate:\n"
        "  uv tool install git+https://github.com/umutkocak/studiowright\n"
        "  sw auth login\n"
        "  sw window hide          # keep Chrome running in background\n"
        "Requires a Google account with an active Flow plan."
    )
    agent_skills = ["atlas-cloud"]  # closest skill covering Flow / Nano Banana

    capabilities = [
        "text_to_image",
        "image_to_image",
        "reference_image",
        "multi_reference",
        "aspect_ratio_control",
    ]
    supports = {
        "text_to_image": True,
        "image_to_image": True,
        "reference_image": True,
        "multiple_reference_images": True,
        "aspect_ratio": True,
        "batch": True,
        "no_api_key": True,
    }
    best_for = [
        "Nano Banana 2.1 / Pro images via your own Google Flow subscription",
        "zero-API-key access to Google's latest image models",
        "reference-conditioned style transfer with multiple refs",
        "2K / 4K upscaled downloads",
    ]
    not_good_for = [
        "headless / server environments (needs a running Chrome)",
        "fast iteration (≈20 s per image)",
        "accounts without an active Google Flow plan",
    ]
    fallback_tools = ["google_imagen", "grok_image", "flux_image"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Image generation prompt.",
            },
            "model": {
                "type": "string",
                "enum": ["nano-banana-2.1", "nano-banana-pro", "nano-banana-2-lite"],
                "default": "nano-banana-2.1",
                "description": "Nano Banana model variant.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": ["16:9", "4:3", "1:1", "3:4", "9:16"],
                "default": "16:9",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "default": 1,
                "description": "Number of images to generate (1–4).",
            },
            "size": {
                "type": "string",
                "enum": ["1K", "2K", "4K"],
                "default": "2K",
                "description": "Download resolution. 2K/4K are upscaled by Flow.",
            },
            "ref_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local file paths to use as reference images.",
            },
            "output_path": {
                "type": "string",
                "description": "Directory where images will be saved (default: out/flow_image).",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Preflight check (sw image has no remote pricing call; validates inputs only).",
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
            "output": {"type": "string", "description": "Path to first generated image."},
            "outputs": {"type": "array", "items": {"type": "string"}},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "aspect_ratio": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "model", "aspect_ratio", "count"]
    side_effects = [
        "opens/uses Google Chrome browser session",
        "consumes Google Flow credits",
        "writes image files to output directory",
    ]
    user_visible_verification = [
        "Inspect generated image(s) for quality and prompt adherence.",
    ]

    # Latency: ~20 s per image; allow up to 4 images
    latency_p50_seconds = 25.0

    def get_status(self) -> ToolStatus:
        if not _sw_binary():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Flow credits are not USD; return 0.0 (cost tracked in credits, not dollars)
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return 20.0 * inputs.get("count", 1)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        sw = _sw_binary()
        if not sw:
            return ToolResult(
                success=False,
                error="StudioWright (sw) not found. " + self.install_instructions,
            )

        prompt = inputs["prompt"]
        model = inputs.get("model", "nano-banana-2.1")
        aspect = inputs.get("aspect_ratio", "16:9")
        count = inputs.get("count", 1)
        size = inputs.get("size", "2K")
        dry_run = inputs.get("dry_run", False)
        profile = inputs.get("profile")
        ref_paths: list[str] = inputs.get("ref_paths") or []

        # sw image does not support remote dry-run pricing; handle client-side
        if dry_run:
            return ToolResult(
                success=True,
                data={
                    "provider": "google_flow",
                    "model": model,
                    "prompt": prompt,
                    "aspect_ratio": aspect,
                    "dry_run": True,
                    "message": "Preflight check passed.",
                },
                cost_usd=None,
                duration_seconds=0.0,
                model=model,
            )

        out_dir = Path(inputs.get("output_path") or "out/flow_image")
        out_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [sw]
        if profile:
            cmd += ["--profile", profile]
        cmd += [
            "image", prompt,
            "--model", model,
            "--aspect", aspect,
            "--count", str(count),
            "--size", size,
            "--out", str(out_dir),
            "--json",
        ]
        for ref in ref_paths:
            cmd += ["--ref", ref]

        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120 * count,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"flow_image timed out after {120 * count}s. "
                      "Chrome may be minimized — use `sw window hide` instead.",
            )
        except Exception as exc:
            return ToolResult(success=False, error=f"flow_image failed to start: {exc}")

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            # The medium is already generated; only sw's inline download failed.
            files = recover_download(sw, "image", start, out_dir, size, profile)
            if files:
                return ToolResult(
                    success=True,
                    data={
                        "provider": "google_flow",
                        "model": model,
                        "prompt": prompt,
                        "aspect_ratio": aspect,
                        "output": files[0],
                        "outputs": files,
                        "images_generated": len(files),
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
                error=f"sw image exited {result.returncode}: {stderr or stdout}",
            )

        data = parse_sw_json(stdout)
        outputs = output_files(data, out_dir, ("*.jpg", "*.png", "*.webp"))

        if not outputs:
            return ToolResult(
                success=False,
                error=f"flow_image returned no output files. stdout={stdout!r}",
            )

        duration = round(time.time() - start, 2)
        return ToolResult(
            success=True,
            data={
                "provider": "google_flow",
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect,
                "output": outputs[0] if outputs else None,
                "outputs": outputs,
                "images_generated": len(outputs),
                "dry_run": False,
                "sw_data": data,
                "cost_status": "flow_credits",
            },
            artifacts=outputs,
            cost_usd=None,
            duration_seconds=duration,
            model=model,
        )
