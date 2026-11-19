"""custom/tools/audio/__init__.py — local TTS and Google Flow Music tools.

`registry.discover("custom.tools")` walks this package, so everything here is
imported at startup. `piper_tts.py` is the *patched reference copy* that
`custom/scripts/setup_local.sh` mounts onto tools/audio/piper_tts.py — it must
not register a second `piper_tts` provider, so `custom/tools/loader.py` skips it.
"""
