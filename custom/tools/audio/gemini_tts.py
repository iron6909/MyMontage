"""Google Cloud Text-to-Speech — Gemini-TTS provider tool and standalone CLI.

Calls Cloud Text-to-Speech REST ``POST /v1/text:synthesize`` with Gemini-TTS
models (``gemini-2.5-flash-tts``, ``gemini-2.5-pro-tts``, etc.) supporting:
  * natural-language director ``prompt`` (style, pace, accent, emotion)
  * inline performance markup tags (``[sigh]``, ``[laughing]``, ``[uhm]``,
    ``[sarcasm]``, ``[whispering]``, ``[short pause]``, ``[long pause]``)
  * single-speaker synthesis and 2-speaker ``multiSpeakerMarkup`` dialogue

Authentication order (process env -> ``~/.harness/providers.env`` -> ADC):
  1. Service-account JSON via ``GOOGLE_APPLICATION_CREDENTIALS`` (recommended;
     must hold ``roles/aiplatform.user`` for ``aiplatform.endpoints.predict``).
  2. Application Default Credentials (``gcloud auth application-default login``).

Usage (from the repository root):
    .venv/bin/python -m custom.tools.audio.gemini_tts --check-auth
    .venv/bin/python -m custom.tools.audio.gemini_tts \\
        --text "Nice umbrella. [short pause] By the way, you took mine!" \\
        --prompt "Speak warmly with playful amusement at a clear CEFR A2 pace." \\
        --voice Callirrhoe --output out/line.mp3 --dry-run
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

from tools.base_tool import (  # pi-lens-ignore: reportMissingImports
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

from ..common.providers_env import load_local_env

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
DEFAULT_MODEL = "gemini-2.5-flash-tts"
DEFAULT_VOICE = "Orus"
DEFAULT_LANGUAGE = "en-US"
DEFAULT_REGION = "global"
DEFAULT_TIMEOUT = 120
MAX_ATTEMPTS = 3
MAX_DETAIL = 500
MAX_AUDIO_BYTES = 128 * 1024 * 1024  # 128 MiB cap

AGENT_PLATFORM_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
)
AGENT_PLATFORM_REGIONS: dict[str, str] = {
    "global": "aiplatform.googleapis.com",
    "us": "aiplatform.us.rep.googleapis.com",
    "eu": "aiplatform.eu.rep.googleapis.com",
}

SUPPORTED_MODELS: tuple[str, ...] = (
    "gemini-2.5-flash-tts",
    "gemini-2.5-pro-tts",
    "gemini-2.5-flash-lite-preview-tts",
    "gemini-3.1-flash-tts-preview",
    *AGENT_PLATFORM_MODELS,
)

SUPPORTED_REGIONS: tuple[str, ...] = (
    "global",
    "us",
    "eu",
    "northamerica-northeast1",
)

# Official 30 Gemini-TTS / Chirp 3 prebuilt voices.
SUPPORTED_VOICES: tuple[str, ...] = (
    "Achernar",
    "Achird",
    "Algenib",
    "Algieba",
    "Alnilam",
    "Aoede",
    "Autonoe",
    "Callirrhoe",
    "Charon",
    "Despina",
    "Enceladus",
    "Erinome",
    "Fenrir",
    "Gacrux",
    "Iapetus",
    "Kore",
    "Laomedeia",
    "Leda",
    "Orus",
    "Pulcherrima",
    "Puck",
    "Rasalgethi",
    "Sadachbia",
    "Sadaltager",
    "Schedar",
    "Sulafat",
    "Umbriel",
    "Vindemiatrix",
    "Zephyr",
    "Zubenelgenubi",
)
_VOICE_LOOKUP: dict[str, str] = {v.lower(): v for v in SUPPORTED_VOICES}

_ENCODING_BY_EXT: dict[str, str] = {
    ".mp3": "MP3",
    ".wav": "LINEAR16",
    ".ogg": "OGG_OPUS",
}
_EXT_BY_ENCODING: dict[str, str] = {
    "MP3": ".mp3",
    "LINEAR16": ".wav",
    "OGG_OPUS": ".ogg",
}
_SPEAKER_ALIAS_RE = re.compile(r"^[A-Za-z0-9]+$")
_ENV_KEYS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_PROJECT_ID",
    "GCLOUD_PROJECT",
    "GOOGLE_TTS_REGION",
)


def _env_value(name: str, local_env: dict[str, str] | None = None) -> str | None:
    val = os.environ.get(name)
    if val:
        return val
    resolved = load_local_env(_ENV_KEYS) if local_env is None else local_env
    return resolved.get(name) or None


def _adc_default_path() -> Path:
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return Path(home) / ".config" / "gcloud" / "application_default_credentials.json"


def inspect_auth() -> dict[str, Any]:
    """Return safe, secret-free credential diagnostics."""
    local_env = load_local_env(_ENV_KEYS)
    sa_raw = _env_value("GOOGLE_APPLICATION_CREDENTIALS", local_env)
    sa_path = Path(sa_raw).expanduser() if sa_raw else None
    sa_exists = bool(sa_path and sa_path.is_file())
    sa_project: str | None = None
    sa_type: str | None = None
    sa_error: str | None = None
    if sa_exists and sa_path is not None:
        try:
            data = json.loads(sa_path.read_text(encoding="utf-8"))
            sa_project = data.get("project_id")
            sa_type = data.get("type")
        except (OSError, json.JSONDecodeError) as exc:
            sa_error = f"unreadable credential JSON: {exc}"

    adc_path = _adc_default_path()
    adc_exists = adc_path.is_file()
    project_id = (
        _env_value("GOOGLE_CLOUD_PROJECT", local_env)
        or _env_value("GOOGLE_CLOUD_PROJECT_ID", local_env)
        or _env_value("GCLOUD_PROJECT", local_env)
        or sa_project
    )

    try:
        import google.auth  # noqa: F401

        google_auth_installed = True
    except ImportError:
        google_auth_installed = False

    if sa_exists and not sa_error:
        auth_source = "service_account_json"
    elif adc_exists:
        auth_source = "gcloud_adc"
    else:
        auth_source = "none"

    return {
        "ready": auth_source != "none" and google_auth_installed,
        "auth_source": auth_source,
        "google_auth_installed": google_auth_installed,
        "service_account_path": str(sa_path) if sa_path else None,
        "service_account_exists": sa_exists,
        "service_account_type": sa_type,
        "service_account_error": sa_error,
        "adc_file_exists": adc_exists,
        "project_id": project_id,
    }


def _mint_bearer_token() -> tuple[str, str | None, str]:
    """Mint an OAuth2 Bearer token from service account JSON or ADC.

    Returns ``(bearer_token, project_id, auth_source)``.
    """
    local_env = load_local_env(_ENV_KEYS)
    sa_raw = _env_value("GOOGLE_APPLICATION_CREDENTIALS", local_env)
    explicit_project = (
        _env_value("GOOGLE_CLOUD_PROJECT", local_env)
        or _env_value("GOOGLE_CLOUD_PROJECT_ID", local_env)
        or _env_value("GCLOUD_PROJECT", local_env)
    )

    try:
        import google.auth
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError(
            "Google Cloud TTS Gemini-TTS requires 'google-auth'. "
            "Install it with: pip install google-auth"
        ) from exc

    if sa_raw:
        sa_path = Path(sa_raw).expanduser()
        if not sa_path.is_file():
            raise RuntimeError(
                f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {sa_path}"
            )
        creds = service_account.Credentials.from_service_account_file(
            str(sa_path), scopes=[CLOUD_PLATFORM_SCOPE]
        )
        creds.refresh(Request())
        token = getattr(creds, "token", None)
        if not token:
            raise RuntimeError("Service-account credentials failed to yield an access token.")
        project_id = explicit_project or getattr(creds, "project_id", None)
        return str(token), (str(project_id) if project_id else None), "service_account_json"

    try:
        creds, adc_project = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
        creds.refresh(Request())
    except Exception as exc:
        raise RuntimeError(
            "No Google Cloud credentials configured. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a service-account JSON key (in env or ~/.harness/providers.env) with "
            "role roles/aiplatform.user, or run `gcloud auth application-default login`. "
            f"Detail: {exc}"
        ) from exc

    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("ADC credentials failed to yield an access token.")
    quota_project = getattr(creds, "quota_project_id", None)
    project_id = explicit_project or quota_project or adc_project
    return str(token), (str(project_id) if project_id else None), "gcloud_adc"


def is_agent_platform_model(model: str) -> bool:
    """True for Gemini 3.8 TTS models served via Agent Platform `:generateContent`."""
    return model.strip() in AGENT_PLATFORM_MODELS


def normalize_voice(name: str, *, allow_custom_ids: bool = False) -> str:
    """Validate and canonicalize a Gemini-TTS voice name (or 3.8 custom voice ID)."""
    cleaned = name.strip()
    if cleaned.lower() in _VOICE_LOOKUP:
        return _VOICE_LOOKUP[cleaned.lower()]
    if allow_custom_ids and cleaned.startswith(("voice_", "voicekey_")):
        return cleaned
    raise ValueError(
        f"Unsupported Gemini-TTS voice {name!r}. "
        f"Supported voices ({len(SUPPORTED_VOICES)}): {', '.join(SUPPORTED_VOICES)}"
    )


def build_endpoint(
    region: str = DEFAULT_REGION,
    *,
    model: str = DEFAULT_MODEL,
    project_id: str | None = None,
) -> str:
    """Return the REST synthesize/generateContent URL for the chosen model and region."""
    reg = (region or DEFAULT_REGION).strip().lower()
    if is_agent_platform_model(model):
        host = AGENT_PLATFORM_REGIONS.get(reg)
        if not host:
            raise ValueError(
                f"Unsupported Agent Platform region {region!r} for {model}; "
                f"choose from {', '.join(AGENT_PLATFORM_REGIONS)}"
            )
        pid = (project_id or "").strip()
        if not pid:
            raise ValueError(
                f"Model {model!r} requires a GCP project_id in the Agent Platform URL."
            )
        return (
            f"https://{host}/v1/projects/{pid}/locations/{reg}"
            f"/publishers/google/models/{model}:generateContent"
        )
    if reg not in SUPPORTED_REGIONS:
        raise ValueError(
            f"Unsupported region {region!r}; choose from {', '.join(SUPPORTED_REGIONS)}"
        )
    host = "texttospeech.googleapis.com" if reg == "global" else f"{reg}-texttospeech.googleapis.com"
    return f"https://{host}/v1/text:synthesize"


def resolve_encoding(output_path: Path | None, explicit_encoding: str | None) -> str:
    """Choose MP3 / LINEAR16 / OGG_OPUS from explicit arg or output file suffix."""
    if explicit_encoding:
        enc = explicit_encoding.strip().upper()
        if enc not in _EXT_BY_ENCODING:
            raise ValueError(
                f"Unsupported audio_encoding {explicit_encoding!r}; "
                f"choose from {', '.join(_EXT_BY_ENCODING)}"
            )
        return enc
    if output_path is not None:
        ext = output_path.suffix.lower()
        if ext in _ENCODING_BY_EXT:
            return _ENCODING_BY_EXT[ext]
        if ext:
            raise ValueError(
                f"Output path {output_path} must end with .mp3, .wav, or .ogg (got {ext})"
            )
    return "MP3"


def _build_agent_platform_payload(inputs: dict[str, Any]) -> dict[str, Any]:
    """Build a Gemini 3.8 TTS Agent Platform `:generateContent` request body."""
    if inputs.get("sample_rate_hertz") is not None:
        raise ValueError("Gemini 3.8 TTS outputs fixed 24 kHz WAV; sample_rate_hertz is not supported.")

    prompt = str(inputs.get("prompt") or "").strip()
    if prompt and len(prompt.encode("utf-8")) > 4000:
        raise ValueError("Gemini-TTS prompt must be <= 4000 UTF-8 bytes.")

    turns = inputs.get("turns")
    speakers = inputs.get("speakers")
    text = str(inputs.get("text") or "").strip()
    language_code = str(inputs.get("language_code") or "").strip()
    speech_config: dict[str, Any] = {}
    if language_code:
        # Gemini 3.8 SpeechConfig accepts ISO 639-1 (e.g. "en" from "en-US").
        speech_config["languageCode"] = language_code.split("-", 1)[0].lower()

    if turns:
        if not isinstance(turns, list) or not isinstance(speakers, dict):
            raise ValueError("Multi-speaker mode requires 'turns' (list) and 'speakers' (dict).")
        if len(speakers) != 2:
            raise ValueError(
                "Gemini-TTS multi-speaker synthesis supports exactly 2 speakers; "
                "for 3+ characters synthesize per line and concatenate."
            )
        speaker_configs = []
        valid_aliases: set[str] = set()
        for alias, voice_name in speakers.items():
            alias_s = str(alias).strip()
            if not _SPEAKER_ALIAS_RE.match(alias_s):
                raise ValueError(
                    f"Invalid speaker alias {alias!r}: must be alphanumeric with no spaces."
                )
            speaker_configs.append(
                {
                    "speaker": alias_s,
                    "voiceConfig": {"voice": normalize_voice(str(voice_name), allow_custom_ids=True)},
                }
            )
            valid_aliases.add(alias_s)

        parts = []
        total_bytes = 0
        for item in turns:
            if not isinstance(item, dict):
                raise TypeError("Each turn must be a {'speaker', 'text'} object.")
            spk = str(item.get("speaker") or "").strip()
            line = str(item.get("text") or "").strip()
            turn_style = str(item.get("style") or prompt).strip()
            if spk not in valid_aliases:
                raise ValueError(f"Turn speaker {spk!r} is not declared in speakers {sorted(valid_aliases)}.")
            if not line:
                raise ValueError("Turn text must not be empty.")
            total_bytes += len(spk.encode("utf-8")) + len(line.encode("utf-8"))
            meta: dict[str, str] = {"speaker": spk}
            if turn_style:
                meta["style"] = turn_style
            parts.append({"text": line, "speechMetadata": meta})
        if total_bytes > 4000:
            raise ValueError(f"Combined multi-speaker turns exceed 4000 bytes ({total_bytes} bytes).")

        speech_config["multiSpeakerVoiceConfig"] = {"speakerVoiceConfigs": speaker_configs}
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": speech_config,
            },
        }

    if not text:
        raise ValueError("Provide non-empty 'text' (or multi-speaker 'turns' + 'speakers').")
    if len(text.encode("utf-8")) > 5000:
        raise ValueError("Gemini-TTS text must be <= 5000 UTF-8 bytes.")

    voice_raw = str(inputs.get("voice") or inputs.get("voice_id") or DEFAULT_VOICE)
    voice_name = normalize_voice(voice_raw, allow_custom_ids=True)
    part: dict[str, Any] = {"text": text}
    if prompt:
        part["speechMetadata"] = {"style": prompt}
    speech_config["voiceConfig"] = {"voice": voice_name}
    return {
        "contents": [{"role": "user", "parts": [part]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_config,
        },
    }


def build_payload(inputs: dict[str, Any], encoding: str) -> dict[str, Any]:
    """Build and validate a Gemini-TTS REST request body (Cloud TTS or Agent Platform)."""
    model = str(inputs.get("model") or DEFAULT_MODEL).strip()
    if model not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported Gemini-TTS model {model!r}; choose from {', '.join(SUPPORTED_MODELS)}"
        )
    if is_agent_platform_model(model):
        return _build_agent_platform_payload(inputs)

    language_code = str(inputs.get("language_code") or DEFAULT_LANGUAGE).strip()
    prompt = str(inputs.get("prompt") or "").strip()
    if prompt and len(prompt.encode("utf-8")) > 4000:
        raise ValueError("Gemini-TTS prompt must be <= 4000 UTF-8 bytes.")

    turns = inputs.get("turns")
    speakers = inputs.get("speakers")
    text = str(inputs.get("text") or "").strip()

    audio_config: dict[str, Any] = {"audioEncoding": encoding}
    if inputs.get("sample_rate_hertz") is not None:
        rate = int(inputs["sample_rate_hertz"])
        if not 8000 <= rate <= 48000:
            raise ValueError("sample_rate_hertz must be between 8000 and 48000.")
        audio_config["sampleRateHertz"] = rate

    if turns:
        if not isinstance(turns, list) or not isinstance(speakers, dict):
            raise ValueError("Multi-speaker mode requires 'turns' (list) and 'speakers' (dict).")
        if len(speakers) != 2:
            raise ValueError(
                "Gemini-TTS multi-speaker synthesis supports exactly 2 speakers; "
                "for 3+ characters synthesize per line and concatenate."
            )
        speaker_configs = []
        valid_aliases: set[str] = set()
        for alias, voice_name in speakers.items():
            alias_s = str(alias).strip()
            if not _SPEAKER_ALIAS_RE.match(alias_s):
                raise ValueError(
                    f"Invalid speaker alias {alias!r}: must be alphanumeric with no spaces."
                )
            speaker_configs.append(
                {"speakerAlias": alias_s, "speakerId": normalize_voice(str(voice_name))}
            )
            valid_aliases.add(alias_s)

        normalized_turns = []
        total_bytes = 0
        for item in turns:
            if not isinstance(item, dict):
                raise TypeError("Each turn must be a {'speaker', 'text'} object.")
            spk = str(item.get("speaker") or "").strip()
            line = str(item.get("text") or "").strip()
            if spk not in valid_aliases:
                raise ValueError(f"Turn speaker {spk!r} is not declared in speakers {sorted(valid_aliases)}.")
            if not line:
                raise ValueError("Turn text must not be empty.")
            total_bytes += len(spk.encode("utf-8")) + len(line.encode("utf-8"))
            normalized_turns.append({"speaker": spk, "text": line})
        if total_bytes > 4000:
            raise ValueError(
                f"Combined multi-speaker turns exceed 4000 bytes ({total_bytes} bytes)."
            )

        synth_input: dict[str, Any] = {"multiSpeakerMarkup": {"turns": normalized_turns}}
        if prompt:
            synth_input["prompt"] = prompt
        voice_block: dict[str, Any] = {
            "languageCode": language_code,
            "modelName": model,
            "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": speaker_configs},
        }
        return {"input": synth_input, "voice": voice_block, "audioConfig": audio_config}

    if not text:
        raise ValueError("Provide non-empty 'text' (or multi-speaker 'turns' + 'speakers').")
    if len(text.encode("utf-8")) > 5000:
        raise ValueError("Gemini-TTS text must be <= 5000 UTF-8 bytes.")

    voice_raw = str(inputs.get("voice") or inputs.get("voice_id") or DEFAULT_VOICE)
    voice_name = normalize_voice(voice_raw)
    synth_input = {"text": text}
    if prompt:
        synth_input["prompt"] = prompt
    voice_block = {
        "languageCode": language_code,
        "name": voice_name,
        "modelName": model,
    }
    return {"input": synth_input, "voice": voice_block, "audioConfig": audio_config}


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = DEFAULT_TIMEOUT,
    max_attempts: int = MAX_ATTEMPTS,
) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ValueError(f"Refusing non-HTTPS endpoint: {url!r}")
    import requests

    retryable = {408, 425, 429, 500, 502, 503, 504}
    last_error = ""
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"Network error calling {url}: {exc}"
            if attempt < max_attempts:
                time.sleep(min(8, 2 ** (attempt - 1)))
                continue
            raise RuntimeError(last_error) from exc

        if 200 <= resp.status_code < 300:
            if len(resp.content) > MAX_AUDIO_BYTES:
                raise RuntimeError(
                    f"Response exceeded {MAX_AUDIO_BYTES} byte safety cap."
                )
            return resp.json()

        detail = (resp.text or "")[:MAX_DETAIL]
        hint = ""
        if resp.status_code == 403 and "aiplatform.endpoints.predict" in detail:
            hint = " (Grant role roles/aiplatform.user on the GCP project.)"
        elif resp.status_code == 403 and "SERVICE_DISABLED" in detail:
            svc = "aiplatform.googleapis.com" if "aiplatform" in url else "texttospeech.googleapis.com"
            hint = f" (Enable {svc} on the GCP project.)"
        last_error = f"HTTP {resp.status_code} from {url}: {detail}{hint}"
        if resp.status_code in retryable and attempt < max_attempts:
            time.sleep(min(8, 2 ** (attempt - 1)))
            continue
        raise RuntimeError(last_error)
    raise RuntimeError(last_error or "Gemini-TTS request failed")


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _find_ffmpeg() -> str | None:
    for candidate in (os.environ.get("FFMPEG"), "/usr/bin/ffmpeg", shutil.which("ffmpeg")):
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, "-version"], capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def _transcode_wav(wav_bytes: bytes, target_encoding: str) -> bytes:
    """Convert 24 kHz WAV returned by Agent Platform Gemini 3.8 into MP3 or OGG_OPUS."""
    if target_encoding == "LINEAR16":
        return wav_bytes
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            f"Gemini 3.8 returns WAV; encoding to {target_encoding} requires a working ffmpeg binary "
            "(or write directly to a .wav output path)."
        )
    if target_encoding == "MP3":
        fmt_args = ["-c:a", "libmp3lame", "-q:a", "2", "-f", "mp3"]
    elif target_encoding == "OGG_OPUS":
        fmt_args = ["-c:a", "libopus", "-b:a", "96k", "-f", "ogg"]
    else:
        raise ValueError(f"Unsupported transcode target: {target_encoding!r}")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", *fmt_args, "pipe:1"],
        input=wav_bytes,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:MAX_DETAIL]
        raise RuntimeError(f"ffmpeg failed converting Gemini 3.8 WAV to {target_encoding}: {stderr}")
    return proc.stdout


def _decode_audio_response(response_obj: dict[str, Any], *, model: str = DEFAULT_MODEL) -> bytes:
    if not isinstance(response_obj, dict):
        raise TypeError("Gemini-TTS response must be a JSON object.")
    if is_agent_platform_model(model):
        b64_data = None
        candidates = response_obj.get("candidates")
        if isinstance(candidates, list) and candidates:
            parts = ((candidates[0] or {}).get("content") or {}).get("parts") or []
            for part in parts:
                inline = (part or {}).get("inlineData") or (part or {}).get("inline_data")
                if isinstance(inline, dict) and inline.get("data"):
                    b64_data = str(inline["data"])
                    break
        if not b64_data:
            raise ValueError("Agent Platform response did not include inlineData.data audio.")
    else:
        raw = response_obj.get("audioContent")
        if not raw:
            raise ValueError("Cloud TTS response did not include non-empty 'audioContent'.")
        b64_data = str(raw)
    try:
        audio = base64.b64decode(b64_data, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid base64 audio payload in response: {exc}") from exc
    if not audio:
        raise ValueError("Decoded audio payload is empty.")
    return audio


class GoogleGeminiTTS(BaseTool):
    """Google Cloud Text-to-Speech Gemini-TTS (director-prompted expressive speech)."""

    name = "google_gemini_tts"
    version = "0.1.0"
    tier = ToolTier.VOICE
    capability = "tts"
    provider = "google_gemini_tts"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies: ClassVar[list[str]] = ["python:google.auth"]
    install_instructions = (
        "1. Enable Cloud Text-to-Speech API (texttospeech.googleapis.com) and "
        "Gemini Enterprise Agent Platform API (aiplatform.googleapis.com) on your GCP project.\n"
        "2. Create a Service Account with role 'Agent Platform User' (roles/aiplatform.user).\n"
        "3. Download its JSON key to ~/.harness/gcp-tts-sa.json (chmod 600).\n"
        "4. Add GOOGLE_APPLICATION_CREDENTIALS=/home/<user>/.harness/gcp-tts-sa.json "
        "to ~/.harness/providers.env (or export it in your shell)."
    )
    fallback = "google_tts"
    fallback_tools: ClassVar[list[str]] = ["google_tts", "mymontage_piper_tts"]
    agent_skills: ClassVar[list[str]] = ["text-to-speech"]

    capabilities: ClassVar[list[str]] = [
        "text_to_speech",
        "voice_selection",
        "style_prompt",
        "multi_speaker",
        "multilingual",
    ]
    supports: ClassVar[dict[str, bool]] = {
        "voice_cloning": False,
        "multilingual": True,
        "offline": False,
        "native_audio": True,
        "style_prompt": True,
        "multi_speaker": True,
    }
    best_for: ClassVar[list[str]] = [
        "expressive short-drama character acting and narration via natural-language prompts",
        "comedic timing and reactions with inline tags ([sigh], [laughing], [sarcasm], [short pause])",
        "30 distinct English character voices across US/UK/AU locales",
    ]
    not_good_for: ClassVar[list[str]] = [
        "native word-level timestamps (pair with npx hyperframes transcribe --model small.en)",
        "fully offline synthesis",
    ]

    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Spoken text (supports Gemini-TTS markup tags)"},
            "prompt": {"type": "string", "description": "Natural-language style/pace/emotion director instruction"},
            "voice": {"type": "string", "default": DEFAULT_VOICE},
            "voice_id": {"type": "string", "description": "Alias for voice (selector compatibility)"},
            "model": {"type": "string", "enum": list(SUPPORTED_MODELS), "default": DEFAULT_MODEL},
            "language_code": {"type": "string", "default": DEFAULT_LANGUAGE},
            "region": {"type": "string", "enum": list(SUPPORTED_REGIONS), "default": DEFAULT_REGION},
            "audio_encoding": {"type": "string", "enum": list(_EXT_BY_ENCODING)},
            "sample_rate_hertz": {"type": "integer", "minimum": 8000, "maximum": 48000},
            "turns": {"type": "array", "description": "Optional 2-speaker turns [{speaker, text}]"},
            "speakers": {"type": "object", "description": "Optional 2-speaker alias -> voice map"},
            "output_path": {"type": "string"},
            "dry_run": {"type": "boolean", "default": False},
            "mock_response_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields: ClassVar[list[str]] = [
        "text",
        "prompt",
        "voice",
        "model",
        "language_code",
        "turns",
        "speakers",
    ]
    side_effects: ClassVar[list[str]] = [
        "writes audio file to output_path",
        "calls Google Cloud TTS Gemini-TTS API",
    ]
    user_visible_verification: ClassVar[list[str]] = [
        "Listen to generated audio and check acting tone and pacing"
    ]

    def get_status(self) -> ToolStatus:
        info = inspect_auth()
        return ToolStatus.AVAILABLE if info["ready"] else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Rough audio-token estimate (25 tok/s): 2.5 Flash $10/1M, 2.5 Pro $20/1M,
        # 3.8 Flash $9/1M (promo through 2026-12-31), 3.8 Flash-Lite $6/1M.
        text = str(inputs.get("text") or "")
        if inputs.get("turns"):
            text = " ".join(str(t.get("text", "")) for t in inputs["turns"] if isinstance(t, dict))
        words = max(1, len(text.split()))
        est_seconds = words / 2.2
        audio_tokens = est_seconds * 25.0
        model = str(inputs.get("model") or DEFAULT_MODEL)
        if model == "gemini-3.8-flash-lite-tts":
            rate = 6.0
        elif model == "gemini-3.8-flash-tts":
            rate = 9.0
        elif "pro" in model:
            rate = 20.0
        else:
            rate = 10.0
        return round((audio_tokens / 1_000_000.0) * rate, 5)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        return self.synthesize(inputs)

    def synthesize(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        local_env = load_local_env(_ENV_KEYS)
        model = str(inputs.get("model") or DEFAULT_MODEL).strip()
        region = str(inputs.get("region") or _env_value("GOOGLE_TTS_REGION", local_env) or DEFAULT_REGION)
        out_raw = inputs.get("output_path")
        out_path = Path(out_raw).expanduser() if out_raw else None

        try:
            encoding = resolve_encoding(out_path, inputs.get("audio_encoding"))
            if out_path is None:
                default_ext = ".wav" if is_agent_platform_model(model) else _EXT_BY_ENCODING[encoding]
                out_path = Path(f"gemini_tts_output{default_ext}")
                encoding = resolve_encoding(out_path, inputs.get("audio_encoding"))
            payload = build_payload(inputs, encoding)
        except (ValueError, TypeError) as exc:
            return ToolResult(success=False, error=str(exc))

        if inputs.get("dry_run"):
            auth_info = inspect_auth()
            try:
                endpoint = build_endpoint(
                    region,
                    model=model,
                    project_id=auth_info["project_id"] or "<PROJECT_ID>",
                )
            except ValueError as exc:
                return ToolResult(success=False, error=str(exc))
            return ToolResult(
                success=True,
                data={
                    "mode": "dry-run",
                    "api": "agent_platform" if is_agent_platform_model(model) else "cloud_tts",
                    "endpoint": endpoint,
                    "auth_ready": auth_info["ready"],
                    "auth_source": auth_info["auth_source"],
                    "project_id": auth_info["project_id"],
                    "payload": payload,
                    "output": str(out_path),
                },
                model=f"google-gemini-tts/{model}",
                duration_seconds=round(time.time() - start, 3),
            )

        mock_path_raw = inputs.get("mock_response_path")
        if mock_path_raw:
            try:
                resp_obj = json.loads(Path(mock_path_raw).read_text(encoding="utf-8"))
                audio_bytes = _decode_audio_response(resp_obj, model=model)
                if is_agent_platform_model(model):
                    audio_bytes = _transcode_wav(audio_bytes, encoding)
                _atomic_write_bytes(out_path, audio_bytes)
            except (OSError, json.JSONDecodeError, ValueError, TypeError, RuntimeError) as exc:
                return ToolResult(success=False, error=f"Mock replay failed: {exc}")
            return ToolResult(
                success=True,
                data={
                    "mode": "mock",
                    "provider": self.provider,
                    "api": "agent_platform" if is_agent_platform_model(model) else "cloud_tts",
                    "model": model,
                    "encoding": encoding,
                    "bytes": len(audio_bytes),
                    "sha256": hashlib.sha256(audio_bytes).hexdigest()[:16],
                    "output": str(out_path),
                },
                artifacts=[str(out_path)],
                model=f"google-gemini-tts/{model}",
                duration_seconds=round(time.time() - start, 3),
            )

        token: str | None = None
        try:
            token, project_id, auth_source = _mint_bearer_token()
            endpoint = build_endpoint(region, model=model, project_id=project_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            }
            if project_id and not is_agent_platform_model(model):
                headers["x-goog-user-project"] = project_id
            resp_obj = _http_post_json(
                endpoint,
                payload,
                headers=headers,
                timeout=int(inputs.get("timeout", DEFAULT_TIMEOUT)),
                max_attempts=int(inputs.get("max_attempts", MAX_ATTEMPTS)),
            )
            audio_bytes = _decode_audio_response(resp_obj, model=model)
            if is_agent_platform_model(model):
                audio_bytes = _transcode_wav(audio_bytes, encoding)
            _atomic_write_bytes(out_path, audio_bytes)
        except (RuntimeError, ValueError, TypeError, OSError) as exc:
            return ToolResult(
                success=False,
                error=f"Google Gemini-TTS failed: {_redact(str(exc), token)}",
                duration_seconds=round(time.time() - start, 3),
            )

        voice_out = (
            payload["generationConfig"]["speechConfig"].get("voiceConfig", {}).get("voice")
            if is_agent_platform_model(model)
            else payload["voice"].get("name")
        )
        return ToolResult(
            success=True,
            data={
                "mode": "live",
                "provider": self.provider,
                "api": "agent_platform" if is_agent_platform_model(model) else "cloud_tts",
                "auth_source": auth_source,
                "project_id": project_id,
                "endpoint": endpoint,
                "model": model,
                "voice": voice_out,
                "encoding": encoding,
                "bytes": len(audio_bytes),
                "sha256": hashlib.sha256(audio_bytes).hexdigest()[:16],
                "output": str(out_path),
            },
            artifacts=[str(out_path)],
            model=f"google-gemini-tts/{model}",
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=round(time.time() - start, 3),
        )


def _parse_speaker_pairs(raw_pairs: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in raw_pairs or []:
        if "=" not in pair:
            raise ValueError(f"Expected --speaker Alias=Voice, got {pair!r}")
        alias, voice = pair.split("=", 1)
        mapping[alias.strip()] = voice.strip()
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Google Cloud Text-to-Speech — Gemini-TTS CLI (single & 2-speaker)."
    )
    parser.add_argument("--check-auth", action="store_true", help="Print safe credential diagnostics and exit")
    parser.add_argument("--list-voices", action="store_true", help="Print all 30 supported voice names and exit")
    parser.add_argument("--text", help="Spoken line text (supports [sigh], [laughing], [short pause], etc.)")
    parser.add_argument("--prompt", default="", help="Director style/pace/emotion prompt")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help=f"Voice name (default: {DEFAULT_VOICE})")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=SUPPORTED_MODELS, help="Gemini-TTS model")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="BCP-47 language code (default: en-US)")
    parser.add_argument("--region", default=None, choices=SUPPORTED_REGIONS, help="Cloud TTS endpoint region")
    parser.add_argument("--turns-json", type=Path, help="JSON file containing [{'speaker','text'}, ...] for 2-speaker mode")
    parser.add_argument("--speaker", action="append", help="Repeatable Alias=Voice mapping for 2-speaker mode")
    parser.add_argument("--encoding", choices=sorted(_EXT_BY_ENCODING), help="Override audio encoding")
    parser.add_argument("--sample-rate", type=int, help="Optional sampleRateHertz (8000..48000)")
    parser.add_argument("--output", type=Path, help="Output audio path (.mp3, .wav, or .ogg)")
    parser.add_argument("--dry-run", action="store_true", help="Validate & print request payload without calling GCP")
    parser.add_argument("--mock-response", type=Path, help="Replay captured JSON response file")
    args = parser.parse_args(argv)

    if args.list_voices:
        print(json.dumps({"models": list(SUPPORTED_MODELS), "voices": list(SUPPORTED_VOICES)}, indent=2))
        return 0

    if args.check_auth:
        info = inspect_auth()
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0 if info["ready"] else 2

    inputs: dict[str, Any] = {
        "text": args.text or "",
        "prompt": args.prompt,
        "voice": args.voice,
        "model": args.model,
        "language_code": args.language,
        "dry_run": args.dry_run,
    }
    if args.region:
        inputs["region"] = args.region
    if args.encoding:
        inputs["audio_encoding"] = args.encoding
    if args.sample_rate is not None:
        inputs["sample_rate_hertz"] = args.sample_rate
    if args.output is not None:
        inputs["output_path"] = str(args.output)
    if args.mock_response is not None:
        inputs["mock_response_path"] = str(args.mock_response)
    if args.turns_json is not None:
        try:
            inputs["turns"] = json.loads(args.turns_json.read_text(encoding="utf-8"))
            inputs["speakers"] = _parse_speaker_pairs(args.speaker)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    result = GoogleGeminiTTS().synthesize(inputs)
    if result.success:
        print(json.dumps(result.data, indent=2, ensure_ascii=False))
        return 0
    print(f"ERROR: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
