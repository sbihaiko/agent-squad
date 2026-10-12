#!/bin/bash
# Guard (#20): the per-user cache directory is named in one place, the seam (env.sh, repoctx.sh and statedir.py). A shipped
# script or SKILL.md that spells it out is a path that skips the per-repository state directory and the shared directory.
# usage: bash test-state-literal.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# offenders <scripts dir>: every shipped file outside the seam that names the cache directory, as file:line
offenders() { local dir=$1 f
  for f in "$dir"/*.sh "$dir"/*.py "$dir"/*.json "$dir"/../SKILL.md; do [ -f "$f" ] || continue
    case "$(basename "$f")" in env.sh|repoctx.sh|statedir.py|test-*) continue;; esac
    /usr/bin/grep -n '\.cache/squad-goal' "$f" | sed "s#^#$(basename "$f"):#" | cut -c1-90; done; }
out=$(offenders "$d"); [ -z "$out" ] && ok "no shipped script or SKILL.md names the cache directory outside the seam" || ko "outside the seam:
$out"
# the guard has teeth: a script with the literal is named, and so is a SKILL.md
mkdir -p "$T/s"; printf '#!/bin/bash\nst=${SG_STATE:-$HOME/.cache/squad-goal}\n' > "$T/s/leak.sh"; printf 'write to ~/.cache/squad-goal/req\n' > "$T/SKILL.md"
out=$(offenders "$T/s"); case "$out" in *leak.sh:2:*SKILL.md:1:*) ok "a script and a SKILL.md that name the cache directory are both reported";; *) ko "the guard missed a planted literal: '$out'";; esac
printf '#!/bin/bash\n. "$(dirname "$0")/env.sh"; st=$SG_STATE\n' > "$T/s/leak.sh"; rm -f "$T/SKILL.md"
[ -z "$(offenders "$T/s")" ] && ok "a script that reads SG_STATE is not reported" || ko "a clean script was reported"
rm -rf "$T"; exit $fail
