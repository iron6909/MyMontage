"""Offline contract tests for the A2 English short drama playbook and style."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main, skipUnless
from unittest.mock import patch

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_DIR = REPO_ROOT / "custom" / "playbooks" / "english-short-drama"
STYLE_DIR = REPO_ROOT / "custom" / "styles" / "light-cinematic-a2-drama"
STYLE_PATH = STYLE_DIR / "light-cinematic-a2-drama.yaml"
TEMPLATE_PATH = PLAYBOOK_DIR / "template.json"
BUILDER_PATH = PLAYBOOK_DIR / "build_workspace.py"
COMPRESS_PATH = PLAYBOOK_DIR / "compress_publish.py"
EDIT_DECISIONS_SCHEMA_PATH = REPO_ROOT / "schemas" / "artifacts" / "edit_decisions.schema.json"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location("english_short_drama_builder", BUILDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnglishShortDramaPlaybook(TestCase):
    def test_style_playbook_schema_and_accessibility(self):
        from styles.playbook_loader import (  # pi-lens-ignore: reportMissingImports
            load_playbook,
            validate_contrast,
            validate_type_hierarchy,
        )

        playbook = load_playbook("anime-ghibli")
        self.assertEqual(playbook["identity"]["name"], "Anime Ghibli")
        palette = playbook["visual_language"]["color_palette"]
        for text in (palette["text"], palette["accent"][0]):
            self.assertTrue(validate_contrast(text, palette["background"])["normal_text"]["AA"])

        type_issues = validate_type_hierarchy(playbook)
        blocking_type = [i for i in type_issues if i["severity"] in {"error", "warning"}]
        self.assertEqual(blocking_type, [], f"Unexpected typography issues: {blocking_type}")

    def test_template_spec_and_playbook_doc_contracts(self):
        doc = (PLAYBOOK_DIR / "PLAYBOOK.md").read_text(encoding="utf-8")
        for token in (
            "pipeline_defs/animation.yaml",
            "styles/anime-ghibli.yaml",
            "nano-banana-pro",
            "nano-banana-2.1",
            "flow_video",
            "flow_music",
            "google_gemini_tts",
            "gemini-2.5-flash-tts",
            "gemini-3.8-flash-tts",
            "openrouter_audio",
            "hyperframes",
        ):
            self.assertIn(token, doc)

        spec = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(spec["style_path"], "styles/anime-ghibli.yaml")
        self.assertEqual(spec["pipeline"], "animation")
        self.assertEqual(spec["visual_mode"], "mixed")
        self.assertEqual(spec["audio_architecture"], "narrator_plus_characters")
        self.assertEqual(spec["render_runtime"], "hyperframes")
        self.assertEqual(spec["composition_mode"], "atelier")
        self.assertEqual(spec["models"]["character_sheet_image"]["model"], "nano-banana-pro")
        self.assertEqual(spec["models"]["storyboard_image"]["model"], "nano-banana-2.1")
        self.assertEqual(spec["models"]["music"]["tool"], "flow_music")
        self.assertEqual(spec["models"]["tts_primary"]["tool"], "google_gemini_tts")
        self.assertEqual(spec["models"]["tts_primary"]["model"], "gemini-2.5-flash-tts")
        self.assertEqual(spec["models"]["tts_primary"]["backup_model"], "gemini-3.8-flash-tts")
        self.assertEqual(spec["models"]["tts_fallback"]["tool"], "openrouter_audio")

    def test_cli_builds_hyperframes_workspace_and_schema_valid_edit_decisions(self):
        schema = json.loads(EDIT_DECISIONS_SCHEMA_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "hyperframes"
            edit_decisions_path = Path(tmp) / "edit_decisions.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(BUILDER_PATH),
                    "--spec",
                    str(TEMPLATE_PATH),
                    "--workspace",
                    str(workspace),
                    "--emit-edit-decisions",
                    str(edit_decisions_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual(summary["style_name"], "Anime Ghibli")

            html = (workspace / "index.html").read_text(encoding="utf-8")
            self.assertIn('data-composition-id="main"', html)
            self.assertIn('data-width="1080"', html)
            self.assertIn('data-height="1920"', html)
            self.assertIn("--bg: #0A0A1A;", html)
            self.assertIn("--accent: #FFB347;", html)
            self.assertIn('--font-en: "Noto Serif JP"', html)
            self.assertIn('--font-zh: "Noto Sans", "Noto Sans SC", sans-serif;', html)
            self.assertIn("--safe-bottom: 480px;", html)
            self.assertIn("--safe-right: 190px;", html)
            self.assertIn('class="caption-word is-key"', html)
            self.assertIn('class="caption-zh"', html)
            self.assertNotIn('id="episode-badge"', html)
            self.assertIn('window.__timelines["main"] = tl;', html)

            edit_decisions = json.loads(edit_decisions_path.read_text(encoding="utf-8"))
            jsonschema.validate(instance=edit_decisions, schema=schema)
            self.assertEqual(edit_decisions["render_runtime"], "hyperframes")
            self.assertEqual(edit_decisions["composition_mode"], "atelier")
            self.assertEqual(
                edit_decisions["bespoke"]["art_direction"],
                "styles/anime-ghibli.yaml",
            )
            self.assertEqual(edit_decisions["subtitles"]["background"], "#0A0A1AD6")

    def test_staged_media_strict_guard_and_style_override(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "project"
            for rel in (
                "assets/images/scene_01_cafe.png",
                "assets/video/scene_02_twist.mp4",
                "assets/audio/line_01.wav",
                "assets/music/bgm.mp3",
            ):
                p = project_dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"stub")

            spec = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            spec["cues"][0]["audio_src"] = "assets/audio/line_01.wav"
            spec["bgm"]["src"] = "assets/music/bgm.mp3"
            spec_path = root / "episode.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            # Custom style override proves YAML values drive rendered output.
            from styles.playbook_loader import (  # pi-lens-ignore: reportMissingImports
                load_playbook,
            )

            alt_style = load_playbook("light-cinematic-a2-drama", styles_dir=STYLE_DIR)
            alt_style["visual_language"]["color_palette"]["accent"] = ["#FFAA33", "#E88D67"]
            alt_style_path = root / "alt-drama.yaml"
            alt_style_path.write_text(yaml.safe_dump(alt_style, sort_keys=False), encoding="utf-8")

            workspace = root / "hf"
            builder.build_workspace(
                spec_path=spec_path,
                workspace=workspace,
                style_path=alt_style_path,
                assets_base=project_dir,
                strict_media=True,
            )
            html = (workspace / "index.html").read_text(encoding="utf-8")
            self.assertIn("--accent: #FFAA33;", html)
            self.assertIn('<video id="scene-scene_02"', html)
            self.assertIn('<audio id="voice-cue_01"', html)
            self.assertIn('<audio id="bgm-track"', html)
            self.assertNotIn("<template", html)

            # Missing media fails loudly under strict_media=True.
            spec["scenes"][0]["src"] = "assets/images/missing.png"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                builder.build_workspace(
                    spec_path=spec_path,
                    workspace=root / "hf_strict_fail",
                    style_path=STYLE_PATH,
                    assets_base=project_dir,
                    strict_media=True,
                )

            # Relative path traversal is rejected.
            spec["scenes"][0]["src"] = "../outside.png"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            with self.assertRaises(ValueError):
                builder.build_workspace(
                    spec_path=spec_path,
                    workspace=root / "hf_traversal",
                    style_path=STYLE_PATH,
                    assets_base=project_dir,
                    strict_media=False,
                )

    def test_cover_card_shifts_timeline_and_edit_decisions(self):
        builder = _load_builder_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            spec.pop("style_path")
            orig_dur = float(spec["episode"]["duration_seconds"])
            orig_scene0_start = float(spec["scenes"][0]["start"])
            orig_cue0_start = float(spec["cues"][0]["start"])
            spec["cover"] = {
                "enabled": True,
                "duration_seconds": 2.0,
                "src": "assets/images/scene_01_cafe.png",
                "kicker": "CEFR A2 · ENGLISH SHORT DRAMA",
            }
            spec_path = root / "episode_cover.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")

            workspace = root / "hf_cover"
            ed_path = root / "edit_decisions.json"
            builder.build_workspace(
                spec_path=spec_path,
                workspace=workspace,
                emit_edit_decisions=ed_path,
            )
            html = (workspace / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="cover-card"', html)
            self.assertIn('class="cover-title-en"', html)
            self.assertIn('class="cover-title-zh"', html)
            self.assertNotIn('id="episode-badge"', html)
            self.assertIn(f'data-duration="{round(orig_dur + 2.0, 3):g}"', html)
            self.assertIn(
                f'id="scene-{spec["scenes"][0]["id"]}" class="clip scene-placeholder" '
                f'data-start="{round(orig_scene0_start + 2.0, 3):g}"',
                html,
            )

            ed = json.loads(ed_path.read_text(encoding="utf-8"))
            self.assertEqual(ed["bespoke"]["art_direction"], "styles/anime-ghibli.yaml")
            self.assertEqual(ed["cuts"][0]["id"], "cover")
            self.assertEqual(ed["cuts"][0]["out_seconds"], 2.0)
            self.assertEqual(ed["cuts"][1]["in_seconds"], round(orig_scene0_start + 2.0, 3))
            if ed.get("audio", {}).get("narration", {}).get("segments"):
                self.assertEqual(
                    ed["audio"]["narration"]["segments"][0]["start_seconds"],
                    round(orig_cue0_start + 2.0, 3),
                )


@skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class TestPublishCompression(TestCase):
    def test_cli_selects_smaller_publish_video_and_preserves_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp).resolve()
            renders = project / "renders"
            renders.mkdir()
            master = renders / "final.mp4"
            command = [sys.executable, str(COMPRESS_PATH), "--project-dir", str(project)]

            for source_crf, status in ((0, "compressed"), (42, "master_is_smaller_or_equal")):
                with self.subTest(source_crf=source_crf):
                    subprocess.run([
                        "ffmpeg", "-v", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
                        "-t", "1", "-c:v", "libx264", "-crf", str(source_crf),
                        "-c:a", "aac", "-b:a", "192k", str(master),
                    ], check=True, capture_output=True, text=True, timeout=30)
                    master_bytes = master.read_bytes()
                    run = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    report = json.loads(run.stdout)
                    self.assertEqual(report["status"], status)
                    published = Path(report["publish"]["path"])
                    self.assertEqual(report["publish"]["resolution"], "180x320")
                    self.assertEqual(report["publish"]["fps"], 30)
                    self.assertEqual(report["publish"]["audio_codec"], "aac")
                    self.assertEqual(master.read_bytes(), master_bytes)
                    if status == "compressed":
                        self.assertEqual(published, renders / "final-publish.mp4")
                        self.assertEqual(report["saved_bytes"], len(master_bytes) - published.stat().st_size)
                        self.assertGreater(report["saved_bytes"], 0)
                    else:
                        self.assertEqual(published, master)
                        self.assertEqual(report["saved_bytes"], 0)


class TestFlowStoryboardResume(TestCase):
    project = "00000000-0000-4000-8000-000000000001"
    media_id = "00000000-0000-4000-8000-000000000002"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.spec_path = self.root / "artifacts" / "episode_spec.json"
        self.spec_path.parent.mkdir()
        self.spec = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        self.spec.update(flow_project_id=self.project, flow_profile="drama")
        self.leo = next(c for c in self.spec["characters"] if c["id"] == "leo")
        self.leo["turnaround_media_id"] = self.media_id
        self.clara = next(c for c in self.spec["characters"] if c["id"] == "clara")
        self.clara["turnaround_sheet"] = "assets/images/char_clara_sheet.png"
        self.clara_sheet = self.root / self.clara["turnaround_sheet"]
        self.clara_sheet.parent.mkdir(parents=True)
        self.clara_sheet.write_bytes(b"placeholder for the offline command check")
        self.inputs = {"episode_spec": str(self.spec_path), "scene_id": "scene_02"}

    def _write_spec(self):
        self.spec_path.write_text(json.dumps(self.spec), encoding="utf-8")

    def test_cli_plan_and_execution_use_same_episode_context(self):
        from custom.tools.image.flow_image import FlowImage

        self._write_spec()
        result = subprocess.run(
            [sys.executable, "-m", "custom.plugins.studiowright", "image",
             "--episode-spec", str(self.spec_path), "--scene-id", "scene_02", "--plan"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        command = json.loads(result.stdout)["command"]
        self.assertEqual(command[1:3], ["--profile", "drama"])
        self.assertEqual(command[command.index("--project") + 1], self.project)
        self.assertEqual(command[command.index("--aspect") + 1], "9:16")
        self.assertEqual(command[command.index("--out") + 1], str(self.root / "assets/images/scene_02"))
        self.assertEqual(
            [command[index + 1] for index, arg in enumerate(command) if arg == "--ref"],
            [self.media_id, str(self.clara_sheet)],
        )
        self.assertIn("Reference image 1: Leo.\nReference image 2: Clara.", command[-1])
        self.assertIn("hand-painted watercolor aesthetic", command[-1])
        self.assertIn("Avoid: photorealistic", command[-1])
        self.assertIn(self.spec["scenes"][1]["storyboard_prompt"], command[-1])

        with patch("custom.tools.image.flow_image._sw_binary", return_value="/mock/bin/sw"), patch(
            "custom.tools.image.flow_image.invoke"
        ) as invoke:
            FlowImage().execute(self.inputs)
            self.assertEqual(invoke.call_args.args[0][1:], command[1:])
            self.assertEqual(invoke.call_args.kwargs["metadata"]["ref_paths"],
                             [self.media_id, str(self.clara_sheet)])

    def test_legacy_project_and_asset_ids_use_existing_manifest(self):
        from custom.tools.image.flow_image import FlowImage

        self.spec.pop("flow_project_id")
        self.spec.pop("flow_profile")
        self.spec["style_path"] = str(STYLE_PATH.relative_to(REPO_ROOT))
        self.spec["scenes"][1]["character_refs"] = ["char_clara", "leo"]
        self.spec["scenes"][1].pop("storyboard_prompt")
        self._write_spec()
        self.spec_path.with_name("asset_manifest.json").write_text(json.dumps({
            "metadata": {"flow_project_id": self.project},
            "assets": [{
                "id": "char_clara",
                "path": self.clara["turnaround_sheet"],
                "subtype": "character_turnaround_sheet",
            }],
        }), encoding="utf-8")

        result = FlowImage().execute({**self.inputs, "prompt": "A close-up by the awning.", "dry_run": True})
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.data["project_id"], self.project)
        self.assertEqual(result.data["ref_paths"], [str(self.clara_sheet), self.media_id])
        self.assertIn("Light-realistic indie short film still", result.data["prompt"])

    def test_missing_or_conflicting_references_do_not_submit(self):
        from custom.tools.image.flow_image import FlowImage

        self._write_spec()
        tool = FlowImage()
        with patch("custom.tools.image.flow_image.invoke") as invoke:
            self.assertIn("differs", tool.execute({**self.inputs, "project": self.media_id}).error)
            self.assertIn("differs", tool.execute({**self.inputs, "profile": "other"}).error)
            self.clara_sheet.unlink()
            self.assertIn("clara", tool.execute(self.inputs).error)
            self.spec["scenes"][1].pop("character_refs")
            self._write_spec()
            self.assertIn("must declare character_refs", tool.execute(self.inputs).error)
            invoke.assert_not_called()


if __name__ == "__main__":
    main()
