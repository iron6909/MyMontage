# Local Deploy — CPU-only, key-free OpenMontage

How to run OpenMontage on a machine with **no GPU, no API keys and no internet
at render time**. Everything below uses free/local providers only: local TTS,
locally drawn visuals, and FFmpeg. Nothing in this path talks to a paid API.

This deploy is **not** part of OpenMontage upstream. It is quarantined in
`custom/` — scripts, docs, the Piper patch and the patched tool copy — so a
pull from upstream stays a pure fast-forward as far as the tree is concerned.

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

1. Mounts the local Piper patch on `tools/audio/piper_tts.py` (see
   [Local Piper patch](#local-piper-patch-not-committed)). Fails fast, before
   installing anything, if the file is not the expected upstream original.
2. Reuses `.venv` if its interpreter is still ≥ 3.10, otherwise creates it from
   the best 3.10–3.13 interpreter available.
3. `pip install -r requirements.txt` — **CPU only**, `requirements-gpu.txt` is
   deliberately not installed. If the configured pip index fails, one retry runs
   against `https://pypi.org/simple` (a stale mirror in `pip.conf` must not be
   able to block setup).
4. Installs `piper-tts==1.2.0` (the release whose CLI matches
   `tools/audio/piper_tts.py`: `--data-dir`, `--sentence-silence`, `--speaker`;
   1.3.0+ removed `--data-dir`).
5. Installs the Piper voice `zh_CN-huayan-medium` into `~/.piper/models` unless it is already
   there. This is a direct, size-checked, resumable download from `rhasspy/piper-voices` on
   HuggingFace — piper-tts 1.2.0 has **no** `piper.download_voices` module. Override with
   `LOCAL_PIPER_VOICE=...`, or point `PIPER_VOICES_BASE_URL` at a mirror.
6. Probes `ffmpeg`/`ffprobe` for a binary that runs, and fails loudly if the
   chosen one lacks `libx264`.
7. Prints the provider posture (`composition_runtimes` + configured/total per
   capability) so you can see that only free/local providers are live.

No `.env` is written and no API key is ever added — cloud providers simply stay
unconfigured.

## Render the sample

```bash
make -f custom/Makefile sample
```

Result: `out/sample.mp4` — 1280×720, H.264 + AAC, 18.2 s across 3 scenes, ~336 KB.
`.venv/`, `out/`, `projects/` and `.task/` are git-excluded (they are listed in
`.git/info/exclude`, so pulling upstream `.gitignore` cannot un-ignore them).

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
| Narration (TTS) | **Piper** (`piper_tts` tool, `runtime=LOCAL`, `$0`) | Resolved through `tools.tool_registry` — the same tool the agent contract uses, not a detour around it. Offline once the voice file is downloaded. |
| Visuals | **Pillow** title cards (`runtime=LOCAL`) | Drawn locally at render time; no image model, no stock API. |
| Composition | **FFmpeg** (`libx264` + `aac`, `runtime=LOCAL`) | One concat-demuxer pass over the cards plus the concatenated narration. |
| Optional richer visuals | **Remotion** (Node, `runtime=LOCAL`) | `make demo` — needs `cd remotion-composer && npm install`, and a voice track would have to be added separately. Not required for the sample. |

Deliberately **not** used: any image/video generation API, any cloud TTS, any
paid provider from `config.yaml`.

## Local Piper patch (not committed)

`piper_tts` upstream always passes `--speaker <id>`, which makes piper send a
`sid` tensor that single-speaker voices (`zh_CN-huayan-medium` among them) do
not accept — ONNX Runtime answers `Invalid input name: sid`. The fix: pass
`--speaker` only for a non-zero id.

That fix must **not** land in `tools/audio/piper_tts.py` in the commit. It lives
in two places instead:

| Path | What it is |
|---|---|
| `custom/patches/piper_tts_speaker.patch` | The diff, generated with `git diff ce284cf~1 ce284cf -- tools/audio/piper_tts.py` |
| `custom/tools/audio/piper_tts.py` | The full patched copy, used as the "already applied" reference |

`custom/scripts/setup_local.sh` mounts it, idempotently:

- file already equals the patched copy → skip the apply, make sure
  `skip-worktree` is set;
- file is the pristine upstream original → `git apply` the patch, then
  `git update-index --skip-worktree tools/audio/piper_tts.py`;
- anything else (upstream changed the file, or it carries other local edits) →
  **stop, apply nothing, exit non-zero** with the recovery steps.

Check the state at any time:

```bash
git ls-files -v tools/audio/piper_tts.py   # 'S' prefix = skip-worktree set
```

### Keeping the patch in sync — skip-worktree is not a merge guarantee

`skip-worktree` only hides the local edit so `git status` stays clean. It does
**not** make a pull conflict-free, and it does not protect you from a lost local
edit — if upstream touches the same `--speaker` block, the merge still has to be
resolved by hand. Do not assume `--no-skip-worktree` alone is enough.

Before syncing:

```bash
git update-index --no-skip-worktree -- tools/audio/piper_tts.py
git checkout HEAD -- tools/audio/piper_tts.py     # drop the local patch, keep the file
git pull                                          # or rebase/merge as usual
```

Then, only after the sync:

```bash
make -f custom/Makefile local-setup               # re-applies the patch, re-sets skip-worktree
```

If upstream changed the same region, setup refuses to patch (it compares the
committed blob against the original the patch was built on, `4159595`). In that
case resolve by hand against `custom/tools/audio/piper_tts.py`, or drop the
patch entirely if upstream fixed the speaker bug itself.

Never `git add` `tools/audio/piper_tts.py` while it is patched — that is exactly
how the local hack would leak into a commit. To review or commit it:

```bash
git update-index --no-skip-worktree -- tools/audio/piper_tts.py
git checkout HEAD -- tools/audio/piper_tts.py
git diff HEAD -- tools/audio/piper_tts.py         # must be empty
```

## Verification

```bash
ffprobe -v error -show_streams -show_format -of json out/sample.mp4
```

Expect one `video` stream (1280×720, h264) and one `audio` stream (aac), with
`format.duration` > 0.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ffmpeg: error while loading shared libraries: libx265.so.216` | Homebrew FFmpeg shadowing the distro build on `PATH` | Nothing to do for the sample (it probes for a working binary). For other tools: `export FFMPEG=/usr/bin/ffmpeg FFPROBE=/usr/bin/ffprobe`. |
| `ERROR: run this from the repository root` | `make -f custom/Makefile ...` called from another directory | `cd` to the repo root first. |
| `refusing to patch tools/audio/piper_tts.py` | The file is not the pristine upstream original — upstream moved, or it has other local edits | Follow [Keeping the patch in sync](#keeping-the-patch-in-sync--skip-worktree-is-not-a-merge-guarantee). Do not force the patch. |
| `SSL: UNEXPECTED_EOF_WHILE_READING` from pip | Configured mirror is unreachable | Setup retries against `LOCAL_PIP_INDEX` (default pypi.org). To skip the dead-mirror leg, fix `~/.config/pip/pip.conf`. |
| `piper: error: unrecognized arguments: --data-dir` | A newer `piper-tts` was installed | `pip install 'piper-tts==1.2.0'` inside `.venv` |
| `Invalid input name: sid` | `--speaker` passed to a single-speaker voice | The patch is not mounted. Run `make -f custom/Makefile local-setup`. |
| Piper downloads a 63 MB `.onnx` into the repo root | `--model` was a bare voice name; piper treats it as "download here" | Already handled: the sample script passes the absolute path to `~/.piper/models/<voice>.onnx`. |
| `ModuleNotFoundError: No module named 'tools'` | Script run without the repo root on `sys.path` | Already handled in `render_local_sample.py` (it walks up to the root from `custom/scripts/`). Use `make -f custom/Makefile sample`. |
| `ERROR: .../.venv/bin/python is missing` | No venv for this checkout | `make -f custom/Makefile local-setup` |
| `Piper voice download failed` | Voice name typo or no network | `LOCAL_PIPER_VOICE=en_US-lessac-medium bash custom/scripts/setup_local.sh` |
| TTS returns silence / empty WAV | Voice model and text language mismatch | Use a matching voice, e.g. `en_US-lessac-medium` with English copy |
