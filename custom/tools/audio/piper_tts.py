"""Local Piper provider with single-speaker voice support."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from tools.audio.piper_tts import PiperTTS as UpstreamPiperTTS
from tools.base_tool import ToolResult


class PiperTTS(UpstreamPiperTTS):
    name = "mymontage_piper_tts"
    install_instructions = "Run make -f custom/Makefile local-setup to install Piper and a voice."

    @staticmethod
    def _voice_data_dir() -> Path:
        if directory := os.environ.get("PIPER_VOICES_DIR"):
            return Path(directory).expanduser()
        return UpstreamPiperTTS._voice_data_dir()

    def _generate(self, inputs: dict[str, Any]) -> ToolResult:
        output_path = Path(inputs.get("output_path", "tts_output.wav"))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Single-speaker voices have no `sid` ONNX input; an explicit --speaker
        # makes piper send one and fail. Piper already defaults to speaker 0.
        speaker_id = int(inputs.get("speaker_id") or 0)
        speaker_args = ["--speaker", str(speaker_id)] if speaker_id else []

        proc = subprocess.run(
            [
                "piper",
                "--model", inputs.get("model", "en_US-lessac-medium"),
                "--data-dir", str(self._voice_data_dir()),
                *speaker_args,
                "--length-scale", str(inputs.get("length_scale", 1.0)),
                "--sentence-silence", str(inputs.get("sentence_silence", 0.3)),
                "--output_file", str(output_path),
            ],
            input=inputs["text"],
            capture_output=True,
            text=True,
            # Both sides UTF-8: the Windows default (cp1252) turns accented text
            # into input piper can't decode, which yields an empty WAV.
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=300,
        )

        if proc.returncode != 0:
            return ToolResult(success=False, error=f"Piper failed (exit {proc.returncode}): {proc.stderr}")
        if not output_path.exists():
            return ToolResult(success=False, error=f"Piper output file missing: {output_path}")

        return ToolResult(
            success=True,
            data={
                "provider": self.provider,
                "model": inputs.get("model", "en_US-lessac-medium"),
                "speaker_id": speaker_id,
                "text_length": len(inputs["text"]),
                "output": str(output_path),
                "format": "wav",
            },
            artifacts=[str(output_path)],
            model=inputs.get("model", "en_US-lessac-medium"),
        )
