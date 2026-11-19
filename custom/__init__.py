"""custom/ — MyMontage's zero-intrusion namespace (rules: custom/AGENTS.md).

The explicit package marker is what lets static tooling resolve `custom.*`
imports. The directory still has to be reached from the repository root, which
is what `python3 -m custom.*` and `custom/scripts/flow_cli.sh` already do.
"""
