"""Render `out/sample.mp4` from a sample copy using only local providers.

Local TTS comes from the registered `piper_tts` tool; the cards are drawn
locally with Pillow; the cut is assembled by the system FFmpeg. No network,
no API key, no paid provider takes part.

    .venv/bin/python custom/scripts/render_local_sample.py
    # or: make -f custom/Makefile sample   (from the repository root)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom.tools.audio.piper_tts import PiperTTS

# custom/scripts/ -> up three levels: scripts -> custom -> repo root.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
# Run as `custom/scripts/render_local_sample.py`, sys.path[0] is custom/scripts/,
# so the repo root has to be added before `tools.*` can be imported.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_OUT = ROOT_DIR / "out" / "sample.mp4"
DEFAULT_VOICE = "zh_CN-huayan-medium"

# The piper tool shells out to a `piper` binary; running under .venv/bin/python
# does not put .venv/bin on PATH by itself, and an unactivated venv must still work.
_VENV_BIN = str(Path(sys.executable).parent)
os.environ["PATH"] = _VENV_BIN + os.pathsep + os.environ.get("PATH", "")

SIZE = (1280, 720)
BG = "#0B1020"
INK = "#F4F6FB"
MUTED = "#8A93A8"
ACCENTS = ["#5B8CFF", "#38D39F", "#F5A623"]

# The sample copy. `text` is spoken by the local TTS, `lines` are drawn on the card.
SCENES = [
    {
        "kicker": "LOCAL SAMPLE · 01",
        "title": "全部在本机完成",
        "lines": [
            "这支短片由 OpenMontage 在本机渲染。",
            "配音、画面、剪辑，三段全部离线。",
        ],
        "text": "这支短片由 OpenMontage 在本机渲染。配音、画面、剪辑，三段全部离线。",
    },
    {
        "kicker": "LOCAL SAMPLE · 02",
        "title": "本地语音合成",
        "lines": [
            "配音来自 Piper 本地语音合成，",
            "不调用任何云端接口，也不需要 API Key。",
        ],
        "text": "配音来自 Piper 本地语音合成，不调用任何云端接口，也不需要 API 密钥。",
    },
    {
        "kicker": "LOCAL SAMPLE · 03",
        "title": "下一步换你的文案",
        "lines": [
            "修改 custom/scripts/render_local_sample.py 里的 SCENES，",
            "就能把这段示例换成你自己的内容。",
        ],
        "text": "修改 render local sample 脚本里的场景列表，就能把这段示例换成你自己的内容。",
    },
]

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def pick_binary(env_var: str, name: str) -> str:
    """First candidate that actually runs — PATH may hold a broken build."""
    candidates = [os.environ.get(env_var, ""), f"/usr/bin/{name}", shutil.which(name) or ""]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            if subprocess.run([candidate, "-version"], capture_output=True).returncode == 0:
                return candidate
    raise SystemExit(
        f"ERROR: no working {name} found. Install ffmpeg, or set {env_var}=/path/to/{name}."
    )


def pick_font(explicit: str | None) -> str:
    for candidate in [explicit, os.environ.get("LOCAL_SAMPLE_FONT", "")] + FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("ERROR: no usable font found. Set LOCAL_SAMPLE_FONT=/path/to/font.ttf.")


def probe_duration(ffprobe: str, path: Path) -> float:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return float(out)


def render_card(scene: dict, index: int, font_path: str, path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    accent = ACCENTS[index % len(ACCENTS)]

    draw.rectangle([0, 0, 14, SIZE[1]], fill=accent)
    kicker_font = ImageFont.truetype(font_path, 24, index=0)
    title_font = ImageFont.truetype(font_path, 64, index=0)
    body_font = ImageFont.truetype(font_path, 32, index=0)

    draw.text((80, 96), scene["kicker"], font=kicker_font, fill=MUTED)
    draw.text((80, 150), scene["title"], font=title_font, fill=INK)
    draw.line([80, 262, 80 + 120, 262], fill=accent, width=6)
    y = 320
    for line in scene["lines"]:
        draw.text((80, y), line, font=body_font, fill="#C9D1E3")
        y += 52

    counter_font = ImageFont.truetype(font_path, 22, index=0)
    draw.text((80, SIZE[1] - 76), f"{index + 1} / {len(SCENES)}", font=counter_font, fill=MUTED)
    draw.text(
        (SIZE[0] - 400, SIZE[1] - 76),
        "piper + pillow + ffmpeg",
        font=counter_font,
        fill=MUTED,
    )
    image.save(path)


def resolve_voice(voice: str) -> str:
    """Piper treats a bare name as "download into cwd"; hand it the real file."""
    voice_dir = Path(os.environ.get("PIPER_VOICES_DIR") or Path.home() / ".piper" / "models").expanduser()
    for base in (voice_dir, Path.cwd()):
        candidate = base / f"{voice}.onnx"
        if candidate.is_file():
            return str(candidate.resolve())
    raise SystemExit(
        f"ERROR: voice {voice!r} not found in {voice_dir}. "
        "Run custom/scripts/setup_local.sh first."
    )


def synthesise(tool: PiperTTS, text: str, voice: str, path: Path) -> None:
    """Local TTS through the registered piper tool (no network, no key)."""
    result = tool.execute({"text": text, "model": resolve_voice(voice), "output_path": str(path)})
    if not result.success:
        raise SystemExit(f"ERROR: local TTS failed: {result.error}")
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"ERROR: local TTS produced no audio at {path}")


def run(cmd: list[str]) -> None:
    print("  $ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render out/sample.mp4 with local-only providers.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output path (default: out/sample.mp4)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"piper voice (default: {DEFAULT_VOICE})")
    parser.add_argument("--font", default=None, help="card font path (default: first installed CJK font)")
    args = parser.parse_args(argv)

    from custom.tools.audio.piper_tts import PiperTTS
    from custom.tools.loader import load
    from tools.tool_registry import registry

    load()
    tool = registry.get(PiperTTS.name)
    if not isinstance(tool, PiperTTS):
        raise SystemExit(f"ERROR: {PiperTTS.name} is not registered.")

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = pick_binary("FFMPEG", "ffmpeg")
    ffprobe = pick_binary("FFPROBE", "ffprobe")
    font_path = pick_font(args.font)

    print(f"FFmpeg: {ffmpeg}")
    print(f"Voice:  {args.voice}")
    print(f"Font:   {font_path}")
    print(f"Output: {out_path}\n")

    with tempfile.TemporaryDirectory(prefix="local-sample-") as tmp:
        work = Path(tmp)
        durations: list[float] = []
        for index, scene in enumerate(SCENES):
            wav = work / f"seg{index}.wav"
            print(f"[{index + 1}/{len(SCENES)}] TTS + card: {scene['title']}")
            synthesise(tool, scene["text"], args.voice, wav)
            durations.append(probe_duration(ffprobe, wav))
            render_card(scene, index, font_path, work / f"card{index}.png")

        audio_list = work / "audio.txt"
        audio_list.write_text(
            "".join(f"file '{work / f'seg{i}.wav'}'\n" for i in range(len(SCENES))), encoding="utf-8"
        )
        narration = work / "narration.wav"
        run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(audio_list),
             "-c", "copy", str(narration)])

        image_list = work / "images.txt"
        entries = "".join(
            f"file '{work / f'card{i}.png'}'\nduration {durations[i]:.3f}\n" for i in range(len(SCENES))
        )
        # The concat demuxer needs the final frame repeated to hold its duration.
        image_list.write_text(
            entries + f"file '{work / f'card{len(SCENES) - 1}.png'}'\n", encoding="utf-8"
        )

        run([
            ffmpeg, "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(image_list),
            "-i", str(narration),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            str(out_path),
        ])

    total = sum(durations)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nDone: {out_path} ({size_mb:.1f} MB, {total:.1f}s across {len(SCENES)} scenes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
