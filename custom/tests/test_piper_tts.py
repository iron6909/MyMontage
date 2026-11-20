"""Offline regression for custom registration and the Piper CLI arguments."""

from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase, main
from unittest.mock import patch

from custom.tools.audio.piper_tts import PiperTTS
from custom.tools.loader import load
from tools.base_tool import ToolStatus
from tools.tool_registry import registry


class TestPiperTTS(TestCase):
    def test_custom_tools_extend_the_upstream_registry(self):
        registry.discover()
        upstream = {name: registry.get(name) for name in registry.list_all()}
        names = load()
        self.assertTrue({PiperTTS.name, "flow_image", "flow_video", "flow_music"} <= set(names))
        self.assertEqual(len(names), len(set(names)), "custom tool names must be unique")
        self.assertFalse(upstream.keys() & set(names), "custom tool names overlap upstream")
        self.assertIsInstance(registry.get(PiperTTS.name), PiperTTS)

    def test_optional_speaker_flag_and_artifact(self):
        tool = PiperTTS()
        with tempfile.TemporaryDirectory() as tmp:
            for inputs, speaker_id in (({}, 0), ({"speaker_id": 0}, 0), ({"speaker_id": 3}, 3)):
                with self.subTest(inputs=inputs):
                    output = Path(tmp) / "output.wav"

                    def run(argv, **kwargs):
                        Path(argv[argv.index("--output_file") + 1]).write_bytes(b"audio")
                        return subprocess.CompletedProcess(argv, 0)

                    with patch.object(tool, "get_status", return_value=ToolStatus.AVAILABLE), patch(
                        "custom.tools.audio.piper_tts.subprocess.run", side_effect=run,
                    ) as command:
                        result = tool.execute({"text": "配音", "output_path": str(output), **inputs})
                    argv = command.call_args.args[0]
                    if speaker_id:
                        self.assertEqual(argv[argv.index("--speaker") + 1], str(speaker_id))
                    else:
                        self.assertNotIn("--speaker", argv)
                    self.assertEqual(command.call_args.kwargs["encoding"], "utf-8")
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.data["speaker_id"], speaker_id)
                    self.assertEqual(result.artifacts, [str(output)])
                    output.unlink()


if __name__ == "__main__":
    main()
