"""Offline contract tests: no subprocess reaches ffprobe or rclone."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from custom.strategy import production
from custom.strategy import shared_upload as upload


class SharedUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "final.mp4"
        self.file.write_bytes(b"video")
        self.calls = []
        self.duration = "1"
        self.streams = [{"codec_type": "video"}, {"codec_type": "audio"}]
        self.size = 5
        self.file_id = "real_ID-123"
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.runner = patch.object(upload.subprocess, "run", side_effect=self.run_command)
        self.runner.start()
        self.addCleanup(self.runner.stop)

    def run_command(self, command, **kwargs):
        self.calls.append(command)
        if command[0].endswith("ffprobe"):
            data = {"format": {"duration": self.duration}, "streams": self.streams}
        elif command[:2] == ["rclone", "lsjson"]:
            data = {"ID": self.file_id, "Size": self.size}
        elif command[:2] == ["rclone", "copyto"]:
            data = {}
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(data))

    def call(self, category="99_Temp_Test", slug="test-1"):
        return upload.upload(self.file, category, slug)

    def test_invalid_category_slug_and_remote_rejected(self):
        for category, slug in [("invalid", "ok"), ("", "ok"), ("99_Temp_Test", ".."),
                               ("99_Temp_Test", "a/b"), ("99_Temp_Test", "a:b"),
                               ("99_Temp_Test", "a\nb"), ("99_Temp_Test", "é")]:
            with self.subTest(category=category, slug=slug), self.assertRaises(ValueError):
                self.call(category, slug)
        os.environ["MYMONTAGE_GDRIVE_REMOTE"] = "gdrive:prefix"
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual(self.calls, [])

    def test_empty_and_bad_video(self):
        self.file.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.call()
        self.file.write_bytes(b"video")
        for duration, streams in [("0", self.streams), ("nan", self.streams),
                                  ("bad", self.streams), ("1", [{"codec_type": "video"}])]:
            self.duration, self.streams = duration, streams
            with self.subTest(duration=duration, streams=streams), self.assertRaises(ValueError):
                self.call()
        self.assertFalse(any(c[0] == "rclone" for c in self.calls))

    def test_upload_shape_private_url_and_artifacts(self):
        artifacts = Path(self.tmp.name) / "artifacts.txt"
        artifacts.write_text("existing: value\n\nother: value\n", encoding="utf-8")
        os.environ["LOCAL_DISPATCH_ARTIFACTS_FILE"] = str(artifacts)
        result = self.call()
        destination = "gdrive:MyMontage_Studio/99_Temp_Test/test-1/test-1.mp4"
        self.assertEqual(self.calls[1], ["rclone", "copyto", "--checksum", "--immutable", "--", str(self.file), destination])
        self.assertEqual(self.calls[2], ["rclone", "lsjson", "--stat", "--", destination])
        self.assertEqual(result["private_url"], "https://drive.google.com/file/d/real_ID-123/view")
        self.assertEqual(result["duration_seconds"], 1.0)
        self.assertEqual(artifacts.read_text().splitlines(), ["existing: value", "other: value", f"local_path: {self.file}",
                         f"remote_path: {destination}", f"private_url: {result['private_url']}", "duration_seconds: 1.0"])

    def test_size_mismatch_and_missing_id_fail(self):
        self.size = 6
        with self.assertRaises(ValueError):
            self.call()
        self.size = 5
        self.file_id = ""
        with self.assertRaises(ValueError):
            self.call()

    def test_production_threshold_remains_above_one_second(self):
        with self.assertRaises(ValueError):
            production.checks(self.file)
        self.duration = "1.01"
        self.assertIn("audio and video present", production.checks(self.file))

    def test_bounded_retries(self):
        original = self.run_command
        def fail_copy(command, **kwargs):
            if command[:2] == ["rclone", "copyto"]:
                self.calls.append(command)
                raise subprocess.CalledProcessError(1, command)
            return original(command, **kwargs)
        self.runner.stop()
        with patch.object(upload.subprocess, "run", side_effect=fail_copy):
            with self.assertRaises(subprocess.CalledProcessError):
                self.call()
        self.assertEqual(sum(c[:2] == ["rclone", "copyto"] for c in self.calls), 3)


if __name__ == "__main__":
    unittest.main()
