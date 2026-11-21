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
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from custom.plugins.studiowright import cli, client
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
    unknown = flags - ALLOWED[key] - {"--"}
    assert not unknown, f"{key} would reject {sorted(unknown)}"
    return flags


def test_image_command() -> None:
    tool = FlowImage()
    flags = _check(tool.build_command({"prompt": "a red fox"}))
    assert {"--model", "--aspect", "--count", "--out", "--json"} <= flags
    assert "--size" not in flags
    assert "--size" in _check(tool.build_command({"prompt": "x", "size": "2K"}))
    with_refs = tool.build_command({"prompt": "x", "ref_paths": ["a.png", "b.png"]})
    assert with_refs.count("--ref") == 2


def test_video_command() -> None:
    tool = FlowVideo()
    flags = _check(tool.build_command({"prompt": "drone shot", "dry_run": True,
                                       "max_credits": 10.0}))
    assert {"--dry-run", "--max-credits"} <= flags
    argv = tool.build_command({"prompt": "x", "max_credits": 10.0})
    assert argv[argv.index("--max-credits") + 1] == "10", "sw --max-credits is INTEGER"
    ingredients = tool.build_command({"prompt": "x", "ref_paths": ["a.png"]})
    assert "--mode" in ingredients and ingredients.count("--ref") == 1
    result = tool.execute({"prompt": "x", "ref_paths": ["a.png"], "start_frame": "s.png"})
    assert not result.success and "either" in result.error
    assert "--duration" in _check(tool.build_command({
        "prompt": "x", "model": "omni-flash-1.2", "duration": 3,
    }))


def test_music_commands() -> None:
    tool = FlowMusic()
    flags = _check(tool.build_command({"action": "create", "prompt": "lofi", "lyrics": "la",
                                       "bpm": 90, "length": "1:30"}))
    assert {"--model", "--length", "--format", "--lyrics", "--bpm"} <= flags

    for action in ("stems", "variation"):
        flags = _check(tool.build_command({"action": action, "song_id": "last"}))
        assert not flags & {"--lyrics", "--title", "--bpm"}, f"{action} takes no lyrics/title"

    flags = _check(tool.build_command({"action": "trim", "song_id": "last", "prompt": "ignored",
                                       "start": "0:05", "end": "0:35"}))
    assert {"--start", "--end"} <= flags
    assert "--prompt" not in flags
    create = tool.build_command({"prompt": "trim"})
    assert _split(create)[0] == "music create" and create[-1] == "trim"

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
        assert "--json" in result.data["command"], tool.name
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


def test_invalid_plans_return_json_without_starting_sw() -> None:
    cases = (["image", "--prompt", ""], ["music", "--action", "trim"],
             ["video", "--prompt", "x", "--timeout", "0"])
    for argv in cases:
        buffer = io.StringIO()
        with patch.object(client, "_run", side_effect=AssertionError("unexpected subprocess")), \
                contextlib.redirect_stdout(buffer):
            code = cli.main([*argv, "--plan"])
        assert code == 1 and json.loads(buffer.getvalue())["status"] == "error", argv


@contextlib.contextmanager
def _fake_sw(handler):
    with patch("shutil.which", return_value="/fake/sw"), patch.object(client, "_run", side_effect=handler) as run:
        yield run


def _mock_result(cmd, body, code=0):
    return subprocess.CompletedProcess(cmd, code, json.dumps(body), "")


def test_empty_report_never_claims_old_files_in_any_tool() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        for name, content in (("old.jpg", b"\xff\xd8\xff"),
                              ("old.mp4", b"0000ftyp"), ("old.mp3", b"ID3")):
            (directory / name).write_bytes(content)
        for tool in (FlowImage(), FlowVideo(), FlowMusic()):
            with _fake_sw(lambda cmd, timeout: _mock_result(cmd, {})):
                result = tool.execute({"prompt": "x", "output_path": tmp})
            assert not result.success and not result.artifacts, tool.name


def test_reported_files_must_exist_and_have_the_right_media_header() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = root / "out"
        outside = root / "outside.jpg"
        outside.write_bytes(b"\xff\xd8\xffjpeg")
        out.mkdir()
        bad = out / "bad.jpg"
        bad.write_bytes(b"<html>an error page")

        def fake_run(cmd, timeout):
            job_id = cmd[cmd.index("--job-id") + 1]
            return _mock_result(cmd, {
                "job_id": job_id, "stage": "delivered",
                "files": [str(outside), str(bad), str(out / "missing.jpg")],
            })

        with _fake_sw(fake_run):
            result = FlowImage().execute({"prompt": "x", "output_path": str(out)})
        assert not result.success and not result.artifacts


def test_partial_batch_downloads_only_this_jobs_missing_id() -> None:
    first = "00000000-0000-4000-8000-000000000001"
    second = "00000000-0000-4000-8000-000000000002"
    project = "00000000-0000-4000-8000-000000000003"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        already_saved = out / "first.jpg"
        recovered = out / "second.jpg"
        already_saved.write_bytes(b"\xff\xd8\xfffirst")

        def fake_run(cmd, timeout):
            if "image" in cmd:
                job_id = cmd[cmd.index("--job-id") + 1]
                return _mock_result(cmd, {
                    "job_id": job_id, "stage": "generated", "project_id": project,
                    "files": [str(already_saved)], "media_ids": [first, second],
                    "file_by_media_id": {first: str(already_saved)},
                }, code=1)
            assert cmd[1:] == ["download", second, "--project", project, "--out", tmp, "--json"]
            recovered.write_bytes(b"\xff\xd8\xffsecond")
            return _mock_result(cmd, {
                "project_id": project, "files": [str(recovered)],
                "file_by_media_id": {second: str(recovered)},
            })

        with _fake_sw(fake_run) as run:
            result = FlowImage().execute({"prompt": "x", "count": 2, "output_path": tmp})
        assert result.success and result.data["recovered"] and run.call_count == 2
        assert result.artifacts == [str(already_saved), str(recovered)]


def test_music_partial_download_uses_the_confirmed_song_id() -> None:
    song = "00000000-0000-4000-8000-000000000004"
    with tempfile.TemporaryDirectory() as tmp:
        media = Path(tmp) / "song.mp3"

        def fake_run(cmd, timeout):
            if "--job-id" in cmd:
                job_id = cmd[cmd.index("--job-id") + 1]
                return _mock_result(cmd, {
                    "job_id": job_id, "stage": "generated", "files": [],
                    "songs": [{"id": song, "download_error": "no file"}],
                }, code=1)
            assert cmd[1:] == ["music", "download", song, "--format", "mp3", "--out", tmp, "--json"]
            media.write_bytes(b"ID3test")
            return _mock_result(cmd, {"files": [str(media)]})

        with _fake_sw(fake_run) as run:
            result = FlowMusic().execute({"prompt": "x", "output_path": tmp})
        assert result.success and result.data["recovered"] and run.call_count == 2
        assert result.data["songs"] == [{"id": song, "file": str(media)}]


def test_timeout_retries_observe_the_same_job_and_never_resubmit() -> None:
    commands = []
    with tempfile.TemporaryDirectory() as tmp:
        def fake_run(cmd, timeout):
            commands.append(cmd)
            if len(commands) == 1:
                raise subprocess.TimeoutExpired(cmd, timeout)
            job_id = commands[0][commands[0].index("--job-id") + 1]
            assert cmd[1:] == ["job", job_id, "--json"]
            return _mock_result(cmd, {
                "job_id": job_id, "status": "running",
                "result": {"stage": "submitted", "media_ids": []},
            })

        with _fake_sw(fake_run), patch.object(client.time, "sleep"):
            result = FlowImage().execute({
                "prompt": "x", "output_path": tmp, "timeout_seconds": 1, "status_retries": 1,
            })
        assert result.data["job_status"] == "pending" and not result.success
        assert len(commands) == 3


def test_video_quotes_require_a_number_and_accept_zero() -> None:
    for amount, expected in ((None, False), (0, True)):
        def fake_run(cmd, timeout, amount=amount):
            job_id = cmd[cmd.index("--job-id") + 1]
            return _mock_result(cmd, {"job_id": job_id, "submitted": False, "credits": amount})

        with _fake_sw(fake_run):
            result = FlowVideo().execute({"prompt": "x", "dry_run": True})
        assert result.success == expected


if __name__ == "__main__":
    checks = [function for name, function in globals().items() if name.startswith("test_")]
    for check in checks:
        check()
        print(f"ok  {check.__name__}")
    print(f"{len(checks)} contract checks passed")
