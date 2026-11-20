"""Run or resume MyMontage's pipeline, then validate and archive its deliverable."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = {"01_English_Drama", "02_Tech_Agent", "03_Anime_Lore"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checks(filename):
    system_ffprobe = Path("/usr/bin/ffprobe")
    ffprobe = str(system_ffprobe) if system_ffprobe.is_file() else "ffprobe"
    reply = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(filename)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    report = json.loads(reply.stdout)
    duration = float(report["format"]["duration"])
    streams = {stream["codec_type"] for stream in report["streams"]}
    if not math.isfinite(duration) or duration <= 1 or not {"audio", "video"} <= streams:
        raise ValueError("deliverable must contain audio and video and exceed one second")
    return f"ffprobe: duration={duration:.2f}s, audio and video present"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--engine", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    dispatch = read(args.dispatch)
    if dispatch["kind"] != "production":
        parser.error("production dispatch required")
    options = dispatch.get("options", {})
    if options.get("mode") == "test":
        category = "99_Temp_Test"
    elif options.get("mode") == "prod" and options.get("category") in CATEGORIES:
        category = options["category"]
    else:
        parser.error("invalid production mode or category")
    for field in ("run_id", "ticket"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", dispatch[field]):
            parser.error(f"invalid {field}")
    attempt = dispatch["attempt"]
    if type(attempt) is not int or not 1 <= attempt <= 100000:
        parser.error("invalid attempt")
    project_id = f'{dispatch["ticket"].lower()}-{dispatch["run_id"].lower()}-{attempt}'
    project = ROOT / "projects" / project_id
    if project.resolve() != project or (project / "renders").resolve() != project / "renders":
        parser.error("unexpected project symlink")
    final = project / "renders" / "final.mp4"
    if final.is_symlink():
        parser.error("unexpected deliverable symlink")
    checkpoints = sorted(project.glob("checkpoint_*.json"))
    if any(read(path).get("status") == "awaiting_human" for path in checkpoints):
        return 76
    compose = project / "checkpoint_compose.json"
    if not final.is_file() or not compose.is_file() or read(compose).get("status") != "completed":
        if args.engine != "agy":
            return 75
        prompt = (
            f"Read {ROOT / 'AGENT_GUIDE.md'} and run its production pipeline for this brief. "
            f"Use project ID {project_id}. Keep all outputs under {project}. "
            "Follow the manifest and approval checkpoints. Never self-approve or spend without authorization. "
            "Resume existing completed checkpoints. Stop at the next human approval. "
            "Leave the verified final file at renders/final.mp4.\n\n"
            + "\n\n".join(f"{key}: {dispatch[key]}" for key in
                           ("goal", "done_check", "proof", "known_info", "constraints"))
        )
        code = subprocess.run(
            ["agy", "--model", args.model, "--sandbox", "--input-format", "text"],
            cwd=ROOT, input=prompt, text=True,
        ).returncode
        if code in (126, 127) and not project.exists():
            return 75
        if code:
            return code
        if any(read(path).get("status") == "awaiting_human" for path in project.glob("checkpoint_*.json")):
            return 76
    if final.is_symlink() or final.resolve() != final or not compose.is_file() or read(compose).get("status") != "completed":
        parser.error("a completed compose checkpoint and local final.mp4 are required")
    marker = read(project / "project.json")
    if marker.get("project_id") != project_id:
        parser.error("the pipeline marker must match this attempt")
    import sys

    sys.path.insert(0, str(ROOT))
    from lib.checkpoint import _stage_requires_approval, get_pipeline_stages, read_checkpoint

    pipeline = marker["pipeline_type"]
    for stage in get_pipeline_stages(pipeline):
        checkpoint_path = project / f"checkpoint_{stage}.json"
        if checkpoint_path.is_symlink():
            parser.error("checkpoint paths must be regular files")
        checkpoint = read_checkpoint(ROOT / "projects", project_id, stage)
        if checkpoint and checkpoint["status"] == "awaiting_human":
            return 76
        if (
            not checkpoint or checkpoint["status"] != "completed"
            or checkpoint["pipeline_type"] != pipeline or checkpoint["project_id"] != project_id
        ):
            parser.error(f"pipeline stage {stage} is incomplete for this attempt")
        if _stage_requires_approval(pipeline, stage) and checkpoint.get("human_approved") is not True:
            return 76
    verification = checks(final)
    remote = os.environ.get("MYMONTAGE_GDRIVE_REMOTE")
    if not remote:
        parser.error("MYMONTAGE_GDRIVE_REMOTE is required for archive")
    destination = f"{remote.rstrip('/')}/{category}/{project_id}/final.mp4"
    subprocess.run(["rclone", "copyto", "--immutable", "--", str(final), destination], check=True, timeout=600)
    result = {key: dispatch[key] for key in ("run_id", "attempt", "ticket")}
    result.update(artifact=str(final), verification=verification, archive=destination)
    with tempfile.NamedTemporaryFile(dir=args.result.parent, mode="w", encoding="utf-8", delete=False) as output:
        json.dump(result, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(output.name, args.result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
