# Local-only (CPU, key-free) deploy helpers. Run from the REPOSITORY ROOT:
#   make -f custom/Makefile local-setup
#   make -f custom/Makefile sample
# Docs: custom/docs/LOCAL_DEPLOY.md
CUSTOM_DIR := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
REPO_ROOT  := $(patsubst %/,%,$(dir $(CUSTOM_DIR)))
SCRIPTS    := $(CUSTOM_DIR)/scripts
VENV_DIR   ?= $(REPO_ROOT)/.venv
PYTHON     ?= $(VENV_DIR)/bin/python

# Both scripts resolve tools.*, requirements.txt and out/ against the repo root,
# so a call from anywhere else must fail loudly instead of writing custom/out/.
ROOT_GUARD := @[ "$$(pwd -P)" = "$(REPO_ROOT)" ] || { echo "ERROR: run this from the repository root: cd $(REPO_ROOT) && make -f custom/Makefile <target>"; exit 1; }

.DEFAULT_GOAL := help
.PHONY: help local-setup sample \
        sw-install sw-auth-flow sw-auth-music sw-hide sw-doctor sw-status sw-tools \
        flow flow-help sw-credentials

help:
	@echo "custom/ - local-only deploy (no GPU, no API keys)"
	@echo "  make -f custom/Makefile local-setup     # .venv + requirements + Piper voice + patch"
	@echo "  make -f custom/Makefile sample           # render out/sample.mp4 with local providers"
	@echo ""
	@echo "StudioWright / Google Flow tools:"
	@echo "  make -f custom/Makefile sw-install       # install StudioWright CLI (requires uv)"
	@echo "  make -f custom/Makefile sw-auth-flow     # sign in to Google Flow (images + video)"
	@echo "  make -f custom/Makefile sw-auth-music    # sign in to Google Flow Music"
	@echo "  make -f custom/Makefile sw-hide          # move Chrome off-screen (do not minimize)"
	@echo "  make -f custom/Makefile sw-doctor        # check Flow page hooks (spends nothing)"
	@echo "  make -f custom/Makefile sw-status        # show pacing + recent jobs"
	@echo "  make -f custom/Makefile sw-tools         # verify flow_image/video/music are registered"
	@echo "  make -f custom/Makefile sw-credentials   # credential presence only (bool + path)"
	@echo ""
	@echo "Google Flow tools (run from the repo root; docs: custom/docs/FLOW.md):"
	@echo "  make -f custom/Makefile flow-help        # --help for image, video and music"
	@echo "  make -f custom/Makefile flow ARGS=\"image --prompt 'a red fox' --dry-run\""
	@echo "  make -f custom/Makefile flow ARGS=\"video --prompt 'drone shot' --plan\""

# .venv + requirements.txt + Piper voice + working FFmpeg + local Piper patch.
# Idempotent: every step checks before acting.
local-setup:
	$(ROOT_GUARD)
	bash "$(SCRIPTS)/setup_local.sh"

# Render out/sample.mp4 from the built-in sample copy (local TTS + FFmpeg).
# Extra flags: make -f custom/Makefile sample ARGS="--voice en_US-lessac-medium"
sample:
	$(ROOT_GUARD)
	@test -x "$(PYTHON)" || { \
		echo "ERROR: $(PYTHON) is missing. Run: make -f custom/Makefile local-setup"; \
		exit 1; \
	}
	"$(PYTHON)" "$(SCRIPTS)/render_local_sample.py" $(ARGS)

# ── StudioWright / Google Flow ──────────────────────────────────────────────

# Install the StudioWright CLI via uv (idempotent).
sw-install:
	$(ROOT_GUARD)
	@command -v uv >/dev/null 2>&1 || { echo "ERROR: uv is not on PATH. Install from https://docs.astral.sh/uv/"; exit 1; }
	uv tool install git+https://github.com/umutkocak/studiowright
	@echo "StudioWright installed. Run: make -f custom/Makefile sw-auth-flow"

# Authenticate Chrome for Google Flow (image + video).
sw-auth-flow:
	$(ROOT_GUARD)
	@command -v sw >/dev/null 2>&1 || { echo "ERROR: sw not found. Run: make -f custom/Makefile sw-install"; exit 1; }
	sw auth login

# Authenticate Chrome for Google Flow Music.
sw-auth-music:
	$(ROOT_GUARD)
	@command -v sw >/dev/null 2>&1 || { echo "ERROR: sw not found. Run: make -f custom/Makefile sw-install"; exit 1; }
	sw auth login --url https://www.flowmusic.app/

# Move Chrome window off-screen (safe alternative to minimizing).
sw-hide:
	$(ROOT_GUARD)
	sw window hide

# Run sw doctor to verify Flow page hooks (reads pages only, spends nothing).
sw-doctor:
	$(ROOT_GUARD)
	sw doctor

# Show current pacing state and recent job history.
sw-status:
	$(ROOT_GUARD)
	sw pace && sw jobs

# Verify that custom Flow tools appear in the OpenMontage registry.
sw-tools:
	$(ROOT_GUARD)
	$(PYTHON) -c "\
import sys; sys.path.insert(0, '.'); \
import custom.tools.loader as loader; \
names = loader.load(); \
print('Custom tools registered:', names); \
assert 'flow_image' in names and 'flow_video' in names and 'flow_music' in names, \
    'Missing tools — check custom/tools/'; \
print('OK')"

# Credential presence only — booleans and paths, never cookie/token values.
sw-credentials:
	$(ROOT_GUARD)
	@python3 "$(SCRIPTS)/sw_credentials.py"

# ── Google Flow tools (custom/plugins/studiowright) ────────────────────────
# One launcher for all three tools; it resolves the interpreter and the repo
# root itself. `--plan` prints the sw argv and runs nothing; `--dry-run`
# submits nothing. Neither spends Flow credits.
flow:
	$(ROOT_GUARD)
	@bash "$(SCRIPTS)/flow_cli.sh" $(ARGS)

flow-help:
	$(ROOT_GUARD)
	@bash "$(SCRIPTS)/flow_cli.sh" --help
	@bash "$(SCRIPTS)/flow_cli.sh" image --help
	@bash "$(SCRIPTS)/flow_cli.sh" video --help
	@bash "$(SCRIPTS)/flow_cli.sh" music --help
