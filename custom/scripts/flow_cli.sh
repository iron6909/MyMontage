#!/usr/bin/env bash
# Launcher for custom/plugins/studiowright, callable from anywhere.
#
# The Flow tools import nothing but the stdlib, so any python >= 3.10 runs them:
# prefer the repo .venv, fall back to python3. Changing to the repo root is what
# makes `custom` importable — running the tool modules by path does not.
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
python="$repo_root/.venv/bin/python"
command -v "$python" >/dev/null 2>&1 || python=python3

cd "$repo_root"
exec "$python" -m custom.plugins.studiowright "$@"
