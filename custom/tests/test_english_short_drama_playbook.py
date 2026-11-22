"""Offline contract tests for the A2 English short drama playbook and style."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYBOOK_DIR = REPO_ROOT / "custom" / "playbooks" / "english-short-drama"
STYLE_DIR = REPO_ROOT / "custom" / "styles" / "light-cinematic-a2-drama"
STYLE_PATH = STYLE_DIR / "light-cinematic-a2-drama.yaml"
TEMPLATE_PATH = PLAYBOOK_DIR / "template.json"
BUILDER_PATH = PLAYBOOK_DIR / "build_workspace.py"
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
            validate_palette,
            validate_type_hierarchy,
        )

        playbook = load_playbook("light-cinematic-a2-drama", styles_dir=STYLE_DIR)
        self.assertEqual(playbook["identity"]["name"], "Light Cinematic A2 Drama")
        self.assertEqual(playbook["identity"]["category"], "cinematic")

        palette_issues = validate_palette(playbook)
        blocking_palette = [i for i in palette_issues if i["severity"] in {"error", "warning"}]
        self.assertEqual(blocking_palette, [], f"Unexpected palette issues: {blocking_palette}")

        type_issues = validate_type_hierarchy(playbook)
        blocking_type = [i for i in type_issues if i["severity"] in {"error", "warning"}]
        self.assertEqual(blocking_type, [], f"Unexpected typography issues: {blocking_type}")

    def test_template_spec_and_playbook_doc_contracts(self):
        doc = (PLAYBOOK_DIR / "PLAYBOOK.md").read_text(encoding="utf-8")
        for token in (
            "pipeline_defs/animation.yaml",
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
                    "--style",
                    str(STYLE_PATH),
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
            self.assertEqual(summary["style_name"], "Light Cinematic A2 Drama")

            html = (workspace / "index.html").read_text(encoding="utf-8")
            self.assertIn('data-composition-id="main"', html)
            self.assertIn('data-width="1080"', html)
            self.assertIn('data-height="1920"', html)
            self.assertIn("--bg: #141210;", html)
            self.assertIn("--accent: #F5B942;", html)
            self.assertIn('--font-en: "Plus Jakarta Sans"', html)
            self.assertIn('--font-zh: "Noto Sans SC"', html)
            self.assertIn("--safe-bottom: 480px;", html)
            self.assertIn("--safe-right: 190px;", html)
            self.assertIn('class="caption-word is-key"', html)
            self.assertIn('class="caption-zh"', html)
            self.assertIn('window.__timelines["main"] = tl;', html)

            edit_decisions = json.loads(edit_decisions_path.read_text(encoding="utf-8"))
            jsonschema.validate(instance=edit_decisions, schema=schema)
            self.assertEqual(edit_decisions["render_runtime"], "hyperframes")
            self.assertEqual(edit_decisions["composition_mode"], "atelier")
            self.assertEqual(
                edit_decisions["bespoke"]["art_direction"],
                "custom/styles/light-cinematic-a2-drama/light-cinematic-a2-drama.yaml",
            )

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


if __name__ == "__main__":
    main()
