"""OpenRouter multi-speaker TTS (custom plugin).

Reads OPENROUTER_API_KEY from .env, calls
https://openrouter.ai/v1/chat/completions with model
google/gemini-3.8-flash-tts, modalities ["text", "audio"] and per-sentence
voice mapping (narrator -> Charon, leo -> Fenrir, carpenter -> Puck,
cook -> Orus). System prompt forces verbatim read-aloud with no added words.
Base64 audio is RIFF-checked: bare PCM16 is wrapped as 24kHz/16-bit/mono WAV,
then concatenated with 0.35s silence between sentences.

Non-destructive: lives only under tools/custom/.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import wave
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

VOICE_MAP = {
    "narrator": "Charon",
    "leo": "Fenrir",
    "carpenter": "Puck",
    "cook": "Orus",
}
MODEL = "google/gemini-3.8-flash-tts"
API_URL = "https://openrouter.ai/v1/chat/completions"
SAMPLE_RATE = 24000
PAUSE_SEC = 0.35
SYSTEM_PROMPT = (
    "You are a professional audiobook narrator. Read the user text aloud "
    "verbatim: do not add, remove, or change any word. No intro, no outro, "
    "no commentary. Narration only."
)


def _pcm16_to_wav(raw: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(raw)
    return buf.getvalue()


def _ensure_wav(raw: bytes) -> bytes:
    if raw[:4] == b"RIFF":
        return raw
    return _pcm16_to_wav(raw)


def _read_wav_frames(wav_bytes: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth()


class OpenRouterMultispeakerTTS(BaseTool):
    name = "openrouter_multispeaker_tts"
    version = "1.0.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "openrouter"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = ["env:OPENROUTER_API_KEY"]
    install_instructions = (
        "Set OPENROUTER_API_KEY in .env (get one at https://openrouter.ai/keys).\n"
        "Optional TTS_MODEL override; default is google/gemini-3.8-flash-tts."
    )
    fallback_tools = ["openai_tts", "piper_tts"]
    agent_skills = ["text-to-speech", "ny-story-adapter"]

    # Highest routing priority for NY jobs (see lib/scoring.py).
    quality_score = 1.0
    historical_success_rate = 1.0

    capabilities = ["text_to_speech", "multi_speaker", "verbatim_read"]
    supports = {"multispeaker": True, "verbatim": True, "offline": False, "word_timestamps": False}
    best_for = ["ny-english-shorts four-voice verbatim narration"]
    not_good_for = ["offline production", "voice cloning"]

    input_schema = {
        "type": "object",
        "required": ["episode_path", "output_path"],
        "properties": {
            "episode_path": {"type": "string", "description": "Episode JSON with sentences[].text/speaker"},
            "output_path": {"type": "string"},
            "pause_sec": {"type": "number", "default": PAUSE_SEC},
            "model": {"type": "string", "default": MODEL},
        },
    }

    resource_profile = ResourceProfile(cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100, network_required=True)
    retry_policy = RetryPolicy(max_retries=2, backoff_seconds=2.0, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["episode_path", "model", "pause_sec"]
    side_effects = ["writes WAV to output_path", "calls OpenRouter API"]
    user_visible_verification = ["output WAV exists; sentence count matches"]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        try:
            data = json.loads(Path(inputs.get("episode_path", "")).read_text(encoding="utf-8"))
            chars = sum(len(s.get("text", "")) for s in data.get("sentences", []))
        except (OSError, ValueError):
            chars = 2000
        return round(chars / 1000 * 0.015, 4)

    def _speak_one(self, api_key: str, model: str, voice: str, text: str) -> bytes:
        import requests

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "modalities": ["text", "audio"],
            "audio": {"voice": voice, "format": "wav"},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        last_error = "unknown"
        for attempt in range(3):
            try:
                resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                body = resp.json()
                msg = (body.get("choices") or [{}])[0].get("message", {})
                audio = msg.get("audio", {}) or {}
                b64 = audio.get("data", "")
                if not b64:
                    last_error = f"no audio.data in response: {str(body)[:200]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                return _ensure_wav(base64.b64decode(b64))
            except Exception as exc:  # noqa: BLE001 - retry transient errors
                last_error = str(exc)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"OpenRouter TTS failed: {last_error}")

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return ToolResult(success=False, error="OPENROUTER_API_KEY is not set. " + self.install_instructions)
        episode_path = inputs.get("episode_path", "")
        output_path = inputs.get("output_path", "")
        if not episode_path or not output_path:
            return ToolResult(success=False, error="episode_path and output_path are required")
        try:
            episode = json.loads(Path(episode_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return ToolResult(success=False, error=f"cannot read episode JSON: {exc}")
        sentences = episode.get("sentences", [])
        if not sentences:
            return ToolResult(success=False, error="episode has no sentences[]")

        model = (inputs.get("model") or os.environ.get("TTS_MODEL", "") or MODEL).strip()
        pause_sec = float(inputs.get("pause_sec", PAUSE_SEC))
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        try:
            parts: list[bytes] = []
            for s in sentences:
                speaker = s.get("speaker", "narrator")
                voice = VOICE_MAP.get(speaker)
                if not voice:
                    return ToolResult(success=False, error=f"unknown speaker {speaker!r}")
                wav = self._speak_one(api_key, model, voice, s.get("text", ""))
                pcm, _, _ = _read_wav_frames(wav)
                parts.append(pcm)
            silence = b"\x00\x00" * int(SAMPLE_RATE * pause_sec)
            pcm_all = silence.join(parts)
            out.write_bytes(_pcm16_to_wav(pcm_all))
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))

        duration = round(len(pcm_all) / 2 / SAMPLE_RATE, 2)
        return ToolResult(
            success=True,
            data={"output": str(out), "model": model, "sentences": len(sentences), "duration_sec": duration},
            artifacts=[str(out)],
            duration_seconds=round(time.time() - start, 2),
            model=model,
        )
