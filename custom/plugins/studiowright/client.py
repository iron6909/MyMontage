"""Invoke the pinned StudioWright CLI and verify its job-scoped outputs."""

from __future__ import annotations

import json
import math
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from custom.tools.common.proxy_env import sw_env
from tools.base_tool import ToolResult

SW_INSTALL = (
    "Run `make -f custom/Makefile sw-install` to install the MyMontage StudioWright fork."
)


def parse_sw_json(stdout: str) -> dict[str, Any]:
    """Read the one JSON result, allowing warning lines before or after it."""
    decoder = json.JSONDecoder()
    lines = stdout.strip().splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith("{"):
            continue
        try:
            value, _ = decoder.raw_decode("".join(lines[index:]))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=sw_env(), check=False,
    )


def timeout_seconds(inputs: dict[str, Any], default: float) -> float:
    timeout = inputs.get("timeout_seconds")
    value = default if timeout is None else float(timeout)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive, finite number of seconds")
    return value


def prompt_text(inputs: dict[str, Any]) -> str:
    prompt = inputs.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must contain text")
    return prompt


def prompt_command(command: list[str], prompt: str) -> list[str]:
    """Treat prompts that look like command options as positional text."""
    return command + (["--"] if prompt.startswith("-") else []) + [prompt]


def _media_header(kind: str, suffix: str, head: bytes) -> bool:
    if kind == "image":
        return {
            ".jpg": head.startswith(b"\xff\xd8\xff"),
            ".jpeg": head.startswith(b"\xff\xd8\xff"),
            ".png": head.startswith(b"\x89PNG\r\n\x1a\n"),
            ".webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
            ".gif": head[:6] in (b"GIF87a", b"GIF89a"),
        }.get(suffix, False)
    if kind == "video":
        return (suffix == ".mp4" and head[4:8] == b"ftyp") or (
            suffix == ".gif" and head[:6] in (b"GIF87a", b"GIF89a")
        )
    if suffix == ".mp3":
        return head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if suffix == ".wav":
        return head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE"
    return suffix == ".m4a" and head[4:8] == b"ftyp"


def _verified_files(data: dict, out_dir: Path, kind: str) -> list[str]:
    reported = data.get("files")
    if not isinstance(reported, list):
        return []
    files = []
    for value in reported:
        if not isinstance(value, str) or not value:
            continue
        try:
            path = Path(value).resolve()
            if not path.is_relative_to(out_dir) or not path.is_file():
                continue
            with path.open("rb") as file:
                if not _media_header(kind, path.suffix.lower(), file.read(12)):
                    continue
        except (OSError, RuntimeError, ValueError):
            continue
        files.append(str(path))
    return list(dict.fromkeys(files))


def _lookup_job(base: list[str], job_id: str, retries: int) -> dict[str, Any]:
    """Read only this request's persisted state. Status lookups never submit work."""
    latest = {}
    for attempt in range(retries + 1):
        try:
            child = _run(base + ["job", job_id, "--json"], 5)
        except (OSError, subprocess.TimeoutExpired):
            break
        status = parse_sw_json(child.stdout)
        if child.returncode == 0 and status.get("job_id") == job_id:
            latest = status
            if status.get("status") not in ("queued", "running") or attempt == retries:
                return status
        if attempt < retries:
            time.sleep(0.5)
    return latest


def _media_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _retrieve(command: list[str], deadline: float) -> dict:
    remaining = deadline - time.monotonic()
    if remaining < 1:
        return {}
    try:
        child = _run(command + ["--json"], min(remaining, 120))
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return parse_sw_json(child.stdout) if child.returncode == 0 else {}


def _recover_files(
    base: list[str], kind: str, data: dict, files: list[str], output: Path, deadline: float,
    audio_format: str,
) -> list[str]:
    """Recover confirmed IDs from this job without submitting or upscaling again."""
    if kind == "music":
        for song in data.get("songs") or []:
            if not isinstance(song, dict) or song.get("file") in files:
                continue
            song_id = song.get("id")
            if not _media_id(song_id):
                continue
            result = _retrieve(
                base + ["music", "download", song_id, "--format", audio_format, "--out", str(output)],
                deadline,
            )
            recovered = _verified_files(result, output, kind)
            if len(recovered) == 1:
                song["file"] = recovered[0]
                song.pop("download_error", None)
                files += recovered
        return list(dict.fromkeys(files))

    project = data.get("project_id")
    if not _media_id(project):
        return files
    known = data.get("file_by_media_id")
    recorded = known.copy() if isinstance(known, dict) else {}
    missing = [
        media for media in data.get("media_ids") or []
        if _media_id(media) and recorded.get(media) not in files
    ]
    if not missing:
        return files
    result = _retrieve(
        base + ["download", *missing, "--project", project, "--out", str(output)], deadline,
    )
    if result.get("project_id") != project:
        return files
    checked = _verified_files(result, output, kind)
    recovered = result.get("file_by_media_id")
    if not isinstance(recovered, dict):
        return files
    for media in missing:
        path = recovered.get(media)
        if path in checked:
            recorded[media] = path
            files.append(path)
    data["file_by_media_id"] = recorded
    return list(dict.fromkeys(files))


def invoke(
    command: list[str], *, kind: str, inputs: dict[str, Any], output: Path,
    timeout: float, metadata: dict[str, Any], expected: int = 1,
) -> ToolResult:
    """Submit once and trust only results addressed to this invocation's job ID."""
    dry_run = bool(inputs.get("dry_run"))
    retries = inputs.get("status_retries", 0)
    if type(retries) is not int or retries < 0:
        return ToolResult(success=False, error="status_retries must be a nonnegative integer")
    job_id = uuid.uuid4().hex
    base = command[:3] if inputs.get("profile") else command[:1]
    command = [*base, "--job-id", job_id, *command[len(base):]]
    started = time.monotonic()
    sw_data: dict[str, Any] = {}
    status: dict[str, Any] = {}
    error = None
    timed_out = False
    try:
        output = output.expanduser().resolve()
        if not dry_run:
            output.mkdir(parents=True, exist_ok=True)
        child = _run(command, timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        error = f"StudioWright stopped responding after {timeout:g}s. Check the saved job before submitting again."
        status = _lookup_job(base, job_id, retries)
    except (OSError, TypeError, ValueError) as exc:
        error = str(exc)
    else:
        result = parse_sw_json(child.stdout)
        if result.get("job_id") == job_id:
            sw_data = result
        if child.returncode != 0:
            error = child.stderr.strip() or f"StudioWright exited with code {child.returncode}"
            if "No such option: --job-id" in error:
                error = SW_INSTALL
        if not sw_data and error != SW_INSTALL:
            status = _lookup_job(base, job_id, 0)

    if status and isinstance(status.get("result"), dict):
        sw_data = status["result"]
        if status.get("error"):
            error = status["error"]
    project_id = sw_data.get("project_id") or status.get("project_id") or inputs.get("project")
    stage = sw_data.get("stage")
    files = [] if dry_run else _verified_files(sw_data, output, kind)
    songs = sw_data.get("songs")
    expected_count = max(expected, len(songs)) if isinstance(songs, list) else expected
    recovered = False
    if not dry_run and stage in ("generated", "delivered") and len(files) < expected_count:
        previous_count = len(files)
        files = _recover_files(
            base, kind, sw_data, files, output, started + timeout, metadata.get("format", "mp3"),
        )
        recovered = len(files) > previous_count
        if recovered:
            sw_data["files"] = files
            if kind != "music":
                sw_data.setdefault("notes", []).append("Recovered missing media at original size.")
        if len(files) == expected_count:
            stage = "delivered"
            sw_data["stage"] = stage
    pending = False
    if dry_run:
        success = sw_data.get("submitted") is False and type(sw_data.get("credits")) is int
        if not success and not error:
            error = "StudioWright returned no valid Flow credit quote"
    else:
        success = stage == "delivered" and len(files) == expected_count
        refused = str(status.get("error") or "").startswith(("GenerationRefused:", "UnusualActivity:"))
        if not success and not refused:
            pending = (
                stage in ("submitted", "generated", "delivered")
                or status.get("status") in ("queued", "running")
                or timed_out and not status
            )
        if pending:
            detail = "waiting for verified downloads" if stage in ("generated", "delivered") else "submission outcome pending"
            error = f"StudioWright job {job_id} is {detail}."
        elif not success and not error:
            error = "StudioWright returned no verifiable output for this job"

    data = {
        **metadata,
        "job_id": job_id,
        "job_status": "pending" if pending else ("ok" if success else "error"),
        "stage": stage,
        "project_id": project_id,
        "media_ids": sw_data.get("media_ids", []),
        "file_by_media_id": sw_data.get("file_by_media_id", {}),
        "songs": songs or [],
        "output": files[0] if files else None,
        "outputs": files,
        "dry_run": dry_run,
        "cost_status": "flow_credits",
        "recovered": recovered,
        "command": command,
        "sw_data": sw_data,
    }
    if kind == "image":
        data["images_generated"] = len(files)
    return ToolResult(
        success=success, error=None if success else error, data=data, artifacts=files,
        cost_usd=None, duration_seconds=round(time.monotonic() - started, 2),
        model=metadata.get("model"),
    )
