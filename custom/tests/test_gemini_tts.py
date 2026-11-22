"""Offline contract tests for Google Cloud TTS — Gemini-TTS (`google_gemini_tts`)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

from tools.tool_registry import registry  # pi-lens-ignore: reportMissingImports

from ..tools.audio.gemini_tts import (
    DEFAULT_MODEL,
    GoogleGeminiTTS,
    build_endpoint,
    build_payload,
    inspect_auth,
)
from ..tools.loader import load


class TestGoogleGeminiTTS(TestCase):
    def test_registered_via_custom_loader_without_upstream_overlap(self):
        registry.discover()
        upstream = set(registry.list_all())
        names = load()
        self.assertIn("google_gemini_tts", names)
        self.assertFalse(upstream & set(names))
        self.assertIsInstance(registry.get("google_gemini_tts"), GoogleGeminiTTS)

    def test_single_and_two_speaker_payload_validation(self):
        single = build_payload(
            {
                "text": "Nice umbrella. [short pause] By the way, you took mine!",
                "prompt": "Warm amused tone at A2 pace.",
                "voice": "callirrhoe",
                "model": "gemini-2.5-pro-tts",
                "language_code": "en-US",
            },
            encoding="MP3",
        )
        self.assertEqual(single["voice"]["name"], "Callirrhoe")
        self.assertEqual(single["voice"]["modelName"], "gemini-2.5-pro-tts")
        self.assertEqual(single["input"]["prompt"], "Warm amused tone at A2 pace.")
        self.assertEqual(single["audioConfig"]["audioEncoding"], "MP3")

        dialogue = build_payload(
            {
                "prompt": "Witty rainy-evening banter.",
                "turns": [
                    {"speaker": "Leo", "text": "Is this your umbrella?"},
                    {"speaker": "Clara", "text": "[laughing] Yes, and that one is yours."},
                ],
                "speakers": {"Leo": "Puck", "Clara": "Callirrhoe"},
            },
            encoding="LINEAR16",
        )
        self.assertEqual(dialogue["voice"]["modelName"], DEFAULT_MODEL)
        self.assertEqual(len(dialogue["input"]["multiSpeakerMarkup"]["turns"]), 2)
        self.assertEqual(
            len(dialogue["voice"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]), 2
        )

        # Validation failures
        with self.assertRaises(ValueError):
            build_payload({"text": "Hi", "voice": "NonExistentVoice"}, encoding="MP3")
        with self.assertRaises(ValueError):
            build_payload({"text": "Hi", "model": "unknown-model"}, encoding="MP3")
        with self.assertRaises(ValueError):
            build_payload({"text": "   "}, encoding="MP3")
        with self.assertRaises(ValueError):
            build_payload(
                {
                    "turns": [{"speaker": "A", "text": "hi"}],
                    "speakers": {"A": "Orus", "B": "Puck", "C": "Kore"},
                },
                encoding="MP3",
            )
        with self.assertRaises(ValueError):
            build_payload(
                {
                    "turns": [{"speaker": "Bad Name", "text": "hi"}],
                    "speakers": {"Bad Name": "Orus", "Clara": "Kore"},
                },
                encoding="MP3",
            )

    def test_endpoint_regions(self):
        self.assertEqual(
            build_endpoint("global"),
            "https://texttospeech.googleapis.com/v1/text:synthesize",
        )
        self.assertEqual(
            build_endpoint("us"),
            "https://us-texttospeech.googleapis.com/v1/text:synthesize",
        )
        self.assertEqual(
            build_endpoint("global", model="gemini-3.8-flash-tts", project_id="proj-1"),
            "https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent",
        )
        self.assertEqual(
            build_endpoint("us", model="gemini-3.8-flash-lite-tts", project_id="proj-1"),
            "https://aiplatform.us.rep.googleapis.com/v1/projects/proj-1/locations/us/publishers/google/models/gemini-3.8-flash-lite-tts:generateContent",
        )
        with self.assertRaises(ValueError):
            build_endpoint("mars-west1")
        with self.assertRaises(ValueError):
            build_endpoint("northamerica-northeast1", model="gemini-3.8-flash-tts", project_id="proj-1")
        with self.assertRaises(ValueError):
            build_endpoint("global", model="gemini-3.8-flash-tts", project_id="")

    def test_gemini_38_agent_platform_payload_and_response(self):
        single = build_payload(
            {
                "model": "gemini-3.8-flash-tts",
                "text": "Wait... <short pause> you took my umbrella!",
                "prompt": "playful and amused, clear CEFR A2 pace",
                "voice": "callirrhoe",
                "language_code": "en-US",
            },
            encoding="LINEAR16",
        )
        self.assertEqual(
            single["contents"][0]["parts"][0],
            {
                "text": "Wait... <short pause> you took my umbrella!",
                "speechMetadata": {"style": "playful and amused, clear CEFR A2 pace"},
            },
        )
        self.assertEqual(
            single["generationConfig"]["speechConfig"],
            {"languageCode": "en", "voiceConfig": {"voice": "Callirrhoe"}},
        )

        # Custom voice IDs accepted on 3.8
        custom_voice = build_payload(
            {"model": "gemini-3.8-flash-tts", "text": "Hi", "voice": "voice_custom_123"},
            encoding="LINEAR16",
        )
        self.assertEqual(
            custom_voice["generationConfig"]["speechConfig"]["voiceConfig"]["voice"],
            "voice_custom_123",
        )

        # Two-speaker 3.8 payload
        dialogue = build_payload(
            {
                "model": "gemini-3.8-flash-tts",
                "prompt": "witty banter",
                "turns": [
                    {"speaker": "Leo", "text": "Is this yours?"},
                    {"speaker": "Clara", "text": "<laughing> Yes!", "style": "amused"},
                ],
                "speakers": {"Leo": "Puck", "Clara": "Callirrhoe"},
            },
            encoding="LINEAR16",
        )
        self.assertEqual(len(dialogue["contents"][0]["parts"]), 2)
        self.assertEqual(
            dialogue["contents"][0]["parts"][1]["speechMetadata"],
            {"speaker": "Clara", "style": "amused"},
        )
        self.assertEqual(
            dialogue["generationConfig"]["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"],
            [
                {"speaker": "Leo", "voiceConfig": {"voice": "Puck"}},
                {"speaker": "Clara", "voiceConfig": {"voice": "Callirrhoe"}},
            ],
        )

        with self.assertRaises(ValueError):
            build_payload(
                {"model": "gemini-3.8-flash-tts", "text": "Hi", "sample_rate_hertz": 16000},
                encoding="LINEAR16",
            )

        # Live path mock: Agent Platform endpoint + inlineData decoding
        tool = GoogleGeminiTTS()
        fake_wav = b"RIFF\x24\x00\x00\x00WAVEfmt "
        ap_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "audio/wav",
                                    "data": base64.b64encode(fake_wav).decode("ascii"),
                                }
                            }
                        ]
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_wav = Path(tmp) / "line38.wav"
            with patch(
                "custom.tools.audio.gemini_tts._mint_bearer_token",
                return_value=("secret-38", "proj-38", "service_account_json"),
            ), patch(
                "custom.tools.audio.gemini_tts._http_post_json",
                return_value=ap_response,
            ) as post_mock:
                res = tool.synthesize(
                    {
                        "model": "gemini-3.8-flash-tts",
                        "text": "Nice umbrella.",
                        "prompt": "warm and amused",
                        "voice": "Callirrhoe",
                        "output_path": str(out_wav),
                    }
                )
            self.assertTrue(res.success, res.error)
            self.assertEqual(res.data["api"], "agent_platform")
            self.assertEqual(out_wav.read_bytes(), fake_wav)
            called_url = post_mock.call_args.args[0]
            self.assertIn("/projects/proj-38/locations/global/publishers/google/models/gemini-3.8-flash-tts:generateContent", called_url)
            self.assertNotIn("x-goog-user-project", post_mock.call_args.kwargs["headers"])

    def test_cli_dry_run_and_mock_response_write_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_dry = root / "dry.mp3"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "custom.tools.audio.gemini_tts",
                    "--text",
                    "Hello from New York.",
                    "--prompt",
                    "Speak warmly.",
                    "--voice",
                    "Orus",
                    "--output",
                    str(out_dry),
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(out_dry.exists())
            data = json.loads(proc.stdout)
            self.assertEqual(data["mode"], "dry-run")
            self.assertEqual(data["payload"]["voice"]["modelName"], DEFAULT_MODEL)

            fake_mp3 = b"ID3\x03\x00\x00\x00\x00\x00\x00fake-audio-bytes"
            mock_json = root / "resp.json"
            mock_json.write_text(
                json.dumps({"audioContent": base64.b64encode(fake_mp3).decode("ascii")}),
                encoding="utf-8",
            )
            out_mock = root / "nested" / "line.mp3"
            proc_mock = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "custom.tools.audio.gemini_tts",
                    "--text",
                    "Hello from New York.",
                    "--voice",
                    "Kore",
                    "--output",
                    str(out_mock),
                    "--mock-response",
                    str(mock_json),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_mock.returncode, 0, proc_mock.stderr)
            self.assertEqual(out_mock.read_bytes(), fake_mp3)

    def test_live_path_headers_and_token_redaction_on_error(self):
        tool = GoogleGeminiTTS()
        fake_wav = b"RIFF\x24\x00\x00\x00WAVEfmt "
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "line.wav"
            with patch(
                "custom.tools.audio.gemini_tts._mint_bearer_token",
                return_value=("secret-token-xyz", "demo-proj-123", "service_account_json"),
            ), patch(
                "custom.tools.audio.gemini_tts._http_post_json",
                return_value={"audioContent": base64.b64encode(fake_wav).decode("ascii")},
            ) as post_mock:
                res = tool.synthesize(
                    {
                        "text": "Good evening.",
                        "prompt": "Warm narrator.",
                        "voice": "Orus",
                        "output_path": str(out),
                    }
                )
            self.assertTrue(res.success, res.error)
            self.assertEqual(out.read_bytes(), fake_wav)
            headers = post_mock.call_args.kwargs["headers"]
            self.assertEqual(headers["Authorization"], "Bearer secret-token-xyz")
            self.assertEqual(headers["x-goog-user-project"], "demo-proj-123")

            with patch(
                "custom.tools.audio.gemini_tts._mint_bearer_token",
                return_value=("secret-token-xyz", "demo-proj-123", "service_account_json"),
            ), patch(
                "custom.tools.audio.gemini_tts._http_post_json",
                side_effect=RuntimeError("boom with secret-token-xyz leaked"),
            ):
                err_res = tool.synthesize(
                    {"text": "Fail test.", "voice": "Orus", "output_path": str(out)}
                )
            self.assertFalse(err_res.success)
            self.assertNotIn("secret-token-xyz", err_res.error or "")
            self.assertIn("[REDACTED]", err_res.error or "")

    def test_inspect_auth_reads_service_account_from_providers_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            harness = home / ".harness"
            harness.mkdir()
            sa_file = harness / "gcp-tts-sa.json"
            sa_file.write_text(
                json.dumps(
                    {
                        "type": "service_account",
                        "project_id": "my-drama-proj",
                        "private_key": "DO-NOT-ECHO",
                    }
                ),
                encoding="utf-8",
            )
            (harness / "providers.env").write_text(
                f"GOOGLE_APPLICATION_CREDENTIALS={sa_file}\n",
                encoding="utf-8",
            )
            clean_env = {
                k: v
                for k, v in os.environ.items()
                if k
                not in {
                    "GOOGLE_APPLICATION_CREDENTIALS",
                    "GOOGLE_CLOUD_PROJECT",
                    "GOOGLE_CLOUD_PROJECT_ID",
                    "GCLOUD_PROJECT",
                }
            }
            clean_env["HOME"] = str(home)
            with patch.dict(os.environ, clean_env, clear=True):
                info = inspect_auth()
            self.assertEqual(info["auth_source"], "service_account_json")
            self.assertEqual(info["project_id"], "my-drama-proj")
            self.assertTrue(info["service_account_exists"])
            self.assertNotIn("DO-NOT-ECHO", json.dumps(info))


if __name__ == "__main__":
    main()
