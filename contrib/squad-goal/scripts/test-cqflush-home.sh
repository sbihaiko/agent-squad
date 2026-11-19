#!/bin/bash
# seconds: 1.15
# A queued loop comment never names the home directory: cqflush.sh writes $HOME as ~ before it posts (#1791).
# Offline: fixture profile, stub gh that keeps the comments of one issue and logs every write.
# usage: bash test-cqflush-home.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain" "$T/c" "$B/alpha/cq"
printf ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' "$T/co" > "$P/alpha.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
for a; do case "$a" in body=@*) bf=${a#body=@};; esac; done
all="$*"
case "$all" in
  *per_page*) for f in "$C"/*; do [ -e "$f" ] && jq -Rs --arg id "$(basename "$f")" '{id: ($id|tonumber), body: .}' < "$f"; done | jq -s . ; exit 0;;
  *body=@*) id=$(( $(ls "$C" | wc -l) + 1 )); cp "$bf" "$C/$id"; echo "POST $id" >> "$GHLOG"; echo "$id";;
esac
GH
chmod +x "$T/bin/gh"
export HOME=$T/home; mkdir -p "$T/home"; export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog C=$T/c PATH="$T/bin:$PATH"; : > "$T/ghlog"
unset SG_TOOLING_REPO SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT
put() { printf '%s\n' "$2" > "$B/alpha/cq/$1"; touch -t 202601010000 "$B/alpha/cq/$1"; }
flush() { (cd "$T/plain" && bash "$d/cqflush.sh" --once 2>&1); }

# 1. a body that names $HOME/x posts ~/x
put 'x__alpha#7-1791a.md' "run $HOME/x/.venv/bin/python now"
out=$(flush)
if [ "$(cat "$T/c/1" 2>/dev/null)" = "run ~/x/.venv/bin/python now" ]; then ok "a body naming \$HOME/x posts ~/x"
else ko "home path: posted [$(cat "$T/c/1" 2>/dev/null)] / $out"; fi

# 2. a body with no home path is unchanged
put 'x__alpha#7-1791b.md' "plain text with /opt/x and no home"
out=$(flush)
if [ "$(cat "$T/c/2" 2>/dev/null)" = "plain text with /opt/x and no home" ]; then ok "a body with no home path is unchanged"
else ko "no home path: posted [$(cat "$T/c/2" 2>/dev/null)] / $out"; fi

rm -rf "$T"
[ "$fail" = 0 ]
