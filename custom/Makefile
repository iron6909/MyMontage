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
.PHONY: help local-setup sample

help:
	@echo "custom/ - local-only deploy (no GPU, no API keys)"
	@echo "  make -f custom/Makefile local-setup   # .venv + requirements + Piper voice + patch"
	@echo "  make -f custom/Makefile sample        # render out/sample.mp4 with local providers"

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
