#!/bin/bash
# Guard (#20): no script reaches GitHub without resolving a repository first. Every shipped script that mentions gh is run with
# a stub gh that records its calls, in a directory no profile matches, with no profile in force and every SG_* value cleared:
# there is no repository to resolve, so a script that still calls gh is acting on a repository it never named. The stub is the
# only transport; nothing here touches the network.
# usage: bash test-gh-resolves.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/plain" "$T/home" "$T/profiles"
printf '#!/bin/bash\necho "gh $*" >> "%s/ghlog"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
# Not run as scripts: libraries (they define functions and call nothing), the checkers of the gh interface itself, the suites, and the
# two servers, which read their repository from the profile at start and are covered by tests/test_hub_*.py.
skip=" ghcall.sh ghread.py ghthrottle.py check-gh-interface.sh check-gh-interface.py forkonly.sh address.sh repoctx.sh env.sh shell-suites.sh hub.py hubmux.py gh-direct-calls.txt "
# leaks <dir>: every script of <dir> that reached the stub gh with no repository to resolve, one per line
leaks() { local dir=$1 f b
  for f in "$dir"/*.sh "$dir"/*.py; do [ -f "$f" ] || continue; b=$(basename "$f")
    case "$b" in test-*) continue;; esac; case "$skip" in *" $b "*) continue;; esac
    /usr/bin/grep -qE '(^|[^A-Za-z_./-])gh( |_api|_get|_send)|ghcall|ghread' "$f" || continue
    : > "$T/ghlog"
    ( cd "$T/plain" && exec env $(compgen -e | /usr/bin/grep '^SG_' | sed 's/^/-u /') HOME="$T/home" PATH="$T/bin:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin" \
        SG_PROFILES_DIR="$T/profiles" SG_STATE_BASE="$T/state" SG_SQUAD=/dev/null \
        perl -e 'alarm shift; exec @ARGV' 5 $( [ "${b##*.}" = py ] && echo python3 || echo bash ) "$f" 1 2 3 ) >/dev/null 2>&1 < /dev/null
    [ -s "$T/ghlog" ] && echo "$b: $(head -1 "$T/ghlog" | cut -c1-80)"; done; }
out=$(leaks "$d"); [ -z "$out" ] && ok "no script reached gh with no repository to resolve" || ko "scripts that reached gh unresolved:
$out"
# the guard has teeth: a script that calls gh without resolving a repository is named; one that sources env.sh is not
mkdir -p "$T/s"; printf '#!/bin/bash\ngh api repos/x/y/issues/$1/comments\n' > "$T/s/leak.sh"
printf '#!/bin/bash\n. "%s/env.sh"\ngh api repos/$SG_REPO/issues/$1/comments\n' "$d" > "$T/s/safe.sh"
out=$(leaks "$T/s"); [ "${out%%:*}" = leak.sh ] && [ "$(echo "$out" | wc -l | tr -d ' ')" = 1 ] && ok "a script that calls gh unresolved is named, one that resolves first is not" || ko "the guard's verdict on a planted leak: '$out'"
rm -rf "$T"; exit $fail
