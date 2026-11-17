"""Google Flow Music browser-automation配乐工具 (custom plugin).

Drives Flow Music / MusicFX with a pure-instrumental prompt: light, witty
European chamber plucked strings. Programmatic synth beeps are rejected by
contract. Like google_flow_image, the agent only prepares the job and
validates the delivered audio file.

Non-destructive: lives only under tools/custom/.
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


class GoogleFlowMusic(BaseTool):
    name = "google_flow_music"
    version = "1.0.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"
    provider = "google_flow"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.HYBRID

    dependencies: list[str] = []
    install_instructions = (
        "No API key needed. Reuses your already-logged-in Chrome profile for "
        "Google Flow Music (MusicFX). Optional GOOGLE_FLOW_PROFILE override."
    )
    fallback_tools = ["music_gen", "comfyui_music"]
    agent_skills = ["ny-story-adapter"]

    # Highest routing priority for NY jobs (see lib/scoring.py).
    quality_score = 1.0
    historical_success_rate = 1.0

    capabilities = ["generate_background_music", "generate_instrumental"]
    supports = {
        "instrumental": True,
        "vocals": False,
        "custom_lyrics": False,
        "style_control": True,
        "long_form": True,
    }
    best_for = [
        "light witty European chamber plucked-string BGM beds",
        "real instrumental music, never programmatic synth",
    ]
    not_good_for = ["sound effects", "songs with vocals"]

    input_schema = {
        "type": "object",
        "required": ["prompt", "output_path"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Pure-instrumental prompt, e.g. light witty European chamber plucked strings",
            },
            "duration_seconds": {"type": "number", "default": 30.0},
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=200, network_required=True)
    retry_policy = RetryPolicy(max_retries=1, backoff_seconds=2.0, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "duration_seconds"]
    side_effects = ["writes Flow Music job JSON next to output_path"]
    user_visible_verification = ["output audio exists; whisper check finds no vocals"]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        prompt = (inputs.get("prompt") or "").strip()
        output_path = inputs.get("output_path", "")
        if not prompt:
            return ToolResult(success=False, error="prompt is required (instrumental only)")
        lowered = prompt.lower()
        if any(bad in lowered for bad in ("synth beep", "sine beep", "programmatic", "8-bit")):
            return ToolResult(success=False, error="programmatic synth prompts are rejected; use real chamber instruments")
        if not output_path:
            return ToolResult(success=False, error="output_path is required")
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        job = {
            "tool": self.name,
            "prompt": prompt,
            "duration_seconds": float(inputs.get("duration_seconds", 30.0)),
            "chrome_profile": os.environ.get("GOOGLE_FLOW_PROFILE", ""),
            "rules": [
                "pure instrumental chamber music, plucked strings, light and witty",
                "no vocals, no synth beeps, no programmatic tones",
            ],
        }
        job_path = out.with_suffix(".flow_music_job.json")
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
                f"No music at {out}. Flow Music job prepared at {job_path}. "
                "Generate the instrumental in Flow Music with the logged-in profile, "
                "save to output_path, then rerun."
            ),
            duration_seconds=round(time.time() - start, 2),
        )
