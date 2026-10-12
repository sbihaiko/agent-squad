#!/bin/bash
# usage: vendor-guard.sh   Re-vendors the personal skills into the fork when they drifted from vendor/skills/VERSIONS.json: a drifted vendor makes every `squad start` refuse ("vendor/skills is not clean ... Nothing ran") and the run never begins.
. "$(dirname "$0")/env.sh"; f=${SG_SQUAD:-$HOME/VSCodeProjects/agent-squad}
[ -d "$f/.git" ] || exit 0; [ -e "$f/.git/MERGE_HEAD" ] || [ -e "$f/.git/index.lock" ] || [ -e "$f/.git/rebase-merge" ] && exit 0
[ "$(/usr/bin/git -C "$f" branch --show-current)" = local/next ] || exit 0
[ -z "$(/usr/bin/git -C "$f" status --porcelain -- vendor/skills)" ] || exit 0   # someone is mid-edit there
"$f/.venv/bin/python3" "$f/scripts/build_vendor_skills.py" >/dev/null 2>&1 || exit 0
[ -n "$(/usr/bin/git -C "$f" status --porcelain -- vendor/skills)" ] || exit 0
/usr/bin/git -C "$f" add vendor/skills && /usr/bin/git -C "$f" commit -q -m "vendor skills: re-vendor drifted personal skills (guard)" -- vendor/skills && echo "VENDOR-GUARD re-vendored: $(/usr/bin/git -C "$f" log --oneline -1)"
