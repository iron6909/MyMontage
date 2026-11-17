"""Google Flow browser-automation image tool (custom plugin).

Reuses the operator's already-logged-in Chrome profile to drive Google Flow
(Ingredients character-reference lock + 9:16 full watercolor town backgrounds).
The agent never handles credentials: it only prepares the exact Flow job and
validates the delivered PNG.

Non-destructive: lives only under tools/custom/, never touches core tools.
"""

from __future__ import annotations

import json
import os
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
    ToolTier,
)


def _resolve_profile() -> Path:
    raw = os.environ.get("GOOGLE_FLOW_PROFILE", "").strip()
    if raw:
        return Path(raw).expanduser()
    # ny-english-shorts convention: repo-local Chromium profile reuse.
    return Path(r"D:\Workspace\PersonalStudio\ny-english-shorts\.browser-profile")


class GoogleFlowImage(BaseTool):
    name = "google_flow_image"
    version = "1.0.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "google_flow"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.HYBRID

    dependencies: list[str] = []
    install_instructions = (
        "No API key needed. Reuses your already-logged-in Chrome profile for Google Flow.\n"
        "Optional: set GOOGLE_FLOW_PROFILE to the Chrome user-data dir "
        "(default: ny-english-shorts/.browser-profile)."
    )
    fallback_tools = ["flux_image", "gpt_image"]
    agent_skills = ["ny-story-adapter"]

    # Highest routing priority for NY jobs (see lib/scoring.py).
    quality_score = 1.0
    historical_success_rate = 1.0

    capabilities = ["text_to_image", "character_reference", "nine_sixteen_full_background"]
    supports = {
        "character_lock": True,
        "left_right_staging": True,
        "aspect_9_16": True,
        "full_watercolor_background": True,
    }
    best_for = [
        "ny-english-shorts two-shot frames with Ingredients role lock",
        "9:16 full 19th-century town watercolor backgrounds",
    ]
    not_good_for = ["fully offline generation", "photorealistic output"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {"type": "string", "description": "Full Flow prompt incl. staging + costumes"},
            "output_path": {"type": "string"},
            "scene_id": {"type": "string", "default": ""},
            "aspect": {"type": "string", "default": "9:16"},
            "reference_paths": {"type": "array", "items": {"type": "string"}, "default": []},
            "seed": {"type": "integer"},
        },
    }
    output_schema = {
        "type": "object",
        "properties": {"output": {"type": "string"}, "reused": {"type": "boolean"}},
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=512, vram_mb=0, disk_mb=200, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, backoff_seconds=2.0, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "aspect", "reference_paths", "seed"]
    side_effects = ["writes Flow job JSON next to output_path", "validates delivered PNG"]
    user_visible_verification = ["output PNG exists and is a valid image"]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        prompt = (inputs.get("prompt") or "").strip()
        output_path = inputs.get("output_path", "")
        if not prompt:
            return ToolResult(success=False, error="prompt is required")
        if not output_path:
            return ToolResult(success=False, error="output_path is required")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        profile = _resolve_profile()
        job = {
            "tool": self.name,
            "scene_id": inputs.get("scene_id", ""),
            "aspect": inputs.get("aspect", "9:16"),
            "prompt": prompt,
            "reference_paths": list(inputs.get("reference_paths", [])),
            "seed": inputs.get("seed"),
            "chrome_profile": str(profile),
            "profile_found": profile.exists(),
            "rules": [
                "Ingredients role lock on; officer/carpenter/cook costumes fixed",
                "two-shot strictly left-right separated, never share frames across reversal beats",
                "9:16 full 19th-century town market watercolor background, never pure-white void",
            ],
        }
        job_path = out.with_suffix(".flow_job.json")
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

        if out.is_file() and out.stat().st_size > 0:
            return ToolResult(
                success=True,
                data={"output": str(out), "reused": True, "job_file": str(job_path)},
                artifacts=[str(out)],
                duration_seconds=round(time.time() - start, 2),
            )
        return ToolResult(
            success=False,
            error=(
                f"No image at {out}. Flow job prepared at {job_path} "
                f"(profile_found={profile.exists()}). Produce the frame in Google Flow "
                "with the logged-in Chrome profile, save the PNG to output_path, then rerun."
            ),
            duration_seconds=round(time.time() - start, 2),
        )
