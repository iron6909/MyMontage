"""Validate and archive a local deliverable using the MyMontage Drive contract."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

CATEGORIES = {"01_English_Drama", "02_Tech_Agent", "03_Anime_Lore", "99_Temp_Test"}
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z", re.ASCII)


def upload(local_path, category, project_slug):
    """Return structured archive metadata; callers must authorize the upload."""
    if category not in CATEGORIES:
        raise ValueError("invalid archive category")
    if not isinstance(project_slug, str) or not SLUG.fullmatch(project_slug):
        raise ValueError("invalid project slug")
    remote = os.environ.get("MYMONTAGE_GDRIVE_REMOTE", "gdrive")
    if remote != "gdrive":
        raise ValueError("MYMONTAGE_GDRIVE_REMOTE must be gdrive")
    path = Path(local_path)
    if any(c in str(path) for c in "\r\n") or not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("local deliverable must be a nonempty file with a single-line path")
    size = path.stat().st_size
    system_ffprobe = Path("/usr/bin/ffprobe")
    ffprobe = str(system_ffprobe) if system_ffprobe.is_file() else "ffprobe"
    reply = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    try:
        report = json.loads(reply.stdout)
        duration = float(report["format"]["duration"])
        streams = {stream["codec_type"] for stream in report["streams"]}
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid ffprobe report") from exc
    if not math.isfinite(duration) or duration <= 0 or not {"audio", "video"} <= streams:
        raise ValueError("deliverable must contain audio and video and have positive duration")
    destination = f"{remote}:MyMontage_Studio/{category}/{project_slug}/{project_slug}.mp4"
    for attempt in range(3):
        try:
            subprocess.run(["rclone", "copyto", "--checksum", "--immutable", "--", str(path), destination],
                           check=True, timeout=600)
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
    reply = subprocess.run(["rclone", "lsjson", "--stat", "--", destination],
                           capture_output=True, text=True, check=True, timeout=60)
    try:
        metadata = json.loads(reply.stdout)
        file_id = metadata["ID"]
        if type(metadata["Size"]) is not int or metadata["Size"] != size:
            raise ValueError("remote size does not match local file")
        if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
            raise ValueError("invalid remote file ID")
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid remote metadata") from exc
    result = {"local_path": str(path), "remote_path": destination,
              "private_url": f"https://drive.google.com/file/d/{file_id}/view",
              "duration_seconds": duration}
    artifacts = os.environ.get("LOCAL_DISPATCH_ARTIFACTS_FILE")
    if artifacts:
        write_artifacts(Path(artifacts), result)
    return result


def write_artifacts(path, result):
    """Append metadata atomically; the dispatch contract supplies a single writer."""
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = [line for line in previous.splitlines() if line.strip()]
    lines.extend(f"{key}: {result[key]}" for key in
                 ("local_path", "remote_path", "private_url", "duration_seconds"))
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as output:
            name = output.name
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("local_path", type=Path)
    parser.add_argument("category")
    parser.add_argument("project_slug")
    args = parser.parse_args()
    print(json.dumps(upload(args.local_path, args.category, args.project_slug), ensure_ascii=False))


if __name__ == "__main__":
    main()
