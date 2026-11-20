"""Generate images via Google Flow (Nano Banana) using StudioWright.

StudioWright drives your locally signed-in Chrome via Playwright — no API key
required. It consumes credits from your own Google Flow subscription.

Prerequisites
-------------
1. Install StudioWright:   make -f custom/Makefile sw-install
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
    """Return absolute path to the ``sw`` binary, or None if not installed."""
    return shutil.which("sw")


def _out_dir(inputs: dict[str, Any]) -> Path:
    return Path(inputs.get("output_path") or "out/flow_image").expanduser()


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
        "  make -f custom/Makefile sw-install\n"
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
                "enum": ["original", "1K", "2K", "4K"],
                "default": "original",
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
                "description": "StudioWright subprocess time limit in seconds, including downloads.",
            },
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "Preflight check, client-side: sw image has no remote pricing call.",
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
    retry_policy = RetryPolicy()
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
        prompt = prompt_text(inputs)
        count = inputs.get("count", 1)
        if type(count) is not int or not 1 <= count <= 4:
            raise ValueError("image count must be an integer from 1 through 4")
        size = inputs.get("size", "original")
        if size not in ("original", "1K", "2K", "4K"):
            raise ValueError("image size must be original, 1K, 2K or 4K")
        timeout_seconds(inputs, 360)
        cmd = [_sw_binary() or "sw"]
        if inputs.get("profile"):
            cmd += ["--profile", inputs["profile"]]
        cmd += [
            "image", "--model", inputs.get("model", "nano-banana-2.1"),
            "--aspect", inputs.get("aspect_ratio", "16:9"), "--count", str(count),
            "--out", str(_out_dir(inputs)), "--json",
        ]
        if size in ("2K", "4K"):
            cmd += ["--size", size]
        if inputs.get("project"):
            cmd += ["--project", inputs["project"]]
        for ref in inputs.get("ref_paths") or []:
            cmd += ["--ref", ref]
        return prompt_command(cmd, prompt)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            cmd = self.build_command(inputs)
            count = inputs.get("count", 1)
            sized = inputs.get("size") in ("2K", "4K")
            timeout = timeout_seconds(inputs, 360 + (1500 if sized else 60) * count)
        except (TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))
        model = inputs.get("model", "nano-banana-2.1")
        metadata = {
            "provider": self.provider, "model": model, "prompt": inputs["prompt"],
            "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
        }
        if inputs.get("dry_run"):
            return ToolResult(
                success=True, data={**metadata, "dry_run": True, "command": cmd},
                cost_usd=None, model=model,
            )
        if cmd[0] == "sw":
            return ToolResult(success=False, error="StudioWright (sw) not found. " + self.install_instructions)
        return invoke(
            cmd, kind="image", inputs=inputs, output=_out_dir(inputs), timeout=timeout,
            metadata=metadata, expected=count,
        )
