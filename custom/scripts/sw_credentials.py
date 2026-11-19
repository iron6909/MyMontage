#!/usr/bin/env python3
"""Report whether the StudioWright credentials exist — booleans and paths only.

Credential material (cookies, tokens, session DBs, emails) must never reach a
log, an evidence file or git. This script therefore prints existence checks and
paths, never contents:

    python3 custom/scripts/sw_credentials.py            # from the repo root
    make -f custom/Makefile sw-credentials

Nothing here reads a cookie, a token or the repo `.env` values.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _studiowright_home() -> Path:
    """Same resolution StudioWright's paths.py uses, without importing it."""
    override = os.environ.get("STUDIOWRIGHT_HOME", "").strip()
    if override:
        return Path(override)
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "studiowright"


def _env_file_ignored() -> bool:
    """True when git ignores `.env` (.git/info/exclude or .gitignore)."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", ".env"],
            capture_output=True,
        ).returncode == 0
    except OSError:
        return False


def main() -> int:
    sw = shutil.which("sw")
    home = _studiowright_home()
    report = {
        # presence, never values
        "sw_on_path": bool(sw),
        "sw_path": sw,
        "studiowright_home": str(home),
        "studiowright_home_exists": home.is_dir(),
        "chrome_profile": str(home / "profiles" / "default"),
        "chrome_profile_exists": (home / "profiles" / "default").is_dir(),
        "studiowright_home_env_set": bool(os.environ.get("STUDIOWRIGHT_HOME", "").strip()),
        "repo_env_file": str(REPO_ROOT / ".env"),
        "repo_env_exists": (REPO_ROOT / ".env").is_file(),
        "repo_env_git_ignored": _env_file_ignored(),
        "harness_dir": str(Path.home() / ".harness"),
        "harness_dir_exists": (Path.home() / ".harness").is_dir(),
    }
    print(json.dumps(report, indent=1))
    return 0 if report["sw_on_path"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
