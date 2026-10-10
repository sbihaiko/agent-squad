#!/bin/bash
# Regression test for janitor rule I (RUN-ON-CLOSED-ISSUES: a live run whose issues are all closed; duplicates/not_planned -> stop, otherwise report once), stubbed gh. usage: bash test-janitor-i.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/auto" "$T/co/runs"; cp "$src" "$T/janitor.py"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *issues/501*) printf 'closed\tnot_planned\tsome bug\n';;
  *issues/502*) printf 'closed\tcompleted\t[duplicate of #9] bug\n';;
  *issues/503*) printf 'closed\tcompleted\treal fix\n';;
  *issues/504*) printf 'open\t\tstill open\n';;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
mk() { mkdir -p "$T/co/runs/$1"; printf '%s\n' "{\"event\": \"spawn_accepted\", \"x\": \"GitHub issue #$2\"}" > "$T/co/runs/$1/record.jsonl"; [ -n "$3" ] && printf '%s\n' "{\"event\": \"spawn_accepted\", \"x\": \"GitHub issue #$3\"}" >> "$T/co/runs/$1/record.jsonl"; echo "$1" >> "$T/st/active-runs.txt"; }
mk run-a 501 502; mk run-b 503; mk run-c 501 504
run() { SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_BASE=main JANITOR_DRY=1 python3 -I "$T/janitor.py" 2>&1; }
o=$(run)
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-a.*stopping' && ok "all duplicate/not_planned -> stop" || ko "run-a not stopped: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-b.*not stopped' && ok "closed as completed -> report only" || ko "run-b: $o"
echo "$o" | /usr/bin/grep -q 'RUN-ON-CLOSED-ISSUES run-c' && ko "run with an open issue reported" || ok "run with an open issue untouched"
rm -rf "$T"; exit $fail
