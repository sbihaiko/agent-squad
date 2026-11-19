#!/bin/bash
# seconds: 3.28
# A reload (#873) ends the comment flusher at the end of its pass: a TERM that arrives while a post is in flight lets that
# post land and be marked done, and the flusher starts no further file. A TERM that cut the post would leave a .posting
# file that no pass reads again, so the comment would be lost.
# Offline: a fixture profile and a stub gh that holds each post for 2 s and records it.
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain" "$B/alpha/cq"
printf ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' "$T/co" > "$P/alpha.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
for a; do case "$a" in body=@*) b=$(cat "${a#body=@}");; esac; done
printf '%s | %s\n' "$*" "$b" >> "$GHLOG"; : > "$GHLOG.posting"; sleep 2
GH
chmod +x "$T/bin/gh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$B GHLOG=$T/ghlog SG_FLUSH_INTERVAL=1 PATH="$T/bin:$PATH"
unset SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT SG_SQUAD
for n in 3 4; do printf 'held %s\n' "$n" > "$B/alpha/cq/x__alpha#$n-note.md"; touch -t 202601010000 "$B/alpha/cq/x__alpha#$n-note.md"; done

(cd "$T/plain" && exec bash "$d/cqflush.sh") > "$T/flush.out" 2>&1 & pid=$!
for _ in $(seq 1 50); do [ -e "$GHLOG.posting" ] && break; sleep 0.1; done
kill -TERM "$pid" 2>/dev/null
for _ in $(seq 1 100); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
kill -0 "$pid" 2>/dev/null && { ko "the flusher still runs after TERM"; kill -9 "$pid" 2>/dev/null; } || ok "the flusher exits after TERM"

[ -f "$B/alpha/cq/x__alpha#3-note.md.done" ] && $G -q '| held 3$' "$GHLOG" && ok "the post in flight lands and is marked done" || ko "the post in flight was cut: $(ls "$B/alpha/cq") / $(cat "$GHLOG" 2>/dev/null)"
[ ! -e "$B/alpha/cq/x__alpha#3-note.md.posting" ] && ok "no post is left in .posting" || ko "a post is left in .posting"
! $G -q 'issues/4/' "$GHLOG" && [ ! -e "$B/alpha/cq/x__alpha#4-note.md.done" ] && ok "the next file is not started" || ko "the next file was started: $(cat "$GHLOG" 2>/dev/null)"
rm -f "$B/alpha/cq/"*; rm -f "$GHLOG"
(cd "$T/plain" && SG_FLUSH_INTERVAL=600 exec bash "$d/cqflush.sh") > "$T/idle.out" 2>&1 & ipid=$!
sleep 1; kill -TERM "$ipid" 2>/dev/null
for _ in $(seq 1 30); do kill -0 "$ipid" 2>/dev/null || break; sleep 0.1; done
kill -0 "$ipid" 2>/dev/null && { ko "an idle flusher waits out its interval after TERM"; pkill -P "$ipid" 2>/dev/null; kill -9 "$ipid" 2>/dev/null; } || ok "an idle flusher exits at once after TERM"
rm -rf "$T"; exit $fail
