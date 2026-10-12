#!/bin/bash
# Regression test for janitor rules K (never-started autofix run -> retryfix; `Nothing ran` frees disp-<n>) and M (PR-STUCK alert), stubbed gh/retryfix/notifier. usage: bash test-janitor-kmn.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/s/janitor.py"
printf '#!/bin/bash\necho "$1" >> %s/retry.calls\n' "$T" > "$T/s/retryfix.sh"; printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/601*) echo open;;
  *search/issues*) echo 0;;
  *pulls/71*) echo dirty;;
  *pulls/72*) echo dirty;;
  *pulls\?state=open*) printf '71 aaaaaaaaa1 main fix/71 2020-01-01T00:00:00Z\n72 bbbbbbbbb2 main fix/72 2020-01-01T00:00:00Z\n73 ccccccccc3 main fix/73 2999-01-01T00:00:00Z\n';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mkdir -p "$T/co/runs/run-never"; echo req > "$T/co/runs/run-never/request.txt"; touch -t 202001010000 "$T/co/runs/run-never/request.txt"; echo run-never > "$T/st/active-runs.txt"
mkdir -p "$T/co/runs/run-live"; echo "fix PR #72 please" > "$T/co/runs/run-live/request.txt"; echo '{"event": "call_start"}' > "$T/co/runs/run-live/record.jsonl"; echo run-live >> "$T/st/active-runs.txt"
echo req-x > "$T/st/auto/disp-601"; touch -t 202001010000 "$T/st/auto/disp-601"; echo "Nothing ran: vendor/skills is not clean" > "$T/st/req-x.log"
( cd "$T/co/runs/run-live" && exec sleep 60 ) & spid=$!; sleep 1
o=$(SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1); kill $spid 2>/dev/null
[ "$(basename "$(cat "$T/retry.calls" 2>/dev/null)")" = run-never ] && ok "K: never-started run -> retryfix called" || ko "K retryfix: $(cat "$T/retry.calls" 2>/dev/null) $o"
[ ! -e "$T/st/auto/disp-601" ] && ok "K: disp marker of a refused start dropped" || ko "K disp marker kept: $o"
/usr/bin/grep -q 'PR-STUCK #71' "$T/st/cycle.log" && ok "M: dirty PR without a live run -> PR-STUCK" || ko "M #71 not reported: $(cat "$T/st/cycle.log" 2>/dev/null)"
/usr/bin/grep -q 'PR-STUCK #72' "$T/st/cycle.log" && ko "M: PR with a live run reported" || ok "M: PR named by a live run left alone"
/usr/bin/grep -q 'PR-STUCK #73' "$T/st/cycle.log" && ko "M: fresh PR reported" || ok "M: recently updated PR left alone"
/usr/bin/grep -q 'PR #71 stuck' "$T/st/alerts.log" && [ -s "$T/notify.calls" ] && ok "M: alerts.log + notification" || ko "M alert missing"
rm -rf "$T"; exit $fail
