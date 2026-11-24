"""Resolve the shared style for English short-drama assets and composition."""

from pathlib import Path
from typing import Any

from jsonschema import ValidationError
from yaml import YAMLError

from styles.playbook_loader import load_playbook

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STYLE_PATH = REPO_ROOT / "styles" / "anime-ghibli.yaml"


def episode_style_path(spec: dict[str, Any]) -> Path:
    style_path = spec.get("style_path")
    return REPO_ROOT / style_path if style_path else DEFAULT_STYLE_PATH


def load_episode_style(style_path: Path) -> dict[str, Any]:
    path = style_path.resolve()
    try:
        return load_playbook(path.stem, styles_dir=path.parent)
    except (ValidationError, YAMLError) as exc:
        message = exc.message if isinstance(exc, ValidationError) else str(exc)
        raise ValueError(f"Invalid style YAML {path}: {message}") from exc
