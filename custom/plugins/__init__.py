"""custom/plugins/__init__.py — hand-mounted plugins.

Nothing here is registered by `registry.discover("custom.tools")`. A plugin is
mounted explicitly with a CLI flag or an environment variable, per
custom/AGENTS.md §2.2, so upstream's __init__.py and registry stay untouched.

Not on the sys.path? Every entry below is meant to run from the repo root:

    python3 -m custom.plugins.<name> ...
"""
