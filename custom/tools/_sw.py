"""StudioWright (`sw`) CLI output handling, shared by the custom flow tools.

`sw ... --json` prints ONE object via `json.dumps(..., indent=1)` (see
studiowright/cli.py `_emit`), so a line-by-line `json.loads` never parses it.
The result object carries the downloaded paths under `files`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def parse_sw_json(stdout: str) -> dict:
    """Return the object `sw --json` printed, or {} when there is none."""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    # Tolerate stray warning lines around the object.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def output_files(data: dict, out_dir: Path, patterns: tuple[str, ...]) -> list[str]:
    """Paths of the files this run produced: `files` from sw, else a folder glob."""
    files = [str(p) for p in (data.get("files") or [])]
    if files:
        return files
    # shortcut: the glob cannot tell this run's files from older ones; it only
    # runs when sw's result lacks `files`, which it has not since 0.2.13.
    found: list[str] = []
    for pattern in patterns:
        found += sorted(str(p) for p in out_dir.glob(pattern))
    return found


def _run_json(cmd: list[str], timeout_s: float = 300) -> dict:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
    except Exception:
        return {}
    return parse_sw_json(result.stdout) if result.returncode == 0 else {}


def recover_download(
    sw: str,
    kind: str,
    since: float,
    out_dir: Path,
    size: str,
    profile: str | None = None,
) -> list[str]:
    """Fetch a just-generated medium by id after sw's inline download failed.

    ``sw image`` sometimes cannot enable the edit view's download button right
    after generating; the medium is already in the Flow project, so listing it
    and downloading it by id delivers the same file without another credit.
    """
    base = [sw] + (["--profile", profile] if profile else [])
    listing = _run_json(base + ["media", "list", "--limit", "10", "--json"])
    ids = [
        str(m.get("media_id"))
        for m in listing.get("media") or []
        if m.get("kind") == kind and float(m.get("created") or 0) >= since - 5
    ]
    if not ids:
        return []
    data = _run_json(
        base + ["download", ids[0], "--out", str(out_dir), "--size", size, "--json"]
    )
    return [str(p) for p in (data.get("files") or [])]
