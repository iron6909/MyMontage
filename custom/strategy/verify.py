"""Check the upstream boundary and run MyMontage's offline regressions."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

POLICY_PATH = "custom/strategy/policy.json"
REQUIRED_FILE = "custom/AGENTS.md"
ARTIFACT_DIRS = {".task", "out", "projects"}
TESTS = (
    "test_providers_env.py",
    "test_flow_cdp_preflight.py",
    "test_flow_contract.py",
    "test_flow_sw_env.py",
    "test_openrouter_image.py",
    "test_openrouter_audio.py",
    "test_openrouter_video.py",
    "test_piper_tts.py",
)


def git(*args):
    return subprocess.check_output(
        ["git", *args], text=True, errors="surrogateescape",
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def require_ancestor(ancestor, head):
    if subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, head]).returncode:
        raise ValueError(f"{ancestor} must be an ancestor of {head}. Fetch the required commits and merge before verifying.")


def pinned_sha(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a full commit SHA")
    if git("cat-file", "-t", value).strip() != "commit":
        raise ValueError(f"{label} must refer to a commit")
    return value


def baseline_at(commit):
    policy = json.loads(git("show", f"{commit}:{POLICY_PATH}"))
    if (
        not isinstance(policy, dict)
        or policy.keys() != {"allow", "baseline", "required_file"}
        or policy["allow"] != ["custom"]
        or policy["required_file"] != REQUIRED_FILE
    ):
        raise ValueError(f"invalid downstream scope in {commit}:{POLICY_PATH}")
    return pinned_sha(policy.get("baseline"), f"{commit} upstream baseline")


def nul_paths(output):
    return filter(None, output.split("\0"))


def reject_paths(paths, message):
    if paths := sorted(set(paths)):
        names = ", ".join(repr(path) for path in paths)
        raise ValueError(f"{message}: {names}")


def verify_policy(base, upstream, require_clean):
    if Path(git("rev-parse", "--show-toplevel").strip()).resolve() != Path.cwd():
        raise ValueError("run from the MyMontage worktree root")
    head = git("rev-parse", "HEAD").strip()
    baseline = baseline_at(head)
    if expected_head := os.environ.get("STRATEGY_HEAD_SHA"):
        if expected_head != head:
            raise ValueError("checkout does not match STRATEGY_HEAD_SHA")
    if upstream and not base:
        raise ValueError("upstream sync verification requires --base")
    if base:
        base = pinned_sha(base, "target branch base")
        require_ancestor(base, head)
        previous = baseline_at(base)
        require_ancestor(previous, base)
        expected = pinned_sha(upstream, "upstream candidate") if upstream else previous
        if baseline != expected:
            raise ValueError(f"candidate baseline {baseline} does not match expected {expected}")
        if upstream:
            require_ancestor(previous, baseline)
    require_ancestor(baseline, head)

    if not git("ls-tree", head, "--", REQUIRED_FILE).startswith(("100644 blob ", "100755 blob ")):
        raise ValueError(f"{REQUIRED_FILE} must be a committed regular file")
    instructions = Path(REQUIRED_FILE)
    if not instructions.is_file() or instructions.is_symlink():
        raise ValueError(f"{REQUIRED_FILE} must be a regular file in the checkout")

    index = list(nul_paths(git("ls-files", "-v", "-z")))
    reject_paths(
        (entry[2:] for entry in index if entry.startswith("S ") or entry[0].islower()),
        "remove skip-worktree and assume-unchanged flags or use a clean checkout",
    )
    diff = ("diff", "--no-ext-diff", "--no-renames", "--name-only", "-z")
    upstream_custom_paths = set(nul_paths(git("ls-tree", "-r", "--name-only", "-z", baseline, "--", "custom")))
    comparisons = [(baseline, head), ("--cached", head), (), ("--cached", baseline)]
    if base and not upstream:
        comparisons.append((base, head))
    for comparison in comparisons:
        changes = list(nul_paths(git(*diff, *comparison, "--")))
        outside = (path for path in changes if not path.startswith("custom/"))
        reject_paths(outside, "changes outside custom/")
        reject_paths(upstream_custom_paths.intersection(changes), "changes to upstream-owned files under custom/")

    def is_artifact(path):
        return ARTIFACT_DIRS.intersection(PurePosixPath(path).parts)

    tracked_artifacts = [entry[2:] for entry in index if is_artifact(entry[2:])]
    reject_paths(tracked_artifacts, "runtime artifacts in the index")
    if base:
        for commit in git("rev-list", head, "--not", base, baseline).splitlines():
            files = git("ls-tree", "-r", "--name-only", "-z", commit, "--", "custom", *sorted(ARTIFACT_DIRS))
            reject_paths((path for path in nul_paths(files) if is_artifact(path)), f"runtime artifacts in commit {commit}")

    if require_clean and git("status", "--porcelain", "--untracked-files=all"):
        raise ValueError("CI requires a clean checkout of the candidate commit")
    print(f"MyMontage policy passed for {head} against OpenMontage {baseline}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=os.environ.get("STRATEGY_BASE_SHA"), help="trusted target branch commit")
    parser.add_argument("--upstream", help="trusted upstream candidate commit for a sync")
    parser.add_argument("--require-clean", action="store_true", help="require a clean CI checkout")
    parser.add_argument("--policy-only", action="store_true", help="skip the offline tool tests")
    args = parser.parse_args()
    try:
        verify_policy(args.base, args.upstream, args.require_clean)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"MyMontage policy failed: {error}\n")
    if args.policy_only:
        return

    environment = {**os.environ, "PATH": "/usr/bin:/bin:" + os.environ.get("PATH", "")}
    for name in TESTS:
        print(f"Verifying {name}", flush=True)
        subprocess.run(
            [sys.executable, "-B", "-m", f"custom.tests.{Path(name).stem}"],
            env=environment, check=True,
        )
    print(f"{len(TESTS)} MyMontage verification entry points passed")


if __name__ == "__main__":
    main()
