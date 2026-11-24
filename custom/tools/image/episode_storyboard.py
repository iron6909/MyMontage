"""Resolve a short-drama storyboard request using the episode workspace."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from custom.styles.episode_style import episode_style_path, load_episode_style


def _read_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return data


def _by_id(data: dict[str, Any], field: str) -> dict[str, dict[str, Any]]:
    rows = data.get(field)
    if not isinstance(rows, list):
        raise ValueError(f"Expected a {field} array")
    indexed = {}
    for row in rows:
        row_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(row_id, str) or not row_id.strip() or row_id in indexed:
            raise ValueError(f"Missing or duplicate ID in {field}: {row_id!r}")
        indexed[row_id] = row
    return indexed


def _character_reference(character: dict[str, Any], root: Path) -> str:
    media_id = character.get("turnaround_media_id")
    if media_id:
        try:
            return str(UUID(media_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Flow turnaround_media_id for {character['id']!r}") from exc
    sheet = character.get("turnaround_sheet")
    if isinstance(sheet, str) and sheet.strip():
        path = (root / sheet).resolve()
        if path.is_file() and path.stat().st_size > 0:
            return str(path)
    raise ValueError(f"Missing character reference for {character['id']!r}: {sheet!r}")


def resolve_storyboard(inputs: dict[str, Any]) -> dict[str, Any]:
    spec_path = inputs.get("episode_spec")
    scene_id = inputs.get("scene_id")
    if spec_path is None and scene_id is None:
        return inputs
    if not spec_path or not scene_id:
        raise ValueError("episode_spec and scene_id must be supplied together")
    if Path(scene_id).name != scene_id or scene_id in {".", ".."}:
        raise ValueError(f"Invalid storyboard scene ID: {scene_id!r}")

    path = Path(spec_path).expanduser().resolve()
    root = path.parent.parent  # The episode spec lives under artifacts/.
    if path.parent.name != "artifacts":
        raise ValueError("The episode spec must live in the episode's artifacts/ directory")
    spec = _read_object(path)
    if spec.get("playbook") != "english-short-drama":
        raise ValueError("Expected the english-short-drama episode spec")
    scene = _by_id(spec, "scenes").get(scene_id)
    if scene is None:
        raise ValueError(f"Unknown storyboard scene {scene_id!r}")
    character_ids = scene.get("character_refs")
    if not isinstance(character_ids, list) or any(
        not isinstance(ref, str) or not ref.strip() for ref in character_ids
    ):
        raise ValueError(f"Scene {scene_id!r} must declare character_refs (use [] for an empty shot)")

    characters = _by_id(spec, "characters")
    project = spec.get("flow_project_id")
    legacy_assets = {}
    manifest_path = path.with_name("asset_manifest.json")
    if (not project or any(ref not in characters for ref in character_ids)) and manifest_path.is_file():
        manifest = _read_object(manifest_path)
        metadata = manifest.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Expected an object for metadata in asset_manifest.json")
        project = project or metadata.get("flow_project_id")
        legacy_assets = {
            asset_id: asset["path"]
            for asset_id, asset in _by_id(manifest, "assets").items()
            if asset.get("subtype") == "character_turnaround_sheet" and asset.get("path")
        }
    if not isinstance(project, str) or not project.strip():
        raise ValueError("Record flow_project_id in episode_spec.json before generating storyboards")
    if inputs.get("project") and inputs["project"] != project:
        raise ValueError("Requested Flow project differs from the episode's flow_project_id")
    profile = spec.get("flow_profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ValueError("flow_profile must be a StudioWright profile name")
    if profile and inputs.get("profile") and inputs["profile"] != profile:
        raise ValueError("Requested StudioWright profile differs from the episode's flow_profile")

    prompt = inputs.get("prompt", scene.get("storyboard_prompt"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError(f"Scene {scene_id!r} needs a storyboard_prompt or an explicit prompt")
    art = load_episode_style(episode_style_path(spec))["asset_generation"]
    prefix = art.get("image_prompt_prefix", "").strip().rstrip(",")
    if prefix and not prompt.lstrip().startswith(prefix):
        prompt = f"{prefix}.\n{prompt.strip()}"
    exclude = art.get("image_negative_prompt", "").strip()
    if exclude:
        prompt = f"{prompt.strip()}\nAvoid: {exclude}."
    prompt = f"{prompt.strip()}\nKeep the frame free of captions, graphic overlays, and watermarks."

    reference_names: dict[str, list[str]] = {}
    for character_id in dict.fromkeys(character_ids):
        character = characters.get(character_id)
        name = character.get("display_name") if character else None
        if character is None and character_id in legacy_assets:
            # Older episode specs used asset IDs in character_refs.
            matches = [
                row for row in characters.values()
                if row.get("turnaround_sheet") == legacy_assets[character_id]
            ]
            if len(matches) == 1:
                character = matches[0]
                # A legacy sheet may depict more subjects than the cast entry.
                name = f"character sheet {character_id}"
        if character is None:
            raise ValueError(f"Unknown character reference {character_id!r} in scene {scene_id!r}")
        reference = _character_reference(character, root)
        reference_names.setdefault(reference, []).append(str(name or character["id"]))

    if reference_names:
        bindings = [
            f"Reference image {index}: {', '.join(names)}."
            for index, names in enumerate(reference_names.values(), 1)
        ]
        prompt = "\n".join([
            prompt.strip(), *bindings,
            "Use the references for the characters specified in this shot. "
            "Preserve their identity, facial features and hairstyle. "
            "Match their wardrobe and accessories except where the shot explicitly specifies a change.",
        ])
    models = spec.get("models", {})
    storyboard = models.get("storyboard_image", {}) if isinstance(models, dict) else None
    if not isinstance(storyboard, dict):
        raise ValueError("Expected an object for models.storyboard_image in episode_spec.json")
    return {
        **inputs,
        "project": project,
        "profile": profile or inputs.get("profile") or "default",
        "prompt": prompt,
        "ref_paths": list(dict.fromkeys([*reference_names, *(inputs.get("ref_paths") or [])])),
        "model": inputs.get("model", storyboard.get("model", "nano-banana-2.1")),
        "aspect_ratio": inputs.get("aspect_ratio", storyboard.get("aspect", "9:16")),
        "size": inputs.get("size", storyboard.get("size", "original")),
        "output_path": inputs.get("output_path") or str(root / "assets" / "images" / scene_id),
    }
