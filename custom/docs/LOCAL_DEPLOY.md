# Local Deploy — CPU-only, key-free OpenMontage

How to run OpenMontage on a machine with **no GPU, no API keys and no internet
at render time**. Everything below uses free/local providers only: local TTS,
locally drawn visuals, and FFmpeg. Nothing in this path talks to a paid API.

MyMontage keeps this deployment under `custom/`. Setup uses a downstream Piper
provider and leaves the upstream working tree untouched.

## Measured on this machine

| | |
|---|---|
| Setup, first full install | ~16 min (network-bound, ~200 kB/s) |
| Setup, idempotent re-run | ~3.5 s |
| Sample render | 2.90 s wall, 491 MB peak RSS (`/usr/bin/time -v`) |
| Output | `out/sample.mp4` — 336 KB, 18.23 s, 1280×720 h264 + AAC |

Those numbers come from the original MOZ-7 deploy; the raw logs, `ffprobe`
JSON and extracted frames live outside this repository (they are archived, not
shipped). Reproduce them locally with the commands below.

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

Run **from the repository root** — both make targets refuse to run anywhere
else, because the scripts resolve `tools.*`, `requirements.txt` and `out/`
against the root:

```bash
cd ~/MyMontage
make -f custom/Makefile local-setup     # or: bash custom/scripts/setup_local.sh
```

What it does, in order (every step checks before acting, so re-running is safe):

1. Reuses `.venv` if its interpreter is still ≥ 3.10, otherwise creates it from
   the best 3.10–3.13 interpreter available.
2. Installs `requirements.txt` and `custom/requirements.txt`. `requirements-gpu.txt` is
   deliberately not installed. If the configured pip index fails, one retry runs
   against `https://pypi.org/simple` (a stale mirror in `pip.conf` must not be
   able to block setup).
3. Installs `piper-tts==1.2.0` (the release whose CLI matches
   `custom/tools/audio/piper_tts.py`: `--data-dir`, `--sentence-silence`, `--speaker`;
   1.3.0+ removed `--data-dir`).
4. Installs the Piper voice `zh_CN-huayan-medium` into `~/.piper/models` unless it is already
   there. This is a direct, size-checked, resumable download from `rhasspy/piper-voices` on
   HuggingFace — piper-tts 1.2.0 has **no** `piper.download_voices` module. Override with
   `LOCAL_PIPER_VOICE=...`, or point `PIPER_VOICES_BASE_URL` at a mirror. Set
   `PIPER_VOICES_DIR` in both setup and render to share a different voice directory.
5. Probes `ffmpeg`/`ffprobe` for a binary that runs, and fails loudly if the
   chosen one lacks `libx264`.
6. Loads the upstream and custom providers and prints the provider posture
   (`composition_runtimes` + configured/total per capability).

No `.env` is written and no API key is ever added — cloud providers simply stay
unconfigured.

## Render the sample

```bash
make -f custom/Makefile sample
```

Result: `out/sample.mp4` — 1280×720, H.264 + AAC, 18.2 s across 3 scenes, ~336 KB.
Upstream ignores `.venv/` and `projects/`. Add `out/` and `.task/` to the local
`.git/info/exclude` for convenience. The downstream CI check rejects tracked
runtime artifacts independently of ignore settings.

Options — pass them through `ARGS`, or call the script directly:

```bash
make -f custom/Makefile sample ARGS="--out out/other.mp4"
make -f custom/Makefile sample ARGS="--voice en_US-lessac-medium"  # any voice in ~/.piper/models
.venv/bin/python custom/scripts/render_local_sample.py --font /path/to/font.ttf
```

The sample copy lives in `SCENES` at the top of
`custom/scripts/render_local_sample.py`. Each scene has `text` (spoken) and
`lines` (drawn on the card); edit those to swap in your own script. Changing a
card's line count does not require re-timing anything — each scene's duration is
taken from its own narrated audio length.

## Providers used by this path

| Role | Provider | How |
|---|---|---|
| Narration (TTS) | **Piper** (`mymontage_piper_tts`, `runtime=LOCAL`, `$0`) | Loaded explicitly into `tools.tool_registry` by the sample. Offline once the voice file is downloaded. |
| Visuals | **Pillow** title cards (`runtime=LOCAL`) | Drawn locally at render time; no image model, no stock API. |
| Composition | **FFmpeg** (`libx264` + `aac`, `runtime=LOCAL`) | One concat-demuxer pass over the cards plus the concatenated narration. |
| Optional richer visuals | **Remotion** (Node, `runtime=LOCAL`) | `make demo` — needs `cd remotion-composer && npm install`, and a voice track would have to be added separately. Not required for the sample. |

Deliberately **not** used: any image/video generation API, any cloud TTS, any
paid provider from `config.yaml`.

## Local Piper provider

The downstream `mymontage_piper_tts` provider omits `--speaker` for the default
speaker. Single-speaker voices such as `zh_CN-huayan-medium` then receive no
`sid` tensor. MyMontage consumers call `custom.tools.loader.load()` in the same
process and select `mymontage_piper_tts` explicitly. OpenMontage's default
discovery still provides its own `piper_tts`.

### Retire the legacy working-tree patch

Older setups applied the Piper fix to `tools/audio/piper_tts.py` and set
`skip-worktree`. If `git ls-files -v -- tools/audio/piper_tts.py` reports `S`,
compare the file and index with `HEAD` before restoring the upstream copy. The
following commands only restore the known legacy patch or a pristine file.
If they stop at a check, review any local changes first.

```bash
piper_file=tools/audio/piper_tts.py
head_blob=$(git rev-parse "HEAD:$piper_file")
index_blob=$(git rev-parse ":$piper_file")
worktree_blob=$(git hash-object -- "$piper_file")
legacy_blob=55addad13dc2a8c4224d311cc50f204d7a71d0e6
test "$index_blob" = "$head_blob" &&
  { test "$worktree_blob" = "$head_blob" || test "$worktree_blob" = "$legacy_blob"; } &&
  git update-index --no-skip-worktree -- "$piper_file" &&
  git restore --source=HEAD --worktree -- "$piper_file"
```

## Verification

```bash
ffprobe -v error -show_streams -show_format -of json out/sample.mp4
```

Expect one `video` stream (1280×720, h264) and one `audio` stream (aac), with
`format.duration` > 0.

## Google Flow (StudioWright)

Google Flow image / video / music generation is a separate, key-free path via the
`sw` CLI: see [FLOW.md](FLOW.md) for the entry points
(`python3 -m custom.plugins.studiowright ...`), the verified `sw` 0.2.13 contract,
and the credential-presence check (`make -f custom/Makefile sw-credentials`).
It needs a signed-in Chrome; nothing on this page changes because of it.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ffmpeg: error while loading shared libraries: libx265.so.216` | Homebrew FFmpeg shadowing the distro build on `PATH` | Nothing to do for the sample (it probes for a working binary). For other tools: `export FFMPEG=/usr/bin/ffmpeg FFPROBE=/usr/bin/ffprobe`. |
| `ERROR: run this from the repository root` | `make -f custom/Makefile ...` called from another directory | `cd` to the repo root first. |
| CI reports `skip-worktree` for `tools/audio/piper_tts.py` | The checkout still carries the old local patch | Follow [Retire the legacy working-tree patch](#retire-the-legacy-working-tree-patch). |
| `SSL: UNEXPECTED_EOF_WHILE_READING` from pip | Configured mirror is unreachable | Setup retries against `LOCAL_PIP_INDEX` (default pypi.org). To skip the dead-mirror leg, fix `~/.config/pip/pip.conf`. |
| `piper: error: unrecognized arguments: --data-dir` | A newer `piper-tts` was installed | `pip install 'piper-tts==1.2.0'` inside `.venv` |
| `Invalid input name: sid` | `--speaker` passed to a single-speaker voice | Select `mymontage_piper_tts` with the default speaker for that voice. |
| Piper downloads a 63 MB `.onnx` into the repo root | `--model` was a bare voice name; piper treats it as "download here" | Already handled: the sample script passes the absolute path to `~/.piper/models/<voice>.onnx`. |
| `ModuleNotFoundError: No module named 'tools'` | Script run without the repo root on `sys.path` | Already handled in `render_local_sample.py` (it walks up to the root from `custom/scripts/`). Use `make -f custom/Makefile sample`. |
| `ModuleNotFoundError: No module named 'custom'` from a Flow tool | The tool module was run by file path, so `sys.path` holds `custom/tools/...` and not the repo root | Run the documented entry instead: `python3 -m custom.plugins.studiowright <image\|video\|music> --help`, or `bash custom/scripts/flow_cli.sh`. See [FLOW.md](FLOW.md). |
| `ERROR: .../.venv/bin/python is missing` | No venv for this checkout | `make -f custom/Makefile local-setup` |
| `Piper voice download failed` | Voice name typo or no network | `LOCAL_PIPER_VOICE=en_US-lessac-medium bash custom/scripts/setup_local.sh` |
| TTS returns silence / empty WAV | Voice model and text language mismatch | Use a matching voice, e.g. `en_US-lessac-medium` with English copy |
