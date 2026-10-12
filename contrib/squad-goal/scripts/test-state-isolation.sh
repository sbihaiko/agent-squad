#!/bin/bash
# State isolation (#20): every profile has its own state directory, <SG_STATE_BASE>/<profile>, so two repositories that
# carry the same issue number never see each other's markers, live-run list or queued comments; and cqflush.sh posts each
# queued comment to the repository its address names (the state directory it sits in, plus its number), never to the
# repository of the cwd it was started in. Two fixture profiles, stub gh, no network.
# usage: bash test-state-isolation.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain"
for p in alpha:x beta:y; do printf ': "${SG_REPO:=%s/%s}"; : "${SG_OWNER:=%s}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' \
  "${p#*:}" "${p%%:*}" "${p#*:}" "$T/co-${p%%:*}" > "$P/${p%%:*}.sh"; done
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# records the endpoint and the body each comment was posted with
for a; do case "$a" in body=@*) b=$(cat "${a#body=@}");; esac; done
printf '%s | %s\n' "$*" "$b" >> "$GHLOG"
GH
chmod +x "$T/bin/gh"
export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog PATH="$T/bin:$PATH"; : > "$T/ghlog"
unset SG_TOOLING_REPO   # retired: env.sh stops on it (#20)
unset SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT

# the loop of each repository writes issue 7's dispatch marker, its live run and a queued comment where env.sh puts them
for p in alpha beta; do
  (cd "$T/plain" && SG_PROFILE=$p bash -c '. "$1/env.sh" || exit 1; mkdir -p "$SG_STATE/auto"; echo "$SG_PROFILE" > "$SG_STATE/auto/disp-7"
    echo "run-$SG_PROFILE-7" >> "$SG_STATE/active-runs.txt"; echo "comment from $SG_PROFILE" > "$SG_STATE/cq/7-note.md"
    echo "$SG_STATE" > "$2/state-$SG_PROFILE"' _ "$d" "$T") || ko "env.sh refused profile $p"
done
[ "$(cat "$T/state-alpha" 2>/dev/null)" = "$B/alpha" ] && [ "$(cat "$T/state-beta" 2>/dev/null)" = "$B/beta" ] \
  && ok "SG_STATE is <base>/<profile> for each profile" || ko "state dirs: alpha=$(cat "$T/state-alpha" 2>/dev/null) beta=$(cat "$T/state-beta" 2>/dev/null)"
for p in alpha beta; do
  [ "$(cat "$B/$p/auto/disp-7" 2>/dev/null)" = "$p" ] && ok "$p sees only its own marker for issue 7" || ko "$p's marker for issue 7: '$(cat "$B/$p/auto/disp-7" 2>/dev/null)'"
  [ "$(cat "$B/$p/active-runs.txt" 2>/dev/null)" = "run-$p-7" ] && ok "$p's live-run list holds only its own run" || ko "$p's live-run list: '$(cat "$B/$p/active-runs.txt" 2>/dev/null | tr '\n' ' ')'"
  [ "$(cat "$B/$p/cq/7-note.md" 2>/dev/null)" = "comment from $p" ] && ok "$p's queue holds only its own comment" || ko "$p's queued comment: '$(cat "$B/$p/cq/7-note.md" 2>/dev/null)'"
done

# a comment queued under a state directory no profile owns is never posted anywhere
mkdir -p "$B/ghost/cq"; echo "orphan" > "$B/ghost/cq/7-x.md"
touch -t 202601010000 "$B"/*/cq/*.md
# one flusher, started in a directory no profile matches, with no profile in force, posts every queue to its own repository
out=$(cd "$T/plain" && bash "$d/cqflush.sh" --once 2>&1)
$G -q 'repos/x/alpha/issues/7/comments.* | comment from alpha$' "$T/ghlog" && ok "alpha's comment is posted to x/alpha#7" || ko "alpha's comment: $(cat "$T/ghlog") $out"
$G -q 'repos/y/beta/issues/7/comments.* | comment from beta$' "$T/ghlog" && ok "beta's comment is posted to y/beta#7" || ko "beta's comment: $(cat "$T/ghlog") $out"
[ "$($G -c . "$T/ghlog")" = 2 ] && ok "nothing else is posted" || ko "posts: $(cat "$T/ghlog")"
[ -f "$B/alpha/cq/7-note.md.done" ] && [ -f "$B/beta/cq/7-note.md.done" ] && ok "both comments are marked done" || ko "done marks: $(ls "$B"/*/cq)"
[ -f "$B/ghost/cq/7-x.md.dead" ] && printf '%s' "$out" | $G -q 'ghost' && ok "the orphan comment is set aside dead and reported by name" || ko "orphan: $(ls "$B/ghost/cq") / $out"
rm -rf "$T"; exit $fail
