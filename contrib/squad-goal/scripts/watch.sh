#!/bin/bash
# Monitor stream: one line per run verdict, new PR, or review+CI summary. Run from the target checkout.
# active-runs.txt holds a run id (under ./runs) or an absolute run folder (launch.py writes absolute paths).
. "$(dirname "$0")/env.sh"
# reported verdicts / stalls persist across restarts ($W); a first start (no file) records current verdicts silently
W="$SG_STATE/watch-seen.txt"; boot=0; [ -e "$W" ] || { boot=1; : > "$W"; }
seen=" $(sed -n 's/^seen //p' "$W" | tr '\n' ' ')"; stalled=" $(sed -n 's/^stall //p' "$W" | tr '\n' ' ')"; prs=" $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number' | tr '\n' ' ')"
for f in "$SG_STATE"/revci-*.out; do grep '^#' "$f" 2>/dev/null | tail -1; done | sort > "$SG_STATE/.rs"
while :; do
  for r in $(cat "$SG_STATE/active-runs.txt"); do case "$seen" in *" $r "*) continue;; esac
    v=$(python3 -c "import json,sys
landed=0; v=None
for l in open((sys.argv[1] if sys.argv[1].startswith('/') else 'runs/'+sys.argv[1])+'/record.jsonl'):
  d=json.loads(l)
  if d.get('event')=='land' and d.get('outcome')=='pass': landed=1
  if d.get('event')=='verdict': v=d
if v:
  rs=v.get('reasons') or []
  if v['verdict']=='failed' and landed and rs and all(x.startswith('ac-verify:no_ac_commands') for x in rs): print('landed (gate: no_ac_commands)')
  else: print(v['verdict'], ','.join(rs))" $r 2>/dev/null | tail -1)
    [ -n "$v" ] && { [ $boot = 1 ] || { echo "RUN ${r##*/} verdict=$v"
        # the run's verdict goes on every issue it worked (issue numbers read from the run's first records)
        rd=$r; [ "${r#/}" = "$r" ] && rd=runs/$r
        for n in $(head -c 200000 "$rd/record.jsonl" 2>/dev/null | grep -oE '(issue|Refs) #[0-9]{3,5}' | grep -oE '[0-9]+' | sort -u); do
          printf "Squad run %s finished: verdict %s. Refs #%s\n" "${r##*/}" "$v" $n > "$SG_STATE/cq/$n-run$$-${r##*-}.md"; done; }
      seen="$seen$r "; echo "seen $r" >> "$W"; }; done
  for n in $(gh api "repos/$SG_REPO/pulls?state=open&per_page=100" -q '.[].number' 2>/dev/null); do case "$prs" in *" $n "*) ;; *)
    t=$(gh api repos/$SG_REPO/pulls/$n -q '.head.ref+" "+.title+"\t"+(.body//"")'); echo "NEW PR #$n ${t%%$'\t'*}"; prs="$prs$n "
    for i in $(printf '%s' "$t" | grep -oE 'Refs #[0-9]{3,5}' | grep -oE '[0-9]+' | sort -u); do printf "PR #%s opened: %s. Refs #%s\n" $n "${t%%$'\t'*}" $i > "$SG_STATE/cq/$i-pr$n.md"; done;; esac; done
  for f in "$SG_STATE"/revci-*.out; do grep '^#' "$f" 2>/dev/null | tail -1; done | sort > "$SG_STATE/.rn"
  comm -13 "$SG_STATE/.rs" "$SG_STATE/.rn" | sed 's/^/REVIEW /'; cp "$SG_STATE/.rn" "$SG_STATE/.rs"
  # stall watchdog: live run whose record.jsonl has not moved for stall_timeout_minutes; review with no summary after review_stall_minutes
  cfg="$(dirname "$0")/models.json"; st=$(jq -r .stall_timeout_minutes "$cfg"); rs=$(jq -r .review_stall_minutes "$cfg")
  for r in $(cat "$SG_STATE/active-runs.txt"); do case "$seen" in *" $r "*) continue;; esac; case "$r" in /*) f=$r/record.jsonl;; *) f=runs/$r/record.jsonl;; esac
    [ -e "$f" ] && [ -n "$(find "$f" -mmin +$st 2>/dev/null)" ] && case "$stalled" in *" $r "*) ;; *) echo "STALL RUN ${r##*/} no record.jsonl progress for ${st}m"; stalled="$stalled$r "; echo "stall $r" >> "$W";; esac; done
  for f in "$SG_STATE"/revci-*.out; do [ -e "$f" ] || continue; grep -q '^#' "$f" && continue; k=$(basename "$f" .out)
    [ -n "$(find "$f" -mmin +$rs 2>/dev/null)" ] && case "$stalled" in *" $k "*) ;; *) echo "STALL REVIEW ${k#revci-} no result for ${rs}m"; stalled="$stalled$k "; echo "stall $k" >> "$W";; esac; done
  tick=$((tick+1)); [ $((tick % 8)) -eq 1 ] && RECONCILE=apply python3 "$(dirname "$0")/reconcile.py" 2>&1 | sed "s/^/BOARD /"   # card Status from GitHub facts, every ~12 min (GraphQL budget)
  [ $((tick % 4)) -eq 1 ] && "$(dirname "$0")/autobug.sh" 2>&1   # open bug issues dispatched automatically, every ~6 min
  "$(dirname "$0")/autopilot.sh" 2>&1   # auto review, auto merge on APPROVE+CI green, round cap
  "$(dirname "$0")/heartbeat.sh"   # hung test hosts (0% CPU) and dead runs
  "$(dirname "$0")/wtclean.sh" 2>&1   # finished, pushed child worktrees
  boot=0; for _ in 1 2; do sleep 30; "$(dirname "$0")/autopilot.sh" 2>&1; done; sleep 30; done   # autopilot every 30 s, the rest every 90 s
