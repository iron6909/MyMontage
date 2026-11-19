"""Generate images via Google Flow (Nano Banana) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   uv tool install git+https://github.com/umutkocak/studiowright
2. Authenticate once:      sw auth login
3. Keep Chrome running:    sw window hide  (never minimize)

Entry point (from the repository root):
    python3 -m custom.plugins.studiowright image --help

`sw` reaches its Chrome on its own loopback port (9300 + crc32(profile) % 90), so
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
    """Return absolute path to the ``sw`` binary, or None if not installed."""
    return shutil.which("sw")


def _out_dir(inputs: dict[str, Any]) -> Path:
    return Path(inputs.get("output_path") or "out/flow_image")


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
        "dry_run": True,
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
                "description": "Local file paths or Flow media ids to use as reference images.",
            },
            "output_path": {
                "type": "string",
                "description": "Directory where images will be saved (default: out/flow_image).",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Abandon the sw call after this many seconds (default: 120 per image).",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Preflight check, client-side: sw image has no remote pricing call.",
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

    def build_command(self, inputs: dict[str, Any]) -> list[str]:
        """The exact `sw` argv for these inputs (used by --plan and by execute)."""
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        cmd += [
            "image", inputs["prompt"],
            "--model", inputs.get("model", "nano-banana-2.1"),
            "--aspect", inputs.get("aspect_ratio", "16:9"),
            "--count", str(inputs.get("count", 1)),
            "--size", inputs.get("size", "2K"),
            "--out", str(_out_dir(inputs)),
            "--json",
        ]
        for ref in inputs.get("ref_paths") or []:
            cmd += ["--ref", ref]
        return cmd

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs["prompt"]
        model = inputs.get("model", "nano-banana-2.1")
        aspect = inputs.get("aspect_ratio", "16:9")
        count = inputs.get("count", 1)
        size = inputs.get("size", "2K")
        profile = inputs.get("profile")
        timeout_s = float(inputs.get("timeout_seconds") or 120 * count)
        cmd = self.build_command(inputs)

        # sw image has no remote dry-run pricing; handle client-side.
        if inputs.get("dry_run"):
            return ToolResult(
                success=True,
                data={
                    "provider": "google_flow",
                    "model": model,
                    "prompt": prompt,
                    "aspect_ratio": aspect,
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
                error=f"flow_image timed out after {timeout_s:.0f}s. "
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
                        "command": cmd,
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
                "command": cmd,
            },
            artifacts=outputs,
            cost_usd=None,
            duration_seconds=duration,
            model=model,
        )
