#!/bin/bash
# usage: export-agent-squad.sh [out.zip]
# Packs the local agent-squad fork's changes for the upstream owner (Korck-lab/agent-squad; we have no write access):
# one git-format-patch per local commit on local/next since the installed 0.2.0 base, plus a zip of the full tree.
set -euo pipefail
F=${SG_SQUAD_FORK:-$HOME/VSCodeProjects/agent-squad}; BASE=$(/usr/bin/git -C "$F" rev-parse local/base-0.2.0)
out=${1:-$HOME/Desktop/agent-squad-next-$(date +%Y%m%d).zip}; tmp=$(mktemp -d)
/usr/bin/git -C "$F" format-patch -q -o "$tmp/patches" "$BASE..local/next"
/usr/bin/git -C "$F" archive --format=tar --prefix=agent-squad/ local/next | tar -x -C "$tmp"
{ echo "Local changes to agent-squad 0.2.0 (upstream sha 34acd2c), branch local/next."; echo; /usr/bin/git -C "$F" log --reverse --format='- %s' "$BASE..local/next"; echo; echo "Apply: git am patches/*.patch on top of 34acd2c."; } > "$tmp/README.txt"
(cd "$tmp" && zip -qr "$out" README.txt patches agent-squad); rm -rf "$tmp"; echo "$out"
