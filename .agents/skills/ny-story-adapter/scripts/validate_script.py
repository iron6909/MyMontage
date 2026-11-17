#!/usr/bin/env python3
"""Validate an ny-english-shorts episode JSON (10 scenes / 25 sentences).

Ported from ny-english-shorts workspace/qa/build_ep01.py asserts.
Read-only: never modifies the episode file.

Usage:
    python .agents/skills/ny-story-adapter/scripts/validate_script.py --episode <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCENE_COUNT = 10
SENTENCE_COUNT = 25
GLOSSARY_COUNT = 5
SPEAKERS = {"narrator", "leo", "carpenter", "cook"}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate ny episode JSON")
    parser.add_argument("--episode", required=True, help="path to episode JSON")
    args = parser.parse_args()

    path = Path(args.episode)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path}: {exc}")

    scenes = data.get("scenes", [])
    sentences = data.get("sentences", [])
    glossary = data.get("glossaryCard", [])

    # V5 split scene9 into 9a/9b is accepted (10 or 11 scenes).
    if len(scenes) not in (SCENE_COUNT, SCENE_COUNT + 1):
        fail(f"scenes: expected {SCENE_COUNT} (or {SCENE_COUNT + 1} with 9a/9b split), got {len(scenes)}")
    if len(sentences) != SENTENCE_COUNT:
        fail(f"sentences: expected {SENTENCE_COUNT}, got {len(sentences)}")
    if len(glossary) != GLOSSARY_COUNT:
        fail(f"glossaryCard: expected {GLOSSARY_COUNT}, got {len(glossary)}")

    scene_ids = {s["id"] for s in scenes}
    for scene in scenes:
        own = [s for s in sentences if s.get("sceneId") == scene["id"]]
        # V5 split beat scene9a carries a single reveal line; allow 1-3.
        if not 1 <= len(own) <= 3:
            fail(f"{scene['id']}: expected 1-3 sentences, got {len(own)}")

    for s in sentences:
        if s.get("sceneId") not in scene_ids:
            fail(f"sentence {s.get('id')}: unknown scene {s.get('sceneId')!r}")
        if s.get("speaker") not in SPEAKERS:
            fail(f"sentence {s.get('id')}: bad speaker {s.get('speaker')!r}")
        if s.get("speaker") != "narrator" and '"' not in s.get("text", ""):
            fail(f"sentence {s.get('id')}: role line without double quotes")

    for s in sentences:
        words = s.get("words", [])
        for a, b in zip(words, words[1:]):
            if not (a.get("end", 0) < b.get("start", 0)):
                fail(f"sentence {s.get('id')}: word timestamps not monotonic")
        if words and words[-1].get("end", 0) > s.get("endSec", 0) + 0.01:
            fail(f"sentence {s.get('id')}: last word ends after sentence end")

    for i in range(len(scenes) - 1):
        if not scenes[i].get("endSec", 0) <= scenes[i + 1].get("startSec", 0):
            fail(f"scenes {scenes[i]['id']} -> {scenes[i+1]['id']}: time overlap")

    for s in scenes:
        prompt = (s.get("prompt", "") or "").upper()
        if "SCENE" in prompt and "SCENE" in prompt.replace("SCENE", "SCENE", 1):
            # Reject prompts that leak internal ids like SCENE1/EP01.
            if "SCENE1" in prompt or "EP01" in prompt:
                fail(f"{s['id']}: prompt leaks internal ids")
        staging = ((s.get("visualAction", "") or "") + " " + (s.get("prompt", "") or "")).lower()
        if "on the left" not in staging and "on the right" not in staging:
            fail(f"{s['id']}: visualAction/prompt lacks left-right staging")

    # Reversal isolation: the three reversal beats must live in distinct
    # scene ids (no frame shared across attitude-reversal beats).
    # Accepts scene9 or the V5 scene9a/scene9b split.
    reversal_groups = [["scene5", "scene6"], ["scene7", "scene8"]]
    has_9split = "scene9a" in scene_ids or "scene9b" in scene_ids
    if has_9split:
        reversal_groups.append([s for s in ("scene9a", "scene9b", "scene10") if s in scene_ids])
    else:
        reversal_groups.append(["scene9", "scene10"])
    flat = [sid for g in reversal_groups for sid in g]
    if len(set(flat)) != len(flat):
        fail("reversal beats share a scene id")
    for sid in flat:
        if sid not in scene_ids:
            fail(f"reversal scene {sid} missing")

    print(
        f"PASS: {len(sentences)} sentences / {len(scenes)} scenes / "
        f"{len(glossary)} glossary chunks | reversals isolated | staging ok"
    )


if __name__ == "__main__":
    sys.exit(main())
