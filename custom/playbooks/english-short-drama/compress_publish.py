"""Create and verify the English short-drama publish MP4 from the rendered master."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.tool_registry import ToolRegistry  # noqa: E402


def _probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    data = json.loads(completed.stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    if video is None or audio is None:
        raise ValueError(f"Expected video and audio streams in {path}")
    try:
        fps = float(Fraction(video["r_frame_rate"]))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"Invalid frame rate in {path}") from exc
    duration = float(data["format"]["duration"])
    if fps <= 0 or not 0 < duration < float("inf"):
        raise ValueError(f"Invalid frame rate or duration in {path}")
    return {
        "path": str(path),
        "format": "mp4",
        "codec": video["codec_name"],
        "audio_codec": audio["codec_name"],
        "resolution": f"{video['width']}x{video['height']}",
        "fps": fps,
        "duration_seconds": duration,
        "file_size_bytes": path.stat().st_size,
    }


def compress_publish(project_dir: Path) -> dict[str, Any]:
    renders = project_dir.resolve() / "renders"
    master_path = renders / "final.mp4"
    if not master_path.is_file():
        raise FileNotFoundError(f"Render the master before compression: {master_path}")
    master = _probe(master_path)
    tools = ToolRegistry()
    tools.discover("tools.video.video_compose")
    encoder = tools.get("video_compose")
    if encoder is None:
        raise RuntimeError("OpenMontage video_compose is unavailable")

    settings = {"codec": "libx264", "crf": 23, "preset": "medium"}
    with TemporaryDirectory(prefix=".publish-", dir=renders) as staging:
        candidate_path = Path(staging) / "final-publish.mp4"
        result = encoder.execute({
            "operation": "encode",
            "input_path": str(master_path),
            "output_path": str(candidate_path),
            **settings,
        })
        if not result.success:
            raise RuntimeError(result.error or "video_compose encode failed")
        candidate = _probe(candidate_path)
        if (
            candidate["codec"] != "h264"
            or candidate["audio_codec"] != "aac"
            or candidate["resolution"] != master["resolution"]
            or candidate["fps"] != master["fps"]
            or abs(candidate["duration_seconds"] - master["duration_seconds"])
            > max(1 / master["fps"], 0.05)
        ):
            raise ValueError("Compressed output failed codec, resolution, frame rate, or duration checks")

        saved_bytes = master["file_size_bytes"] - candidate["file_size_bytes"]
        publish = master
        if saved_bytes > 0:
            target = renders / candidate_path.name
            candidate_path.replace(target)
            publish = {**candidate, "path": str(target)}

    return {
        "status": "compressed" if saved_bytes > 0 else "master_is_smaller_or_equal",
        "encoding": settings,
        "master": master,
        "publish": {**publish, "platform_target": "xiaohongshu_douyin_9x16"},
        "saved_bytes": max(0, saved_bytes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True, help="OpenMontage episode workspace")
    args = parser.parse_args()
    try:
        report = compress_publish(args.project_dir)
    except subprocess.CalledProcessError as exc:
        parser.exit(1, f"Publish compression failed: {(exc.stderr or str(exc)).strip()}\n")
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Publish compression failed: {exc}\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
