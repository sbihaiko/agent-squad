#!/bin/bash
# usage: models-apply.sh [<repo models.json>] [<live models.json>]   The explicit apply (#1261, ADR-0140 clause 7).
# Copies the repository chains into the live models.json, atomically, with a backup in <live skill>/backups, and keeps
# every other key of the live file. Nothing else writes the live file; a hand-run publish only reports the difference.
here="$(cd "$(dirname "$0")" && pwd)"
repo=${1:-$here/models.json}
live=${2:-${SG_LIVE_DIR:-$HOME/.claude/skills/squad-goal}/scripts/models.json}
b=$(python3 "$here/models_chains.py" apply "$repo" "$live") || exit $?
echo "models-apply: chains of $repo applied to $live; backup $b"
