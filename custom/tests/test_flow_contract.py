"""Contract check for the Google Flow tools against StudioWright 0.2.13.

Plain asserts, no pytest. Run from the repository root:

    python3 -m custom.tests.test_flow_contract

Every `--flag` the tools emit must exist in the corresponding `sw --help`, and
each action must emit the flags click makes *required* — two classes of bug this
catches: `sw --max-credits` rejecting a float, and `sw music trim/replace`
rejecting a command without `--start/--end`.
"""

from __future__ import annotations

import contextlib
import io
import json

from custom.plugins.studiowright import cli
from custom.tools.audio.flow_music import FlowMusic
from custom.tools.image.flow_image import FlowImage
from custom.tools.video.flow_video import FlowVideo

# Verbatim from `sw <cmd> --help` on StudioWright 0.2.13.
ALLOWED: dict[str, set[str]] = {
    "image": {"--model", "--aspect", "--count", "--ref", "--project", "--out", "--size", "--json"},
    "video": {"--model", "--mode", "--start", "--end", "--ref", "--aspect", "--resolution",
              "--duration", "--count", "--project", "--out", "--size", "--max-credits",
              "--dry-run", "--json"},
    "music create": {"--lyrics", "--lyrics-file", "--bpm", "--length", "--seed", "--model",
                     "--title", "--cover", "--cover-prompt", "--format", "--out", "--json"},
    "music stems": {"--format", "--out", "--json"},
    "music extend": {"--prompt", "--lyrics", "--lyrics-file", "--from", "--until", "--seed",
                     "--title", "--cover", "--cover-prompt", "--format", "--out", "--json"},
    "music cover": {"--prompt", "--lyrics", "--lyrics-file", "--seed", "--title", "--cover",
                    "--cover-prompt", "--format", "--out", "--json"},
    "music variation": {"--format", "--out", "--json"},
    "music trim": {"--start", "--end", "--format", "--out", "--json"},
    "music replace": {"--start", "--end", "--prompt", "--lyrics", "--lyrics-file", "--seed",
                      "--title", "--cover", "--cover-prompt", "--format", "--out", "--json"},
}


_MUSIC_ACTIONS = {"create", "stems", "extend", "cover", "variation", "trim", "replace"}


def _split(argv: list[str]) -> tuple[str, set[str], list[str]]:
    """Return (sw subcommand key, emitted --flags, positional args)."""
    command = next(a for a in argv if a in ("image", "video", "music"))
    index = argv.index(command)
    if command != "music":
        key, positionals = command, argv[index + 1:]
    elif argv[index + 1] in _MUSIC_ACTIONS:
        key, positionals = f"music {argv[index + 1]}", argv[index + 2:]
    else:
        # `sw music "SOUND"` is short for `sw music create "SOUND"`.
        key, positionals = "music create", argv[index + 1:]
    flags = {a for a in positionals if a.startswith("--")}
    return key, flags, [a for a in positionals if not a.startswith("--")]


def _check(argv: list[str]) -> set[str]:
    key, flags, _ = _split(argv)
    assert key in ALLOWED, f"unknown sw subcommand {key!r}"
    unknown = flags - ALLOWED[key]
    assert not unknown, f"{key} would reject {sorted(unknown)}"
    return flags


def test_image_command() -> None:
    tool = FlowImage()
    flags = _check(tool.build_command({"prompt": "a red fox"}))
    assert {"--model", "--aspect", "--count", "--size", "--out", "--json"} <= flags
    with_refs = tool.build_command({"prompt": "x", "ref_paths": ["a.png", "b.png"]})
    assert with_refs.count("--ref") == 2


def test_video_command() -> None:
    tool = FlowVideo()
    flags = _check(tool.build_command({"prompt": "drone shot", "dry_run": True,
                                       "max_credits": 10.0}))
    assert {"--dry-run", "--max-credits"} <= flags
    argv = tool.build_command({"prompt": "x", "max_credits": 10.0})
    assert argv[argv.index("--max-credits") + 1] == "10", "sw --max-credits is INTEGER"
    ingredients = tool.build_command({"prompt": "x", "ref_paths": ["a.png"],
                                      "start_frame": "s.png", "end_frame": "e.png"})
    assert "--mode" in ingredients and ingredients.count("--ref") == 1


def test_music_commands() -> None:
    tool = FlowMusic()
    flags = _check(tool.build_command({"action": "create", "prompt": "lofi", "lyrics": "la",
                                       "bpm": 90, "length": "1:30"}))
    assert {"--model", "--length", "--format", "--lyrics", "--bpm"} <= flags

    for action in ("stems", "variation"):
        flags = _check(tool.build_command({"action": action, "song_id": "last"}))
        assert not flags & {"--lyrics", "--title", "--bpm"}, f"{action} takes no lyrics/title"

    flags = _check(tool.build_command({"action": "trim", "song_id": "last",
                                       "start": "0:05", "end": "0:35"}))
    assert {"--start", "--end"} <= flags

    flags = _check(tool.build_command({"action": "replace", "song_id": "last", "prompt": "jazz",
                                       "start": "0:15", "end": "0:45", "lyrics": "la"}))
    assert {"--start", "--end", "--prompt", "--lyrics"} <= flags


def test_music_rejects_incomplete_edit() -> None:
    result = FlowMusic().execute({"action": "trim", "prompt": "x"})
    assert not result.success and "start" in (result.error or "")


def test_dry_run_touches_no_browser() -> None:
    """dry_run must return before any subprocess: no Chrome, no credits."""
    for tool, inputs in (
        (FlowImage(), {"prompt": "x", "dry_run": True}),
        (FlowMusic(), {"action": "create", "prompt": "x", "dry_run": True}),
    ):
        # pi-lens-ignore: python-sql-injection -- BaseTool.execute, not a SQL cursor
        result = tool.execute(inputs)
        assert result.success and result.data["dry_run"], tool.name
        assert result.data["command"][-1] == "--json", tool.name
        assert result.artifacts == [], f"{tool.name} dry run wrote a file"


def test_cli_plan_and_dry_run() -> int:
    for argv in (["image", "--prompt", "x", "--plan"],
                 ["video", "--prompt", "x", "--plan"],
                 ["music", "--prompt", "x", "--plan"],
                 ["image", "--prompt", "x", "--dry-run"],
                 ["music", "--prompt", "x", "--dry-run"]):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(argv)
        report = json.loads(buffer.getvalue())
        assert code == 0, (argv, report)
        assert report["status"] in ("plan", "dry_run"), (argv, report)
        assert report["command"], (argv, report)
    return 0


if __name__ == "__main__":
    for check in (test_image_command, test_video_command, test_music_commands,
                  test_music_rejects_incomplete_edit, test_dry_run_touches_no_browser,
                  test_cli_plan_and_dry_run):
        check()
        print(f"ok  {check.__name__}")
    print("6 contract checks passed")
