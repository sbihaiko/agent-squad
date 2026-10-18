#!/bin/bash
# The requeue count belongs to the issue, not the run (#532): each run that a review sent back started afresh, and one issue
# was dispatched 19 times. The third requeue of one issue stops it: its dispatch marker stays, its card keeps the reached
# column with the owner freeze marker, and a SQUAD-NOT-STARTABLE comment is queued, which both dispatch passes read. A
# requeue below the cap is unchanged, except that a refused card write is named.
# usage: bash test-wave-requeue-cap.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/bin"; cp "$d/wave.sh" "$d/address.sh" "$d/ghcall.sh" "$T/s/"
printf 'export SG_STATE=%s SG_REPO=x/y SG_OWNER=o SG_PROJECT=3 BOARD=%s/board.sh\n. %s/address.sh\n' "$T/st" "$T" "$T/s" > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\nif [ "${BOARD_REFUSE:-}" = 1 ]; then echo "board refused" >&2; exit 1; fi\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
printf '#!/bin/bash\nprintf "%%s\\n" "$*" >> "%s/gh.log"\nprintf "{}"\n' "$T" > "$T/bin/gh"; chmod +x "$T/bin/gh"
nsc() { cat "$T"/st/cq/* 2>/dev/null | /usr/bin/grep -c '^SQUAD-NOT-STARTABLE:'; }
for i in 1 2; do
  echo req > "$T/st/auto/disp-7"
  PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x" >/dev/null
done
[ ! -e "$T/st/auto/disp-7" ] && [ "$(/usr/bin/grep -c 'o 3 Todo 7' "$T/board.log")" = 2 ] && [ "$(nsc)" = 0 ] \
  && ok "two requeues free the issue and put it back in Todo" || ko "below the cap: marker $(/bin/ls "$T/st/auto"), board '$(tr '\n' ' ' < "$T/board.log")', stops $(nsc)"
echo req > "$T/st/auto/disp-7"
o=$(PATH="$T/bin:$PATH" bash "$T/s/wave.sh" requeue 7 "the review asked for changes: x")
[ -e "$T/st/auto/disp-7" ] && ok "the third requeue keeps the dispatch marker" || ko "the third requeue freed the issue"
! /usr/bin/grep -q 'o 3 Blocked 7' "$T/board.log" && ok "the third requeue does not move the card to Blocked" || ko "board: '$(tr '\n' ' ' < "$T/board.log")'"
/usr/bin/grep -Fqx -- 'api -X POST repos/x/y/issues/7/labels -f labels[]=frozen:owner' "$T/gh.log" && ok "the third requeue freezes the card for the owner" || ko "freeze: '$(tr '\n' ' ' < "$T/gh.log" 2>/dev/null)'"
[ "$(nsc)" = 1 ] && ok "the third requeue queues a SQUAD-NOT-STARTABLE comment" || ko "no stop comment queued"
case "$o" in *"WAVE STOP #7"*) ok "the stop is logged on one line";; *) ko "no stop line: '$o'";; esac
echo req > "$T/st/auto/disp-8"; o8=$(PATH="$T/bin:$PATH" BOARD_REFUSE=1 bash "$T/s/wave.sh" requeue 8 "y" 2>&1)
[ ! -e "$T/st/auto/disp-8" ] && ok "another issue keeps its own count" || ko "issue 8 was stopped by issue 7's count: $o8"
case "$o8" in *"WAVE-BOARD #8 Todo refused: board refused"*) ok "a refused card write is reported";; *) ko "the refused card write was dropped: '$o8'";; esac
rm -rf "$T"; exit $fail
