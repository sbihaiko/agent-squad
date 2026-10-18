#!/bin/bash
# usage: vendor-guard.sh   Re-vendors the personal skills into the fork when they drifted from vendor/skills/VERSIONS.json: a drifted vendor makes every `squad start` refuse ("vendor/skills is not clean ... Nothing ran") and the run never begins.
# The watch cycle runs this pass every cycle, so it keeps a key of its input in $SG_STATE and returns at once when the key is unchanged (#568): the build is the costly step -- it reads every personal skill, copies the whole vendor tree and writes VERSIONS.json -- and an unchanged key does no work and makes no write.
# The key is one digest of the config, the personal skills' content, the vendor tree and vendor/skills/VERSIONS.json (build_vendor_skills.py --key). A changed key runs the build as before, then keeps the new key.
. "$(dirname "$0")/env.sh"; f=${SG_SQUAD:-$HOME/VSCodeProjects/agent-squad}
[ -d "$f/.git" ] || exit 0; [ -e "$f/.git/MERGE_HEAD" ] || [ -e "$f/.git/index.lock" ] || [ -e "$f/.git/rebase-merge" ] && exit 0
[ "$(/usr/bin/git -C "$f" branch --show-current)" = local/next ] || exit 0
[ -z "$(/usr/bin/git -C "$f" status --porcelain -- vendor/skills)" ] || exit 0   # someone is mid-edit there
py=$f/.venv/bin/python3; build=$f/scripts/build_vendor_skills.py; kf=$SG_STATE/vendor-guard.key
key=$("$py" "$build" --key 2>/dev/null) || exit 0
[ -n "$key" ] || exit 0
[ "$(cat "$kf" 2>/dev/null)" = "$key" ] && exit 0                  # nothing the build reads changed: no build, no write
"$py" "$build" >/dev/null 2>&1 || exit 0
if [ -n "${SG_STATE:-}" ]; then mkdir -p "$SG_STATE"   # no state directory: the pass still builds, it just cannot skip
  new=$("$py" "$build" --key 2>/dev/null) && [ -n "$new" ] && { printf '%s\n' "$new" > "$kf.$$" && mv "$kf.$$" "$kf"; }   # the key the next cycle reads
fi
[ -n "$(/usr/bin/git -C "$f" status --porcelain -- vendor/skills)" ] || exit 0
/usr/bin/git -C "$f" add vendor/skills && /usr/bin/git -C "$f" commit -q -m "vendor skills: re-vendor drifted personal skills (guard)" -- vendor/skills && echo "VENDOR-GUARD re-vendored: $(/usr/bin/git -C "$f" log --oneline -1)"
