"""custom/tools/common/providers_env.py — fallback reader for ~/.harness/providers.env.

run-worker injects that file into the environment before it starts pi/agy. A direct
CLI run gets no such injection, so this module supplies the same settings *only*
when the process environment does not already define them. The path is fixed,
nothing is fetched over the network, and a repository `.env` is never consulted.

The grammar is deliberately explicit — `KEY=VALUE`, optional `export ` prefix,
optional matching quotes, `#` comments — because the file holds credentials:

  * no shell, no eval, no variable expansion, no command substitution;
  * the caller passes the exact key names it wants, so every other line in the
    file is ignored and cannot leak into the process;
  * line contents never reach a log or an exception message, only line numbers.

A missing, unreadable or malformed file is not fatal: the caller keeps its
built-in default, and a key-less live call still raises `MissingApiKey`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path

HARNESS_DIR = ".harness"
PROVIDERS_ENV_NAME = "providers.env"

_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_INLINE_COMMENT = re.compile(r"\s+#.*$")


def providers_env_path() -> Path:
    """The fixed trusted path, resolved per call so a temporary HOME is honoured."""
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(home) / HARNESS_DIR / PROVIDERS_ENV_NAME


def parse_providers_env(
    text: str, names: Iterable[str]
) -> tuple[dict[str, str], list[int]]:
    """Return ({requested key: value}, [line numbers skipped]).

    Skipped lines are reported by number only — the file holds credentials, so no
    line content is ever returned, logged or quoted.
    """
    wanted = set(names)
    values: dict[str, str] = {}
    skipped: list[int] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if match is None:
            skipped.append(number)
            continue
        key = match.group(1)
        value = _INLINE_COMMENT.sub("", match.group(2)).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in wanted:
            continue
        if value:
            values[key] = value
        else:
            skipped.append(number)  # `KEY=` with no value is not a usable setting
    return values, skipped


def load_local_env(names: Iterable[str]) -> dict[str, str]:
    """Read the trusted file. Absent, unreadable or malformed means "no values"."""
    try:
        text = providers_env_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values, _skipped = parse_providers_env(text, names)
    return values
