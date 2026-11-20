"""Verify MyMontage's current worktree with its offline project regression cases."""

import os
from pathlib import Path
import subprocess
import sys

TESTS = (
    "test_providers_env.py",
    "test_flow_cdp_preflight.py",
    "test_flow_contract.py",
    "test_flow_sw_env.py",
    "test_openrouter_image.py",
    "test_openrouter_audio.py",
    "test_openrouter_video.py",
)


def main():
    worktree = Path.cwd()
    if not (worktree / "custom" / "AGENTS.md").is_file():
        raise SystemExit("run from a MyMontage worktree")
    environment = {**os.environ, "PATH": "/usr/bin:/bin:" + os.environ.get("PATH", "")}
    for name in TESTS:
        print(f"Verifying {name}", flush=True)
        subprocess.run(
            [sys.executable, "-B", "-m", f"custom.tests.{Path(name).stem}"],
            cwd=worktree, env=environment, check=True,
        )
    print(f"{len(TESTS)} MyMontage verification entry points passed")


if __name__ == "__main__":
    main()
