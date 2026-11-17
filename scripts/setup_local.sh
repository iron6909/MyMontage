#!/usr/bin/env bash
# Local-only OpenMontage setup: in-repo venv, requirements.txt (no GPU extras),
# a free offline TTS voice, and a working FFmpeg. No API keys, no paid providers.
# Safe to re-run: every step checks before it acts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"
VENV_PY="$VENV_DIR/bin/python"
PIPER_VOICE="${LOCAL_PIPER_VOICE:-zh_CN-huayan-medium}"
# piper-tts 1.2.0 is the release whose CLI matches tools/audio/piper_tts.py
# (--data-dir/--sentence-silence/--speaker). 1.3.0+ dropped --data-dir.
PIPER_TTS_SPEC="${PIPER_TTS_SPEC:-piper-tts==1.2.0}"
PIPER_VOICES_DIR="${PIPER_VOICES_DIR:-$HOME/.piper/models}"
# piper-tts 1.2.0 has no `piper.download_voices` module, so fetch the voice
# directly from the piper-voices repo. Override the base for a mirror.
PIPER_VOICES_BASE_URL="${PIPER_VOICES_BASE_URL:-https://huggingface.co/rhasspy/piper-voices/resolve/main}"
PYTHON_MIN="3.10"

log() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# pip_install <args...>: try the user's configured index, then a fallback index.
# Mirrors do go down (a stale pip.conf can point at a dead host), and that must
# not block a local setup.
PIP_FALLBACK_INDEX="${LOCAL_PIP_INDEX:-https://pypi.org/simple}"
pip_install() {
  "$VENV_PY" -m pip install --retries 2 --timeout 30 "$@" && return 0
  log "pip failed against the configured index; retrying with $PIP_FALLBACK_INDEX"
  "$VENV_PY" -m pip install --retries 2 --timeout 30 --index-url "$PIP_FALLBACK_INDEX" "$@"
}

# ---- 1. Interpreter -------------------------------------------------------
# piper-tts / onnxruntime ship wheels for CPython 3.10-3.13 only, so prefer a
# real 3.1x interpreter over a newer system default (e.g. 3.14+).
pick_python() {
  if [ -n "${PYTHON:-}" ]; then printf '%s' "$PYTHON"; return; fi
  for candidate in python3.12 python3.11 python3.13 python3.10 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
      command -v "$candidate"
      return
    fi
  done
  return 1
}

# Reuse an existing venv only if its interpreter still satisfies the floor.
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= tuple(map(int,'$PYTHON_MIN'.split('.'))) else 1)" 2>/dev/null; then
  log "Reusing existing virtual environment: $VENV_DIR ($("$VENV_PY" -V 2>&1))"
else
  BASE_PYTHON="$(pick_python)" || die "No Python 3.10-3.13 found. Set PYTHON=/path/to/python3.11 and re-run."
  log "Creating virtual environment: $VENV_DIR (base: $BASE_PYTHON, $("$BASE_PYTHON" -V 2>&1))"
  rm -rf "$VENV_DIR"
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

# ---- 2. Python dependencies ----------------------------------------------
log "Installing requirements.txt (CPU only; requirements-gpu.txt is not used)"
pip_install --upgrade pip >/dev/null
pip_install -r "$ROOT_DIR/requirements.txt"

# ---- 3. Local TTS --------------------------------------------------------
if [ -x "$VENV_DIR/bin/piper" ] && "$VENV_DIR/bin/piper" --help 2>&1 | grep -q -- '--data-dir'; then
  log "piper-tts already installed ($("$VENV_DIR/bin/piper" --version 2>&1 | head -1))"
else
  log "Installing $PIPER_TTS_SPEC (free, offline TTS)"
  pip_install "$PIPER_TTS_SPEC"
fi
mkdir -p "$PIPER_VOICES_DIR"
# <lang>_<REGION>-<speaker>-<quality> maps to
# <lang>/<lang>_<REGION>/<speaker>/<quality>/<voice>.{onnx,onnx.json}
VOICE_REGION="${PIPER_VOICE%%-*}"
VOICE_LANG="${VOICE_REGION%%_*}"
VOICE_REST="${PIPER_VOICE#*-}"
VOICE_QUALITY="${VOICE_REST##*-}"
VOICE_SPEAKER="${VOICE_REST%-*}"
VOICE_URL_BASE="$PIPER_VOICES_BASE_URL/$VOICE_LANG/$VOICE_REGION/$VOICE_SPEAKER/$VOICE_QUALITY"

fetch_file() {
  dest="$1"; url="$2"
  if [ -s "$dest" ]; then
    log "  already present: $dest"
    return 0
  fi
  curl -fL --retry 3 --retry-delay 2 -C - -o "$dest.part" "$url" || return 1
  mv "$dest.part" "$dest"
}

if [ -s "$PIPER_VOICES_DIR/$PIPER_VOICE.onnx" ]; then
  log "Piper voice already present: $PIPER_VOICES_DIR/$PIPER_VOICE.onnx"
else
  command -v curl >/dev/null 2>&1 || die "curl is required to download the Piper voice."
  log "Downloading Piper voice: $PIPER_VOICE -> $PIPER_VOICES_DIR"
  fetch_file "$PIPER_VOICES_DIR/$PIPER_VOICE.onnx.json" "$VOICE_URL_BASE/$PIPER_VOICE.onnx.json" \
    && fetch_file "$PIPER_VOICES_DIR/$PIPER_VOICE.onnx" "$VOICE_URL_BASE/$PIPER_VOICE.onnx" \
    || die "Piper voice download failed ($VOICE_URL_BASE). Set PIPER_VOICES_BASE_URL to a mirror, or LOCAL_PIPER_VOICE to another voice."
fi

# ---- 4. FFmpeg -----------------------------------------------------------
# A broken binary earlier on PATH is worse than no binary: probe candidates and
# keep the first one that actually runs.
FFMPEG_BIN=""; FFPROBE_BIN=""
for candidate in "${FFMPEG:-}" /usr/bin/ffmpeg "$(command -v ffmpeg || true)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  "$candidate" -version >/dev/null 2>&1 || continue
  FFMPEG_BIN="$candidate"; break
done
for candidate in "${FFPROBE:-}" /usr/bin/ffprobe "$(command -v ffprobe || true)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  "$candidate" -version >/dev/null 2>&1 || continue
  FFPROBE_BIN="$candidate"; break
done
[ -n "$FFMPEG_BIN" ] || die "No working ffmpeg found. Install it (apt install ffmpeg) or set FFMPEG=/path/to/ffmpeg."
[ -n "$FFPROBE_BIN" ] || die "No working ffprobe found. Install it (apt install ffmpeg) or set FFPROBE=/path/to/ffprobe."
log "FFmpeg: $FFMPEG_BIN"
log "FFprobe: $FFPROBE_BIN"
# Not `ffmpeg -encoders | grep -q`: grep exits on first match, ffmpeg takes
# SIGPIPE, and `set -o pipefail` then reports the pipeline as failed.
ENCODERS="$("$FFMPEG_BIN" -hide_banner -encoders 2>/dev/null || true)"
case "$ENCODERS" in
  *libx264*) ;;
  *) die "$FFMPEG_BIN has no libx264 encoder; sample rendering needs it." ;;
esac

# ---- 5. Provider posture (report only, no keys written) -------------------
log "Checking which providers are configured (free/local only by design)"
PATH="$VENV_DIR/bin:$PATH" "$VENV_PY" - <<'PY' || true
from tools.tool_registry import registry
registry.discover()
summary = registry.provider_menu_summary()
print("  composition runtimes:", summary.get("composition_runtimes"))
for cap in summary.get("capabilities", []):
    print(f"  {cap.get('capability')}: {cap.get('configured')}/{cap.get('total')} configured")
PY

log "Local setup complete."
log "Render the sample: .venv/bin/python scripts/render_local_sample.py   (= make sample)"
