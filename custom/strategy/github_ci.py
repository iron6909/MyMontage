"""Resolve the upstream baseline for the hosted GitHub PR check."""

import os
import subprocess

from .verify import baseline_at, pinned_sha, require_ancestor

UPSTREAM_REPOSITORY = "https://github.com/OpenMontageInc/OpenMontage.git"


def main():
    base = pinned_sha(os.environ.get("BASE_SHA"), "target branch base")
    candidate = baseline_at("HEAD")
    previous = baseline_at(base)
    command = ["make", "-f", "custom/Makefile", "ci", f"BASE_SHA={base}"]
    if candidate != previous:
        subprocess.run(
            ["git", "fetch", "--no-tags", UPSTREAM_REPOSITORY, "refs/heads/main"],
            check=True,
        )
        require_ancestor(candidate, "FETCH_HEAD")
        command.append(f"UPSTREAM_SHA={candidate}")
    subprocess.run(command, check=True)


if __name__ == "__main__":
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
