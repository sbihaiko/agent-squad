#!/bin/bash
# Regression test for #6 (ADR-0277): a read repeated inside its window is answered from a snapshot and spends no request,
# a changed key or a corrupt entry goes live, and nothing that gates a write is ever answered from the snapshot. It runs
# the real readers as processes (ghthrottle.py, ghread.py, and ghcall.sh's doors) against a stub `gh` that records every
# request it receives, which is how a shell caller reaches them. Nothing here touches the network, a model or a board.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
fails=0
ok() { echo "  ok: $1"; }
bad() { echo "  FAILED: $1"; fails=$((fails + 1)); }

mkdir -p "$T/bin" "$T/st"
for v in $(compgen -e | grep '^SG_'); do unset "$v"; done
export SG_STATE="$T/st" HOME="$T/home" PATH="$T/bin:$PATH"; mkdir -p "$HOME"
cp "$here/ghread.py" "$here/ghthrottle.py" "$here/ghcall.sh" "$here/drainpolicy.py" "$T/"
CLI="python3 $T/ghthrottle.py"

# The stub `gh`: one line per request in gh.log. A conditional request (`-H 'If-None-Match: <etag>'`) is answered 304 when
# the etag is the one in $T/etag, and a live one carries the etag and the body in $T/etag and $T/body. `STUB_FAIL=1` makes
# it refuse with GitHub's own error shape.
cat > "$T/bin/gh" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DIR/gh.log"
if [ -n "${STUB_FAIL:-}" ]; then printf '{"message":"Not Found","status":"404"}'; exit 1; fi
for a in "$@"; do case "$a" in "If-None-Match: "*) sent=${a#If-None-Match: };; esac; done
etag=$(cat "$STUB_DIR/etag" 2>/dev/null)
if [ -n "${sent:-}" ] && [ "$sent" = "\"$etag\"" ]; then printf 'HTTP/2.0 304 Not Modified\r\n\r\n'; exit 1; fi
printf 'HTTP/2.0 200 OK\r\netag: "%s"\r\n\r\n%s' "$etag" "$(cat "$STUB_DIR/body")"
STUB
chmod +x "$T/bin/gh"; export STUB_DIR="$T"
setfact() { printf '%s' "$1" > "$T/body"; printf '%s' "${2:-e1}" > "$T/etag"; }
requests() { wc -l < "$T/gh.log" 2>/dev/null | tr -d ' ' || echo 0; }
reset() { rm -rf "$T/st/gh-cache" "$T/gh.log" "$T/st/gh-budget.json"; touch "$T/gh.log"; unset STUB_FAIL SG_GH_CACHE_WINDOW; }
entry() { PYTHONPATH="$T" python3 -c "import sys, ghthrottle; print(ghthrottle.Cache(state_dir='$T/st').entry_path(sys.argv[1]))" "$1"; }
backdate() { local f; f=$(entry "$1"); jq '.at = 1' "$f" > "$f.new" && mv "$f.new" "$f"; }   # the window has long passed
poison() {   # poison <route> <json>: a fresh, well-formed, wrong entry -- written the way the cache writes its own
  PYTHONPATH="$T" python3 -c "
import sys, ghthrottle, ghread
ghthrottle.Cache(state_dir='$T/st', window=3600).store(sys.argv[1], ghread.Read(kind=ghread.ANSWER, body=sys.argv[2]))" "$1" "$2"; }

CHEAP=repos/o/r/labels; KEYED=repos/o/r/pulls/7/files

echo "a repeat inside the window:"
reset; setfact '[{"name":"bug"}]'
a=$($CLI $CHEAP); rc1=$?; b=$($CLI $CHEAP); rc2=$?
[ "$rc1$rc2" = "00" ] && [ "$a" = '[{"name":"bug"}]' ] && [ "$a" = "$b" ] && ok "the repeat returns the same answer" || bad "the repeat: $a / $b / $rc1$rc2"
[ "$(requests)" = 1 ] && ok "and spends no request" || bad "requests: $(requests)"
jq -e '.tokens == 299' "$T/st/gh-budget.json" >/dev/null && ok "and one token left the shared bucket, not two" || bad "bucket: $(cat "$T/st/gh-budget.json")"
c=$(. "$T/ghcall.sh"; gh_snapshot $CHEAP -q '.[0].name'); [ "$c" = bug ] && [ "$(requests)" = 1 ] && ok "gh_snapshot answers from it too, with the caller's projection" || bad "gh_snapshot: $c / $(requests)"

echo "the window:"
reset; setfact '["old"]'; $CLI $CHEAP >/dev/null; backdate $CHEAP; setfact '["new"]' e2   # GitHub moves the etag when the fact moves (#45)
a=$($CLI $CHEAP); [ "$a" = '["new"]' ] && [ "$(requests)" = 3 ] && ok "a read past the window is live (the etag check that found it moved, then the read)" || bad "expired: $a / $(requests)"
reset; setfact '[]'; export SG_GH_CACHE_WINDOW=0; $CLI $CHEAP >/dev/null; $CLI $CHEAP >/dev/null
[ "$(requests)" = 2 ] && ok "SG_GH_CACHE_WINDOW=0 turns the snapshot off" || bad "window 0: $(requests)"
reset; setfact '[]'; export SG_GH_CACHE_WINDOW=junk; $CLI $CHEAP >/dev/null; $CLI $CHEAP >/dev/null
[ "$(requests)" = 1 ] && ok "a bad SG_GH_CACHE_WINDOW falls back to the documented default" || bad "bad window: $(requests)"
grep -q "SG_GH_CACHE_WINDOW" "$here/ghthrottle.py" && grep -q "SG_GH_CACHE_WINDOW" "$here/ghcall.sh" && ok "the window is documented where it is read" || bad "the window is not documented"

echo "an expensive fact is keyed:"
reset; setfact '[{"filename":"a.py"}]' v1; $CLI $KEYED >/dev/null; backdate $KEYED
a=$($CLI $KEYED); b=$($CLI $KEYED)
[ "$a" = '[{"filename":"a.py"}]' ] && [ "$a" = "$b" ] && ok "an unchanged ETag answers with the entry" || bad "unchanged: $a / $b"
[ "$(requests)" = 2 ] && grep -q 'If-None-Match: "v1"' "$T/gh.log" && ok "after one conditional request, and the entry was refreshed" || bad "unchanged requests: $(requests): $(cat "$T/gh.log")"
backdate $KEYED; setfact '[{"filename":"b.py"}]' v2
a=$($CLI $KEYED); n=$(requests); b=$($CLI $KEYED)
[ "$a" = '[{"filename":"b.py"}]' ] && [ "$n" = 4 ] && ok "a changed ETag invalidates the entry: a conditional request, then a live read" || bad "changed: $a / $n"
[ "$b" = "$a" ] && [ "$(requests)" = 4 ] && ok "and the next repeat is the new entry" || bad "after change: $b / $(requests)"

echo "a corrupt or absent entry is a live read:"
for damage in '' '{not json' '[]' '{"route":"x","at":"soon","body":"[]"}'; do
  reset; setfact '["live"]'; $CLI $CHEAP >/dev/null; printf '%s' "$damage" > "$(entry $CHEAP)"
  a=$($CLI $CHEAP); rc=$?
  [ "$rc" = 0 ] && [ "$a" = '["live"]' ] && [ "$(requests)" = 2 ] && ok "entry [${damage:-empty}] reads live, no error" || bad "entry [$damage]: rc $rc / $a / $(requests)"
done
reset; setfact '["live"]'; a=$($CLI $CHEAP); [ "$a" = '["live"]' ] && ok "an absent entry reads live" || bad "absent: $a"
reset; setfact '["live"]'; printf 'a file' > "$T/blocker"; a=$(SG_STATE="$T/blocker" $CLI $CHEAP); [ "$a" = '["live"]' ] && ok "an unwritable state dir costs the snapshot, not the read" || bad "unwritable: $a"
reset; export STUB_FAIL=1; $CLI $CHEAP >/dev/null; rc=$?; unset STUB_FAIL; setfact '["fine"]'; a=$($CLI $CHEAP)
[ "$rc" = 2 ] && [ "$a" = '["fine"]' ] && [ "$(requests)" = 2 ] && ok "a refusal is not kept" || bad "refusal kept: $rc / $a / $(requests)"

echo "the liveness rule: nothing that gates a write is read from the snapshot:"
reset; setfact '{"state":"open"}'
ROUTES="repos/o/r/issues/7 repos/o/r/pulls/7 repos/o/r/pulls?state=open&per_page=100 repos/o/r/issues?state=open repos/o/r/commits/main/status repos/o/r/branches/main repos/o/r/git/ref/heads/main"
for r in $ROUTES; do poison "$r" '{"state":"closed","poison":true}'; done
for r in $ROUTES; do
  before=$(requests); out=$($CLI "$r")
  if [ "$(requests)" -gt "$before" ] && ! printf '%s' "$out" | grep -q poison; then ok "$r is read live"; else bad "$r was answered from the snapshot: $out"; fi
done
for r in repos/o/r/issues/7 repos/o/r/commits/main/status; do   # the door a shell caller uses for a decision
  before=$(requests); out=$(. "$T/ghcall.sh"; SG_GHREAD="python3 $T/ghread.py" gh_api "$r")
  if [ "$(requests)" -gt "$before" ] && ! printf '%s' "$out" | grep -q poison; then ok "gh_api $r is live"; else bad "gh_api $r: $out"; fi
done
before=$(requests); out=$(. "$T/ghcall.sh"; gh_snapshot repos/o/r/issues/7)   # a gating route handed to the snapshot door by mistake
if [ "$(requests)" -gt "$before" ] && ! printf '%s' "$out" | grep -q poison; then ok "a gating route handed to gh_snapshot is still live"; else bad "gh_snapshot gating: $out"; fi
before=$(requests); out=$(python3 "$T/ghread.py" --method PATCH repos/o/r/issues/7 -f state=closed); rc=$?
if [ "$rc" = 0 ] && [ "$(requests)" -gt "$before" ] && tail -1 "$T/gh.log" | grep -q -- '--method PATCH repos/o/r/issues/7 -f state=closed' && ! printf '%s' "$out" | grep -q poison; then ok "the write path sends the write and ignores the snapshot"; else bad "write: $rc / $out / $(tail -1 "$T/gh.log")"; fi

echo
[ "$fails" -eq 0 ] && { echo "ghcache: all cases passed"; exit 0; }
echo "ghcache: $fails case(s) FAILED"; exit 1
