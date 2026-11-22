"""Build a deterministic 9:16 HyperFrames atelier workspace for an A2 English short drama.

Loads the style playbook via OpenMontage's ``load_playbook(..., styles_dir=...)``,
renders ``index.html`` with bilingual EN-ZH word-by-word karaoke subtitles inside the
Xiaohongshu/Douyin safe zone, and optionally emits a schema-valid ``edit_decisions.json``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from html import escape
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STYLE_PATH = (
    REPO_ROOT
    / "custom"
    / "styles"
    / "light-cinematic-a2-drama"
    / "light-cinematic-a2-drama.yaml"
)


def _fmt_num(value: float) -> str:
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _load_style(style_path: Path) -> dict[str, Any]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from styles.playbook_loader import (  # pi-lens-ignore: reportMissingImports
        load_playbook,
    )

    resolved = style_path.resolve()
    return load_playbook(resolved.stem, styles_dir=resolved.parent)


def _stage_asset(
    src: str,
    workspace: Path,
    assets_base: Path | None,
    strict_media: bool,
) -> tuple[str, bool]:
    """Return ``(workspace_relative_src, file_exists)``; stage local files if found."""
    if not src:
        return "", False
    rel = Path(src)
    if not rel.is_absolute() and ".." in rel.parts:
        raise ValueError(f"Asset path must not traverse parent directories: {src!r}")
    if rel.is_absolute():
        if rel.is_file():
            target = workspace / "assets" / rel.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if rel.resolve() != target.resolve():
                shutil.copy2(rel, target)
            return f"assets/{rel.name}", True
        if strict_media:
            raise FileNotFoundError(f"Missing media asset: {src}")
        return src, False

    workspace_file = workspace / rel
    if workspace_file.is_file():
        return rel.as_posix(), True

    if assets_base is not None:
        candidate = assets_base / rel
        if candidate.is_file():
            workspace_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, workspace_file)
            return rel.as_posix(), True

    if strict_media:
        raise FileNotFoundError(
            f"Missing media asset {src!r} (checked {workspace} and {assets_base})"
        )
    return rel.as_posix(), False


def _cue_words(cue: dict[str, Any]) -> list[dict[str, Any]]:
    words = cue.get("words") or []
    if words:
        return words
    # Fallback uniform timing only for draft previews before Whisper alignment.
    tokens = [tok for tok in str(cue.get("en", "")).split() if tok]
    if not tokens:
        return []
    start = float(cue["start"])
    end = float(cue["end"])
    step = max(0.05, (end - start) / len(tokens))
    return [
        {
            "id": f"{cue['id']}_w{idx}",
            "text": tok,
            "start": round(start + idx * step, 3),
            "end": round(min(end, start + (idx + 1) * step), 3),
        }
        for idx, tok in enumerate(tokens)
    ]


def build_edit_decisions(
    spec: dict[str, Any],
    style: dict[str, Any],
    style_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    """Emit an upstream-schema-valid edit_decisions artifact for HyperFrames atelier."""
    palette = style["visual_language"]["color_palette"]
    typography = style["typography"]
    audio_style = style["audio"]
    transition_dur = style["motion"]["pacing_rules"].get(
        "transition_duration_seconds", 0.45
    )

    cuts: list[dict[str, Any]] = []
    for scene in spec.get("scenes", []):
        start = float(scene["start"])
        duration = float(scene["duration"])
        cut: dict[str, Any] = {
            "id": str(scene["id"]),
            "source": str(scene.get("src") or f"placeholder:{scene['id']}"),
            "in_seconds": start,
            "out_seconds": round(start + duration, 3),
            "layer": "primary",
            "transition_in": "crossfade" if start > 0 else "cut",
            "transition_duration": transition_dur if start > 0 else 0,
            "backgroundColor": palette["background"],
        }
        motion = scene.get("motion")
        if motion and motion != "none":
            cut["transform"] = {"animation": str(motion)}
        cuts.append(cut)

    audio_block: dict[str, Any] = {}
    narration_segments = [
        {
            "asset_id": str(cue["audio_src"]),
            "start_seconds": float(cue["start"]),
            "end_seconds": float(cue["end"]),
        }
        for cue in spec.get("cues", [])
        if cue.get("audio_src")
    ]
    if narration_segments:
        audio_block["narration"] = {"segments": narration_segments}

    bgm = spec.get("bgm") or {}
    if bgm.get("src"):
        audio_block["music"] = {
            "asset_id": str(bgm["src"]),
            "volume": float(bgm.get("base_volume", audio_style["music_volume"])),
            "fade_in_seconds": float(bgm.get("fade_in_seconds", 1.0)),
            "fade_out_seconds": float(bgm.get("fade_out_seconds", 1.2)),
            "ducking": {
                "enabled": True,
                "threshold_db": float(audio_style.get("ducking_threshold_db", -14)),
            },
        }

    try:
        style_ref = str(style_path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        style_ref = str(style_path)

    return {
        "version": "1.0",
        "cuts": cuts,
        "audio": audio_block,
        "subtitles": {
            "enabled": True,
            "style": "karaoke",
            "font": f"{typography['headings']['font']}, {typography['body']['font']}",
            "font_size": 46,
            "color": palette["text"],
            "background": "#141210D6",
            "position": "bottom-center",
            "max_words_per_line": 7,
        },
        "renderer_family": spec.get("renderer_family", "animation-first"),
        "render_runtime": "hyperframes",
        "composition_mode": "atelier",
        "bespoke": {
            "entry": str((workspace / "index.html").resolve()),
            "composition_id": "main",
            "art_direction": style_ref,
        },
        "metadata": {
            "playbook": spec.get("playbook", "english-short-drama"),
            "style_name": style["identity"]["name"],
            "style_path": style_ref,
            "episode_id": spec["episode"]["id"],
            "cefr_level": spec["episode"].get("cefr_level", "A2"),
            "models": spec.get("models", {}),
        },
    }


def render_html(
    spec: dict[str, Any],
    style: dict[str, Any],
    workspace: Path,
    assets_base: Path | None = None,
    strict_media: bool = False,
) -> str:
    """Render deterministic 9:16 HyperFrames atelier HTML from spec + style YAML."""
    ep = spec["episode"]
    width = int(ep.get("width", 1080))
    height = int(ep.get("height", 1920))
    duration = float(ep["duration_seconds"])
    safe = ep.get("safe_zone") or {}
    safe_top = int(safe.get("top_px", 260))
    safe_bottom = int(safe.get("bottom_px", 480))
    safe_left = int(safe.get("left_px", 96))
    safe_right = int(safe.get("right_px", 190))

    palette = style["visual_language"]["color_palette"]
    bg_color = palette["background"]
    text_color = palette["text"]
    muted_color = palette.get("muted", "#BFB3A2")
    primary_a = palette["primary"][0]
    primary_b = palette["primary"][-1]
    accent_gold = palette["accent"][0]
    accent_warm = palette["accent"][-1]

    headings_font = style["typography"]["headings"]["font"]
    body_font = style["typography"]["body"]["font"]
    vignette_enabled = bool(style["asset_generation"].get("default_vignette", True))
    default_bgm_vol = float(style["audio"].get("music_volume", 0.16))

    characters = {c["id"]: c for c in spec.get("characters", [])}

    scene_nodes: list[str] = []
    scene_motion_js: list[dict[str, Any]] = []
    for idx, scene in enumerate(spec.get("scenes", [])):
        sid = str(scene["id"])
        kind = str(scene.get("kind", "still"))
        start = float(scene["start"])
        sdur = float(scene["duration"])
        motion = str(scene.get("motion", "push-in" if kind == "still" else "none"))
        track = 1 + (idx % 2)
        src_rel, exists = _stage_asset(
            str(scene.get("src", "")), workspace, assets_base, strict_media
        )

        if kind == "video" and exists:
            scene_nodes.append(
                f'      <video id="scene-{escape(sid)}" class="clip scene-media" '
                f'src="{escape(src_rel)}" data-start="{_fmt_num(start)}" '
                f'data-duration="{_fmt_num(sdur)}" data-track-index="{track}" '
                f'muted playsinline></video>'
            )
        elif exists:
            scene_nodes.append(
                f'      <div id="scene-{escape(sid)}" class="clip scene-still" '
                f'data-start="{_fmt_num(start)}" data-duration="{_fmt_num(sdur)}" '
                f'data-track-index="{track}">\n'
                f'        <img id="img-{escape(sid)}" class="still-img" '
                f'src="{escape(src_rel)}" alt="" />\n'
                f"      </div>"
            )
            scene_motion_js.append(
                {"target": f"#img-{sid}", "start": start, "duration": sdur, "motion": motion}
            )
        else:
            prompt_note = escape(str(scene.get("storyboard_prompt", sid))[:140])
            scene_nodes.append(
                f'      <div id="scene-{escape(sid)}" class="clip scene-placeholder" '
                f'data-start="{_fmt_num(start)}" data-duration="{_fmt_num(sdur)}" '
                f'data-track-index="{track}">\n'
                f'        <div class="placeholder-inner">\n'
                f'          <div class="placeholder-kicker">'
                f"{escape(kind.upper())} · {escape(sid)}</div>\n"
                f'          <div class="placeholder-note">{prompt_note}</div>\n'
                f"        </div>\n"
                f"      </div>"
            )

    caption_nodes: list[str] = []
    cue_timeline_js: list[dict[str, Any]] = []
    audio_nodes: list[str] = []

    for gi, cue in enumerate(spec.get("cues", [])):
        cid = str(cue["id"])
        speaker = characters.get(str(cue.get("speaker_id", "")), {})
        speaker_name = str(speaker.get("display_name") or cue.get("speaker_id") or "")
        speaker_color = str(speaker.get("accent_color") or accent_warm)
        c_start = float(cue["start"])
        c_end = float(cue["end"])
        words = _cue_words(cue)
        kp = cue.get("key_phrase") or {}

        word_spans = []
        word_js = []
        for wi, w in enumerate(words):
            wid = f"w-{gi}-{wi}"
            is_key = bool(w.get("highlight"))
            cls = "caption-word is-key" if is_key else "caption-word"
            word_spans.append(
                f'<span id="{wid}" class="{cls}">{escape(str(w["text"]))}</span>'
            )
            word_js.append(
                {
                    "id": wid,
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                    "highlight": is_key,
                }
            )

        meta_parts: list[str] = []
        if speaker_name:
            meta_parts.append(
                f'<span class="speaker-pill" style="border-color:{escape(speaker_color)};'
                f'color:{escape(speaker_color)};">{escape(speaker_name)}</span>'
            )
        if kp.get("en"):
            kp_label = f"{kp['en']} · {kp['zh']}" if kp.get("zh") else str(kp["en"])
            meta_parts.append(
                f'<span class="key-phrase-pill">{escape(kp_label)}</span>'
            )
        meta_html = (
            f'          <div class="caption-meta">{"".join(meta_parts)}</div>\n'
            if meta_parts
            else ""
        )

        caption_nodes.append(
            f'        <div id="cg-{gi}" class="caption-group">\n'
            f"{meta_html}"
            f'          <div class="caption-en">{" ".join(word_spans)}</div>\n'
            f'          <div class="caption-zh">{escape(str(cue.get("zh", "")))}</div>\n'
            f"        </div>"
        )
        cue_timeline_js.append(
            {"index": gi, "start": c_start, "end": c_end, "words": word_js}
        )

        audio_src_rel, audio_exists = _stage_asset(
            str(cue.get("audio_src", "")), workspace, assets_base, strict_media
        )
        if audio_exists:
            audio_nodes.append(
                f'      <audio id="voice-{escape(cid)}" src="{escape(audio_src_rel)}" '
                f'data-start="{_fmt_num(c_start)}" '
                f'data-duration="{_fmt_num(max(0.1, c_end - c_start))}" '
                f'data-track-index="10" data-volume="1"></audio>'
            )

    bgm = spec.get("bgm") or {}
    bgm_rel, bgm_exists = _stage_asset(
        str(bgm.get("src", "")), workspace, assets_base, strict_media
    )
    bgm_js: dict[str, Any] | None = None
    if bgm_exists:
        base_vol = float(bgm.get("base_volume", default_bgm_vol))
        duck_vol = float(bgm.get("ducked_volume", round(base_vol * 0.5, 3)))
        audio_nodes.append(
            f'      <audio id="bgm-track" src="{escape(bgm_rel)}" '
            f'data-start="0" data-duration="{_fmt_num(duration)}" '
            f'data-track-index="11" data-volume="{_fmt_num(base_vol)}"></audio>'
        )
        bgm_js = {
            "base": base_vol,
            "ducked": duck_vol,
            "fadeIn": float(bgm.get("fade_in_seconds", 1.0)),
            "fadeOut": float(bgm.get("fade_out_seconds", 1.2)),
        }

    vignette_html = (
        '      <div id="film-vignette" class="clip vignette-layer" data-start="0" '
        f'data-duration="{_fmt_num(duration)}" data-track-index="5" '
        'data-layout-allow-occlusion></div>\n'
        if vignette_enabled
        else ""
    )

    title_en = escape(str(ep.get("title_en", "")))
    title_zh = escape(str(ep.get("title_zh", "")))
    cefr = escape(str(ep.get("cefr_level", "A2")))
    title_hold = min(3.6, duration)
    safe_width = max(320, width - safe_left - safe_right)

    font_families = list(dict.fromkeys(f for f in (headings_font, body_font) if f))
    google_fonts_query = "&".join(
        f"family={f.replace(' ', '+')}:wght@400;500;700" for f in font_families
    )
    google_fonts_link = (
        f'    <link rel="stylesheet" '
        f'href="https://fonts.googleapis.com/css2?{google_fonts_query}&display=swap" />\n'
        if google_fonts_query
        else ""
    )

    payload_json = json.dumps(
        {
            "duration": duration,
            "accent": accent_gold,
            "text": text_color,
            "scenes": scene_motion_js,
            "cues": cue_timeline_js,
            "bgm": bgm_js,
            "titleHold": title_hold,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <title>{title_en} — {title_zh}</title>
{google_fonts_link}    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      :root {{
        --bg: {bg_color};
        --primary-a: {primary_a};
        --primary-b: {primary_b};
        --accent: {accent_gold};
        --accent-warm: {accent_warm};
        --text: {text_color};
        --muted: {muted_color};
        --font-en: "{headings_font}", sans-serif;
        --font-zh: "{body_font}", sans-serif;
        --safe-top: {safe_top}px;
        --safe-bottom: {safe_bottom}px;
        --safe-left: {safe_left}px;
        --safe-right: {safe_right}px;
      }}
      * {{ box-sizing: border-box; }}
      html, body {{
        margin: 0;
        padding: 0;
        width: {width}px;
        height: {height}px;
        overflow: hidden;
        color: var(--text);
        font-family: var(--font-en);
      }}
      #root {{
        position: relative;
        width: {width}px;
        height: {height}px;
        overflow: hidden;
      }}
      .clip {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
      }}
      .bg-backdrop {{
        background: var(--bg);
        z-index: 0;
      }}
      .scene-media, .still-img {{
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }}
      .scene-media, .scene-still, .scene-placeholder {{
        z-index: 1;
      }}
      .scene-still {{
        overflow: hidden;
      }}
      .scene-placeholder {{
        display: flex;
        align-items: flex-start;
        justify-content: center;
        padding: calc(var(--safe-top) + 120px) var(--safe-right) var(--safe-bottom) var(--safe-left);
        background: linear-gradient(160deg, var(--primary-a) 0%, var(--primary-b) 55%, var(--bg) 100%);
      }}
      .placeholder-inner {{
        width: 100%;
        padding: 36px;
        border-radius: 20px;
        border: 2px solid rgba(245, 185, 66, 0.35);
        background: rgba(20, 18, 16, 0.52);
      }}
      .placeholder-kicker {{
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 0.08em;
        color: var(--accent);
        margin-bottom: 14px;
      }}
      .placeholder-note {{
        font-size: 32px;
        line-height: 1.45;
        color: var(--text);
      }}
      .vignette-layer {{
        z-index: 5;
        pointer-events: none;
        background:
          linear-gradient(180deg, rgba(20,18,16,0.48) 0%, rgba(20,18,16,0) 24%,
            rgba(20,18,16,0.12) 58%, rgba(20,18,16,0.78) 100%);
      }}
      .episode-badge-layer {{
        z-index: 6;
        padding: var(--safe-top) var(--safe-right) 0 var(--safe-left);
        display: flex;
        align-items: flex-start;
        gap: 14px;
        pointer-events: none;
      }}
      .badge-chip {{
        display: inline-flex;
        align-items: center;
        padding: 10px 20px;
        border-radius: 999px;
        background: rgba(20, 18, 16, 0.82);
        border: 2px solid var(--accent);
        color: var(--accent);
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 0.06em;
      }}
      .badge-title {{
        padding: 10px 22px;
        border-radius: 14px;
        background: rgba(20, 18, 16, 0.78);
        font-size: 28px;
        font-weight: 700;
        color: var(--text);
      }}
      .badge-title-zh {{
        margin-left: 10px;
        font-family: var(--font-zh);
        font-weight: 500;
        color: var(--muted);
      }}
      #caption-stage {{
        z-index: 7;
        pointer-events: none;
      }}
      .caption-group {{
        position: absolute;
        left: var(--safe-left);
        right: var(--safe-right);
        bottom: var(--safe-bottom);
        padding: 26px 30px;
        border-radius: 22px;
        background: rgba(20, 18, 16, 0.86);
        border: 1.5px solid rgba(247, 242, 232, 0.16);
        box-shadow: 0 14px 36px rgba(0, 0, 0, 0.45);
        opacity: 0;
        visibility: hidden;
        overflow: visible;
      }}
      .caption-meta {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 12px;
        margin-bottom: 14px;
      }}
      .speaker-pill {{
        display: inline-block;
        padding: 4px 14px;
        border-radius: 999px;
        border: 2px solid var(--accent-warm);
        font-size: 28px;
        font-weight: 700;
        letter-spacing: 0.03em;
      }}
      .key-phrase-pill {{
        display: inline-block;
        padding: 4px 14px;
        border-radius: 10px;
        background: var(--primary-a);
        border: 1.5px solid var(--accent);
        color: var(--text);
        font-family: var(--font-zh);
        font-size: 28px;
        font-weight: 500;
      }}
      .caption-en {{
        font-family: var(--font-en);
        font-size: 46px;
        font-weight: 700;
        line-height: 1.32;
        color: rgba(247, 242, 232, 0.62);
        display: flex;
        flex-wrap: wrap;
        column-gap: 12px;
        row-gap: 8px;
      }}
      .caption-word {{
        display: inline-block;
        white-space: nowrap;
        max-width: {safe_width}px;
        transform-origin: center bottom;
      }}
      .caption-word.is-key {{
        text-decoration: underline;
        text-decoration-color: var(--accent);
        text-underline-offset: 6px;
        text-decoration-thickness: 3px;
      }}
      .caption-zh {{
        margin-top: 14px;
        font-family: var(--font-zh);
        font-size: 32px;
        font-weight: 400;
        line-height: 1.42;
        color: var(--text);
      }}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{_fmt_num(duration)}"
      data-width="{width}"
      data-height="{height}"
    >
      <div id="bg-backdrop" class="clip bg-backdrop" data-start="0" data-duration="{_fmt_num(duration)}" data-track-index="0"></div>
{chr(10).join(scene_nodes)}
{vignette_html}      <div id="episode-badge" class="clip episode-badge-layer" data-start="0" data-duration="{_fmt_num(title_hold)}" data-track-index="6">
        <span class="badge-chip">{cefr} STORY</span>
        <span class="badge-title">{title_en}<span class="badge-title-zh">{title_zh}</span></span>
      </div>
      <section id="caption-stage" class="clip" data-start="0" data-duration="{_fmt_num(duration)}" data-track-index="7">
{chr(10).join(caption_nodes)}
      </section>
{chr(10).join(audio_nodes)}
    </div>
    <script>
      (function () {{
        const DATA = {payload_json};
        const tl = gsap.timeline({{ paused: true }});

        // Opening episode badge gentle fade out
        const badge = document.getElementById("episode-badge");
        if (badge && DATA.titleHold > 0.6) {{
          tl.fromTo(badge, {{ opacity: 0, y: -8 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: "power2.out" }}, 0);
          tl.to(badge, {{ opacity: 0, y: -6, duration: 0.35, ease: "power2.in" }}, Math.max(0.4, DATA.titleHold - 0.35));
        }}

        // Subtle filmic motion on still storyboards (no jarring jumps)
        DATA.scenes.forEach(function (scene) {{
          const el = document.querySelector(scene.target);
          if (!el) return;
          if (scene.motion === "push-in") {{
            tl.fromTo(el, {{ scale: 1.0, x: 0, y: 0 }}, {{ scale: 1.07, duration: scene.duration, ease: "none" }}, scene.start);
          }} else if (scene.motion === "pull-out") {{
            tl.fromTo(el, {{ scale: 1.07, x: 0, y: 0 }}, {{ scale: 1.0, duration: scene.duration, ease: "none" }}, scene.start);
          }} else if (scene.motion === "pan-right") {{
            tl.fromTo(el, {{ scale: 1.08, x: -24 }}, {{ scale: 1.08, x: 24, duration: scene.duration, ease: "none" }}, scene.start);
          }} else if (scene.motion === "pan-left") {{
            tl.fromTo(el, {{ scale: 1.08, x: 24 }}, {{ scale: 1.08, x: -24, duration: scene.duration, ease: "none" }}, scene.start);
          }}
        }});

        // Bilingual EN-ZH word-by-word karaoke cues
        DATA.cues.forEach(function (cue) {{
          const groupEl = document.getElementById("cg-" + cue.index);
          if (!groupEl) return;
          const exitAt = Math.max(cue.start + 0.15, cue.end - 0.14);

          tl.set(groupEl, {{ visibility: "visible", opacity: 0, y: 8 }}, cue.start);
          tl.to(groupEl, {{ opacity: 1, y: 0, duration: 0.18, ease: "power2.out" }}, cue.start);

          cue.words.forEach(function (word) {{
            const wordEl = document.getElementById(word.id);
            if (!wordEl) return;
            const activeScale = word.highlight ? 1.09 : 1.06;
            tl.to(wordEl, {{ color: DATA.accent, scale: activeScale, duration: 0.1, ease: "power2.out" }}, word.start);
            tl.to(wordEl, {{ color: DATA.text, scale: 1.0, duration: 0.12, ease: "power2.out" }}, word.end);
          }});

          tl.to(groupEl, {{ opacity: 0, y: -4, duration: 0.14, ease: "power2.in" }}, exitAt);
          tl.set(groupEl, {{ opacity: 0, visibility: "hidden" }}, cue.end);
        }});

        // BGM speech-aware ducking via timeline volume keyframes
        const bgmEl = document.getElementById("bgm-track");
        if (bgmEl && DATA.bgm) {{
          tl.set(bgmEl, {{ volume: 0 }}, 0);
          tl.to(bgmEl, {{ volume: DATA.bgm.base, duration: DATA.bgm.fadeIn, ease: "power1.out" }}, 0);
          DATA.cues.forEach(function (cue) {{
            tl.to(bgmEl, {{ volume: DATA.bgm.ducked, duration: 0.2, ease: "power1.out" }}, Math.max(0, cue.start - 0.15));
            tl.to(bgmEl, {{ volume: DATA.bgm.base, duration: 0.3, ease: "power1.inOut" }}, cue.end);
          }});
          tl.to(bgmEl, {{ volume: 0, duration: DATA.bgm.fadeOut, ease: "power1.in" }}, Math.max(0, DATA.duration - DATA.bgm.fadeOut));
        }}

        // Built-in caption exit self-lint
        DATA.cues.forEach(function (cue) {{
          const el = document.getElementById("cg-" + cue.index);
          if (!el) return;
          tl.seek(cue.end + 0.01);
          const computed = window.getComputedStyle(el);
          if (computed.opacity !== "0" && computed.visibility !== "hidden") {{
            console.warn("[caption-lint] cg-" + cue.index + " still visible at t=" + (cue.end + 0.01).toFixed(2) + "s");
          }}
        }});
        tl.seek(0);

        window.__timelines = window.__timelines || {{}};
        window.__timelines["main"] = tl;
      }})();
    </script>
  </body>
</html>
"""


def build_workspace(
    spec_path: Path,
    workspace: Path,
    style_path: Path | None = None,
    assets_base: Path | None = None,
    emit_edit_decisions: Path | None = None,
    strict_media: bool = False,
) -> dict[str, Any]:
    """Read episode spec + style YAML and write HyperFrames workspace (and optional edit_decisions)."""
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to read episode spec {spec_path}: {exc}") from exc
    resolved_style_path = style_path or (
        REPO_ROOT / spec["style_path"] if spec.get("style_path") else DEFAULT_STYLE_PATH
    )
    style = _load_style(resolved_style_path)

    workspace.mkdir(parents=True, exist_ok=True)
    html = render_html(
        spec,
        style,
        workspace=workspace,
        assets_base=assets_base,
        strict_media=strict_media,
    )
    index_path = workspace / "index.html"
    index_path.write_text(html, encoding="utf-8")

    result: dict[str, Any] = {
        "index_html": str(index_path),
        "style_name": style["identity"]["name"],
        "style_path": str(resolved_style_path),
        "episode_id": spec["episode"]["id"],
    }
    if emit_edit_decisions is not None:
        edit_decisions = build_edit_decisions(spec, style, resolved_style_path, workspace)
        emit_edit_decisions.parent.mkdir(parents=True, exist_ok=True)
        emit_edit_decisions.write_text(
            json.dumps(edit_decisions, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result["edit_decisions"] = str(emit_edit_decisions)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a 9:16 HyperFrames atelier workspace for an A2 English short drama."
    )
    parser.add_argument("--spec", required=True, type=Path, help="Episode spec JSON path")
    parser.add_argument("--workspace", required=True, type=Path, help="Target HyperFrames workspace dir")
    parser.add_argument(
        "--style",
        type=Path,
        default=None,
        help="Style YAML path (defaults to spec.style_path or light-cinematic-a2-drama.yaml)",
    )
    parser.add_argument(
        "--assets-base",
        type=Path,
        default=None,
        help="Project root used to stage relative asset paths into the workspace",
    )
    parser.add_argument(
        "--emit-edit-decisions",
        type=Path,
        default=None,
        help="Optional output path for schema-valid edit_decisions.json",
    )
    parser.add_argument(
        "--strict-media",
        action="store_true",
        help="Fail loudly if any referenced image/video/audio asset is missing",
    )
    args = parser.parse_args(argv)
    summary = build_workspace(
        spec_path=args.spec,
        workspace=args.workspace,
        style_path=args.style,
        assets_base=args.assets_base,
        emit_edit_decisions=args.emit_edit_decisions,
        strict_media=args.strict_media,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
