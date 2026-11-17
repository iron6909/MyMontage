# Local Deploy — CPU-only, key-free OpenMontage

How to run OpenMontage on a machine with **no GPU, no API keys and no internet
at render time**. Everything below uses free/local providers only: local TTS,
locally drawn visuals, and FFmpeg. Nothing in this path talks to a paid API.

## Measured on this machine

| | |
|---|---|
| Setup, first full install | ~16 min (network-bound, ~200 kB/s) |
| Setup, idempotent re-run | ~3.5 s |
| Sample render | 2.90 s wall, 491 MB peak RSS (`/usr/bin/time -v`) |
| Output | `out/sample.mp4` — 336 KB, 18.23 s, 1280×720 h264 + AAC |

Full logs, `ffprobe` JSON and extracted frames: `.task/evidence/`
(start at `SUMMARY.md`).

## This machine

| | |
|---|---|
| Host | LECOO-MINI |
| OS | Ubuntu 26.04 |
| GPU | none (integrated only) |
| RAM | ~8.8 GB usable |
| Repo | `~/MyMontage` (this checkout) |
| Node | v24.14.0 (only needed for the optional Remotion path) |

Two machine-specific facts drive the setup script:

1. **The `ffmpeg` first on `PATH` is broken.** Homebrew's build at
   `/home/linuxbrew/.linuxbrew/bin/ffmpeg` fails with
   `libx265.so.216: cannot open shared object file`. The working binaries are
   the distro ones: `/usr/bin/ffmpeg` and `/usr/bin/ffprobe` (FFmpeg 8.0.1,
   with `libx264` + `aac`). Both scripts probe candidates for a binary that
   actually runs instead of trusting `PATH`.
2. **The default `python3` is 3.15, but `piper-tts`/`onnxruntime` wheels stop
   at 3.13.** The setup script therefore prefers `python3.12/3.11/3.13/3.10`
   and only falls back to `python3`. Here it picks
   `/home/banko/.local/bin/python3.11`.

## Setup

```bash
cd ~/MyMontage
bash scripts/setup_local.sh      # or: make local-setup
```

What it does, in order (every step checks before acting, so re-running is safe):

1. Reuses `.venv` if its interpreter is still ≥ 3.10, otherwise creates it from
   the best 3.10–3.13 interpreter available.
2. `pip install -r requirements.txt` — **CPU only**, `requirements-gpu.txt` is
   deliberately not installed. If the configured pip index fails, one retry runs
   against `https://pypi.org/simple` (a stale mirror in `pip.conf` must not be
   able to block setup).
3. Installs `piper-tts==1.2.0` (the release whose CLI matches
   `tools/audio/piper_tts.py`: `--data-dir`, `--sentence-silence`, `--speaker`;
   1.3.0+ removed `--data-dir`).
4. Installs the Piper voice `zh_CN-huayan-medium` into `~/.piper/models` unless it is already
   there. This is a direct, size-checked, resumable download from `rhasspy/piper-voices` on
   HuggingFace — piper-tts 1.2.0 has **no** `piper.download_voices` module. Override with
   `LOCAL_PIPER_VOICE=...`, or point `PIPER_VOICES_BASE_URL` at a mirror.
5. Probes `ffmpeg`/`ffprobe` for a binary that runs, and fails loudly if the
   chosen one lacks `libx264`.
6. Prints the provider posture (`composition_runtimes` + configured/total per
   capability) so you can see that only free/local providers are live.

No `.env` is written and no API key is ever added — cloud providers simply stay
unconfigured.

## Render the sample

```bash
.venv/bin/python scripts/render_local_sample.py    # or: make sample
```

Result: `out/sample.mp4` — 1280×720, H.264 + AAC, 18.2 s across 3 scenes, ~336 KB.
Both `out/` and `.venv/` are gitignored.

Options:

```bash
.venv/bin/python scripts/render_local_sample.py --out out/other.mp4
.venv/bin/python scripts/render_local_sample.py --voice en_US-lessac-medium   # any voice in ~/.piper/models
.venv/bin/python scripts/render_local_sample.py --font /path/to/font.ttf
```

The sample copy lives in `SCENES` at the top of
`scripts/render_local_sample.py`. Each scene has `text` (spoken) and
`lines` (drawn on the card); edit those to swap in your own script. Changing a
card's line count does not require re-timing anything — each scene's duration is
taken from its own narrated audio length.

## Providers used by this path

| Role | Provider | How |
|---|---|---|
| Narration (TTS) | **Piper** (`piper_tts` tool, `runtime=LOCAL`, `$0`) | Resolved through `tools.tool_registry` — the same tool the agent contract uses, not a detour around it. Offline once the voice file is downloaded. |
| Visuals | **Pillow** title cards (`runtime=LOCAL`) | Drawn locally at render time; no image model, no stock API. |
| Composition | **FFmpeg** (`libx264` + `aac`, `runtime=LOCAL`) | One concat-demuxer pass over the cards plus the concatenated narration. |
| Optional richer visuals | **Remotion** (Node, `runtime=LOCAL`) | `make demo` — needs `cd remotion-composer && npm install`, and a voice track would have to be added separately. Not required for the sample. |

The TTS half of this path needed one upstream fix: `piper_tts` used to always pass
`--speaker <id>`, which makes piper send a `sid` tensor that single-speaker voices
(`zh_CN-huayan-medium` among them) do not accept — ONNX Runtime answers
`Invalid input name: sid`. The tool now passes `--speaker` only for a non-zero id.

Deliberately **not** used: any image/video generation API, any cloud TTS, any
paid provider from `config.yaml`.

## Verification

```bash
ffprobe -v error -show_streams -show_format -of json out/sample.mp4
```

Expect one `video` stream (1280×720, h264) and one `audio` stream (aac), with
`format.duration` > 0. Measured numbers for this deploy are in
`.task/evidence/` (see `evidence/SUMMARY.md`).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ffmpeg: error while loading shared libraries: libx265.so.216` | Homebrew FFmpeg shadowing the distro build on `PATH` | Nothing to do for the sample (it probes for a working binary). For other tools: `export FFMPEG=/usr/bin/ffmpeg FFPROBE=/usr/bin/ffprobe`. |
| `SSL: UNEXPECTED_EOF_WHILE_READING` from pip | Configured mirror is unreachable | Setup retries against `LOCAL_PIP_INDEX` (default pypi.org). To skip the dead-mirror leg, fix `~/.config/pip/pip.conf`. |
| `piper: error: unrecognized arguments: --data-dir` | A newer `piper-tts` was installed | `pip install 'piper-tts==1.2.0'` inside `.venv` |
| `Invalid input name: sid` | `--speaker` passed to a single-speaker voice | Already handled: the tool only passes `--speaker` for a non-zero id. |
| Piper downloads a 63 MB `.onnx` into the repo root | `--model` was a bare voice name; piper treats it as "download here" | Already handled: the sample script passes the absolute path to `~/.piper/models/<voice>.onnx`. |
| `ModuleNotFoundError: No module named 'tools'` | Script run with `sys.path[0] = scripts/` | Already handled in `render_local_sample.py`. Use `.venv/bin/python scripts/...` or `make sample`. |
| `Piper voice download failed` | Voice name typo or no network | `LOCAL_PIPER_VOICE=en_US-lessac-medium bash scripts/setup_local.sh` |
| TTS returns silence / empty WAV | Voice model and text language mismatch | Use a matching voice, e.g. `en_US-lessac-medium` with English copy |
