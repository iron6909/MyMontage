# MOZ-7 evidence — local deploy (LECOO-MINI)

All commands were run in the foreground, in `~/MyMontage`, on `feat/MOZ-7`.

## Done Check

| # | Check | Result | Evidence file |
|---|-------|--------|---------------|
| 1 | `bash scripts/setup_local.sh` exits 0 | **exit 0** | `01_setup_run1.log` (last line `EXIT_CODE=0`) |
| 2 | second run still exits 0 (idempotent) | **exit 0** | `02_setup_run2_idempotent.log` |
| 3 | sample render exits 0, `out/sample.mp4` created | **exit 0**, 335,684 B | `03_render.log` (`RENDER_EXIT=0`) |
| 4 | `ffprobe -show_streams -show_format -of json` shows video + audio, duration > 0 | **video h264 1280×720 (18.2 s), audio aac 22050 Hz (18.232 s), format duration 18.232018** | `04_ffprobe.json` |
| 5 | render duration + peak memory (`/usr/bin/time -v`) | **Elapsed 0:02.90, Maximum resident set size 502,636 KB (~491 MB), exit 0** | `05_render_time_v.log` |

Extra sanity checks: `frame_1s.png`, `frame_8s.png`, `frame_15s.png` are real frames pulled
from the render (CJK text renders correctly); the audio track is a real AAC stream, not silence.

## Timings

| Step | Wall clock |
|---|---|
| First full install (venv + requirements.txt + piper + 63 MB voice) | ~16 min — network-bound, the package index ran at ~200 kB/s |
| Idempotent re-run (`setup_local.sh` with everything present) | ~3.5 s per run (~7 s for runs 1+2 measured together) |
| Sample render (TTS ×3 + 3 cards + x264 encode) | 2.90 s, peak RSS 491 MB |

## What changed

| File | Change |
|---|---|
| `scripts/setup_local.sh` (new) | Idempotent local setup: 3.10–3.13 venv, requirements.txt (never requirements-gpu.txt), `piper-tts==1.2.0`, HF voice download, working-FFmpeg probe, provider posture report. |
| `scripts/render_local_sample.py` (new) | Renders `out/sample.mp4` from the built-in 3-scene sample copy: `piper_tts` tool → WAV, Pillow cards, FFmpeg concat/encode. |
| `Makefile` | `make local-setup`, `make sample`. |
| `docs/agents/LOCAL_DEPLOY.md` (new) | Deploy path, setup command, render command, local providers, troubleshooting. |
| `.gitignore` | `out/`. |
| `tools/audio/piper_tts.py` | Bug fix: only pass `--speaker` for a non-zero speaker id (see below). |

## Blockers hit and fixed

1. **Dead pip mirror.** `pip.conf` pointed at `pypi.tuna.tsinghua.edu.cn`, which fails TLS
   handshakes (`SSL: UNEXPECTED_EOF_WHILE_READING`). Reproduced in `00_setup_attempt1_dead_mirror.log`.
   Fixed in the script, not by editing the user's config: it retries once against a fallback
   index (`LOCAL_PIP_INDEX`, default `https://pypi.org/simple`). The mirror was later switched to
   Aliyun by the operator, which is why the final runs are fast.
2. **`piper.download_voices` does not exist in piper-tts 1.2.0** (`00c_...log`). Fixed by
   downloading the voice directly from `rhasspy/piper-voices` on HuggingFace, with the resolved
   path recorded and a size-checked, resumable fetch.
3. **`ffmpeg -encoders | grep -q libx264` under `set -o pipefail` reported failure** (`00d_...log`):
   grep exits on first match, ffmpeg takes SIGPIPE, rc=141. Fixed by capturing the output and
   matching with `case`.
4. **`ModuleNotFoundError: tools`** when running `scripts/render_local_sample.py` (sys.path[0]
   is `scripts/`). Fixed by inserting the repo root into `sys.path`.
5. **`Invalid input name: sid`** from ONNX Runtime for `zh_CN-huayan-medium`. Root cause: the
   `piper_tts` tool always passed `--speaker 0`; piper forwards a real `sid` tensor, which
   single-speaker voices do not have (with no `--speaker`, piper sends `None` and ONNX Runtime
   ignores it). Fixed in `tools/audio/piper_tts.py`: emit `--speaker` only when the id is non-zero.
6. **Piper downloaded the voice into the repo root** when given a bare voice name (`--model` is a
   *file path*; `--data-dir` is the espeak data dir, not the voice dir). Fixed by resolving
   `~/.piper/models/<voice>.onnx` in the render script. The stray 63 MB files were removed and are
   not committed.

## Notes

- No API key was written and no paid provider was called. `config.yaml` is untouched.
- `tts: 1/20 configured` in the setup report is the piper provider; everything else cloud-only stays 0.
- `.venv/`, `out/` and `.pi/` are not committed.
