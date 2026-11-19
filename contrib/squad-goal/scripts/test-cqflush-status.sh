#!/bin/bash
# seconds: 1.54
# One loop status comment per issue, edited in place (#328): a queued comment whose name carries a marker key
# (<owner>__<repo>#<n>-status~<tag>.md, or -delivery~) is not posted blindly. cqflush.sh finds the comment that holds the hidden
# marker on that issue and edits it (PATCH); it posts only when none exists; and it sends no write when the body is already the same.
# An unmarked comment is posted as before. Offline: fixture profile, stub gh that keeps the comments of one issue.
# usage: bash test-cqflush-status.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}
T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
G=/usr/bin/grep; P=$T/profiles; B=$T/state; mkdir -p "$P" "$T/bin" "$T/plain" "$T/c" "$B/alpha/cq"
printf ': "${SG_REPO:=x/alpha}"; : "${SG_OWNER:=x}"; : "${SG_PROJECT:=1}"; : "${SG_CHECKOUT:=%s}"\n' "$T/co" > "$P/alpha.sh"
cat > "$T/bin/gh" <<'GH'
#!/bin/bash
# keeps the comments of one issue in $C/<id> and its labels in $T/lab/<n>; logs every write as POST, PATCH or DELETE
for a; do case "$a" in body=@*) bf=${a#body=@};; esac; done
all="$*"
case "$all" in
  *per_page*) for f in "$C"/*; do [ -e "$f" ] && jq -Rs --arg id "$(basename "$f")" '{id: ($id|tonumber), body: .}' < "$f"; done | jq -s . ; exit 0;;
  *"--method DELETE"*issues/comments/*) id=${all##*issues/comments/}; id=${id%% *}; rm -f "$C/$id"; echo "DELETE $id" >> "$GHLOG";;
  *"--method DELETE"*issues/*/labels/*) n=$(printf '%s' "$all" | sed -n 's#.*issues/\([0-9]*\)/labels/.*#\1#p'); lab=${all##*/labels/}; lab=${lab%% *}
    [ -n "${REFUSE_LABEL:-}" ] && { printf '{"message":"Resource not accessible by integration","status":"403"}'; exit 1; }
    if [ -f "$LAB/$n" ] && /usr/bin/grep -qxF "$lab" "$LAB/$n"; then
      /usr/bin/grep -vxF "$lab" "$LAB/$n" > "$LAB/$n.new" || true; mv -f "$LAB/$n.new" "$LAB/$n"; echo "DELETE labels/$n/$lab" >> "$GHLOG"
    else printf '{"message":"Not Found","status":"404"}'; exit 1; fi;;
  *"--method PATCH"*issues/comments/*) id=${all##*issues/comments/}; id=${id%% *}; cp "$bf" "$C/$id"; echo "PATCH $id" >> "$GHLOG";;
  *issues/comments/*) id=${all##*issues/comments/}; id=${id%% *}; jq -Rs --arg id "$id" '{id: ($id|tonumber), body: .}' < "$C/$id";;
  *body=@*) id=$(( $(ls "$C" | wc -l) + 1 )); cp "$bf" "$C/$id"; echo "POST $id" >> "$GHLOG"; echo "$id";;
esac
GH
chmod +x "$T/bin/gh"
export HOME=$T/home; mkdir -p "$T/home"; export SG_PROFILES_DIR=$P SG_STATE_BASE=$B SG_SQUAD=/dev/null GHLOG=$T/ghlog C=$T/c LAB=$T/lab PATH="$T/bin:$PATH"; : > "$T/ghlog"
unset SG_TOOLING_REPO SG_PROFILE SG_STATE SG_REPO SG_CHECKOUT
put() { printf '%s\n' "$2" > "$B/alpha/cq/$1"; touch -t 202601010000 "$B/alpha/cq/$1"; }
flush() { (cd "$T/plain" && bash "$d/cqflush.sh" --once 2>&1); }
ncom() { ls "$T/c" | wc -l | tr -d " "; }
writes() { $G -c . "$T/ghlog"; }

# 1. the first status comment is posted, and carries the hidden marker on its first line
put 'x__alpha#7-status~r1.md' 'review round 1: CHANGES'
out=$(flush); [ "$(cat "$T/ghlog")" = "POST 1" ] && [ "$(head -1 "$T/c/1")" = '<!-- sg-loop:status -->' ] && $G -q 'round 1' "$T/c/1" \
  && ok "the first status comment is posted with its marker" || ko "first: $(cat "$T/ghlog") / $out / $(cat "$T/c/1" 2>/dev/null)"

# 2. a second review round edits that comment and posts no new one (criterion a)
put 'x__alpha#7-status~r2.md' 'review round 2: APPROVE'
out=$(flush); [ "$(cat "$T/ghlog")" = "$(printf 'POST 1\nPATCH 1')" ] && [ "$(ncom)" = 1 ] && $G -q 'round 2' "$T/c/1" && ! $G -q 'round 1' "$T/c/1" \
  && ok "a second round edits the status comment and posts no new one" || ko "second: $(cat "$T/ghlog") / $out / $(ls "$T/c")"

# 3. a pass with nothing new to say sends no write
put 'x__alpha#7-status~r3.md' 'review round 2: APPROVE'
out=$(flush); [ "$(writes)" = 2 ] && [ -f "$B/alpha/cq/x__alpha#7-status~r3.md.done" ] \
  && ok "a status with the same body sends no write and is retired" || ko "same body: $(cat "$T/ghlog") / $out"

# 4. the delivery comment is its own marker, so a status comment is never overwritten by it
put 'x__alpha#7-delivery~merge.md' 'merged as abc'
out=$(flush); [ "$(ncom)" = 2 ] && $G -q 'round 2' "$T/c/1" && [ "$(head -1 "$T/c/2")" = '<!-- sg-loop:delivery -->' ] \
  && ok "the delivery comment is a separate marker comment" || ko "delivery: $(cat "$T/ghlog") / $out"

# 5. a merge and its close leave one delivery comment, not two (criterion b)
put 'x__alpha#7-delivery~close.md' 'closed after merging abc'
out=$(flush); [ "$(ncom)" = 2 ] && $G -q 'closed after' "$T/c/2" && ! $G -q 'merged as' "$T/c/2" \
  && ok "a second delivery write edits the one delivery comment" || ko "two deliveries: $(cat "$T/ghlog") / $out / $(ls "$T/c")"

# 6. an unmarked comment is posted as before
put 'x__alpha#7-note.md' 'plain'
out=$(flush); [ "$(ncom)" = 3 ] && ok "an unmarked comment is still posted" || ko "unmarked: $(cat "$T/ghlog") / $out"

# 7. a re-arm (#673, issue_ledger.py rearm) queues <n>-unstart~<tag>.md: the label goes first, then every SQUAD-NOT-STARTABLE comment (#1297),
#    and so does the frozen:not-startable label the marker wrote (#1297), because the label alone keeps both dispatchers
#    skipping. No other comment goes. A second one with nothing left to remove tolerates the 404 the label answers.
printf 'SQUAD-NOT-STARTABLE: 3 failed dispatches\n' > "$T/c/8"; printf 'a plain note\n' > "$T/c/9"
mkdir -p "$LAB"; printf 'bug\nfrozen:not-startable\n' > "$LAB/7"; : > "$T/ghlog"
put 'x__alpha#7-unstart~rearm1.md' 'SQUAD-NOT-STARTABLE:'
out=$(flush); [ ! -e "$T/c/8" ] && [ -e "$T/c/9" ] && [ "$(cat "$T/ghlog")" = "$(printf 'DELETE labels/7/frozen:not-startable\nDELETE 8')" ] \
  && [ "$(cat "$LAB/7")" = "bug" ] && [ -f "$B/alpha/cq/x__alpha#7-unstart~rearm1.md.done" ] \
  && case "$out" in *"removed 1 comment(s), label frozen:not-startable removed"*) true;; *) false;; esac \
  && ok "a re-arm removes the SQUAD-NOT-STARTABLE comment and the label, and no other comment" || ko "unstart: $(cat "$T/ghlog") / $out / $(ls "$T/c") / label '$(cat "$LAB/7" 2>/dev/null)'"
: > "$T/ghlog"
put 'x__alpha#7-unstart~rearm2.md' 'SQUAD-NOT-STARTABLE:'
out=$(flush); [ ! -s "$T/ghlog" ] && [ -f "$B/alpha/cq/x__alpha#7-unstart~rearm2.md.done" ] \
  && case "$out" in *"removed 0 comment(s), label frozen:not-startable absent"*) true;; *) false;; esac \
  && ok "a re-arm with nothing to remove tolerates the 404 and sends no other write" || ko "unstart again: $(cat "$T/ghlog") / $out"
# 8. (#1297) a label the API refuses (403, a secondary limit) is not a 404: the comments stay and the file stays queued, so the
#    next pass removes both. The label goes first, so a refusal leaves the marker, the evidence, where it was.
printf 'SQUAD-NOT-STARTABLE: 3 failed dispatches\n' > "$T/c/10"; : > "$T/ghlog"; printf 'bug\nfrozen:not-startable\n' > "$LAB/7"
put 'x__alpha#7-unstart~rearm3.md' 'SQUAD-NOT-STARTABLE:'
export REFUSE_LABEL=1; out=$(flush); unset REFUSE_LABEL
q=$B/alpha/cq/x__alpha#7-unstart~rearm3.md
[ -e "$T/c/10" ] && [ ! -s "$T/ghlog" ] && [ -f "$q" ] && [ ! -e "$q.dead" ] && [ ! -e "$q.done" ] && [ "$(cat "$q.tries" 2>/dev/null)" = 1 ] \
  && ok "a refused label keeps the comment and the file queued for a retry" || ko "refused label: $(cat "$T/ghlog") / $out / comments $(ls "$T/c") / queue $(ls "$B/alpha/cq")"
out=$(flush)
[ ! -e "$T/c/10" ] && [ -f "$q.done" ] && [ "$(cat "$LAB/7")" = bug ] \
  && ok "the retry removes the comment and the label" || ko "retry: $(cat "$T/ghlog") / $out / comments $(ls "$T/c") / label '$(cat "$LAB/7")'"

# 9. (#1301) a re-arm also clears the issue's notstartable claim files, so a later failure with the same reason is marked again:
#    the claim is health.sh's memory that the marker was posted, and the marker is gone, so the claim names a comment that is not
#    there. Only that issue's claims go; another issue's claim and a non-claim file in auto are left alone.
mkdir -p "$B/alpha/auto"
printf 'SQUAD-NOT-STARTABLE: 3 failed dispatches\n' > "$T/c/12"; : > "$T/ghlog"; printf 'bug\nfrozen:not-startable\n' > "$LAB/7"
: > "$B/alpha/auto/notstartable-7-some_reason"; : > "$B/alpha/auto/notstartable-7-killed-no-process"
: > "$B/alpha/auto/notstartable-8-other_reason"; : > "$B/alpha/auto/health-runs-repeating-failure"
put 'x__alpha#7-unstart~rearm4.md' 'SQUAD-NOT-STARTABLE:'
out=$(flush)
{ [ ! -e "$B/alpha/auto/notstartable-7-some_reason" ] && [ ! -e "$B/alpha/auto/notstartable-7-killed-no-process" ] \
  && [ -e "$B/alpha/auto/notstartable-8-other_reason" ] && [ -e "$B/alpha/auto/health-runs-repeating-failure" ]; } \
  && ok "#1301 a re-arm clears the issue's notstartable claims and no other issue's" \
  || ko "re-arm claims: $(ls "$B/alpha/auto" | tr '\n' ' ') / $out"
# a label the API refuses leaves the claims: the marker is still in place, so its memory stays too
printf 'SQUAD-NOT-STARTABLE: 3 failed dispatches\n' > "$T/c/13"; : > "$T/ghlog"; printf 'bug\nfrozen:not-startable\n' > "$LAB/7"
: > "$B/alpha/auto/notstartable-7-refused_reason"
put 'x__alpha#7-unstart~rearm5.md' 'SQUAD-NOT-STARTABLE:'
export REFUSE_LABEL=1; out=$(flush); unset REFUSE_LABEL
{ [ -e "$T/c/13" ] && [ -e "$B/alpha/auto/notstartable-7-refused_reason" ]; } \
  && ok "#1301 a refused re-arm keeps the claims while the marker stays" \
  || ko "refused re-arm claims: $(ls "$B/alpha/auto" | tr '\n' ' ') / $out / $(ls "$T/c")"
out=$(flush)
{ [ ! -e "$T/c/13" ] && [ ! -e "$B/alpha/auto/notstartable-7-refused_reason" ]; } \
  && ok "#1301 the retry clears the claims with the marker" \
  || ko "retry claims: $(ls "$B/alpha/auto" | tr '\n' ' ') / $out / $(ls "$T/c")"
rm -rf "$T"; exit $fail
