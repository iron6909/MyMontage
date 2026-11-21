"""custom/plugins/studiowright — StudioWright (`sw`) CLI route for Google Flow.

`sw` (github.com/iron6909/studiowright, installed locally with uv) drives the
signed-in Chrome to reach Google Flow: images, video and Flow Music. There is no
API key and no HTTP endpoint — the plugin shells out to `sw` and reads the one
JSON object it prints (`sw ... --json`).

The installed fork revision is pinned in custom/Makefile.

Modules
-------
client.py  subprocess helpers: JSON parsing, output files, download recovery
cli.py     repo-root entry: `python3 -m custom.plugins.studiowright <image|video|music>`

Mounted by custom/Makefile (`make -f custom/Makefile flow ARGS="image --help"`).
"""
