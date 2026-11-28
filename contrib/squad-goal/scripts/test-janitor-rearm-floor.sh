#!/bin/bash
# seconds: 1.08
# Regression test for janitor rule O after a rearm (#692) and for its rescans (#693). Stubbed gh/board/wave.sh; a temp state dir.
#  (A) a rearm keeps a floor: a run the ledger listed before the rearm, or one whose verdict is older than the rearm, is never
#      sent to `wave.sh fail` again, so a rearmed issue is not stopped again by its old runs;
#  (B) a run that ended after the rearm counts, and a run folder is read once per verdict: a second pass sends nothing again;
#  (C) an issue whose child entered the run's wave is the wave's to decide, never a failed dispatch, whether or not the run landed.
# usage: bash test-janitor-rearm-floor.sh [janitor.py]
src=${1:-$(dirname "$0")/janitor.py}; here="$(cd "$(dirname "$src")" && pwd -P)"; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
# The pass loads squad/procs.py from the tree the script sits in, then SG_SQUAD, SG_CHECKOUT, else the caller's cwd and its
# parents. The fixture copies the janitor into a repo-shaped temp tree (<root>/contrib/squad-goal/scripts), so the first
# answer is the tree the copy sits in, as it is for the real script; <root>/squad links the tree the script ships in. Every
# run starts from a temp cwd outside any repository with no SG_SQUAD, the shape of the daily pass (#3097), so a pass that
# resolves its root from the caller's cwd or a variable fails here first.
squad_root=$(cd "$here/../../.." && pwd -P)
S="$T/repo/contrib/squad-goal/scripts"
mkdir -p "$T/bin" "$S" "$T/cwd" "$T/st/auto" "$T/st/cq" "$T/co/runs"; ln -s "$squad_root/squad" "$T/repo/squad"
cp "$src" "$S/janitor.py"; cp "$here/issue_state.py" "$here/issue_ledger.py" "$S/"; cp "$here/board_columns.py" "$here/board-definition.json" "$S/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$S/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/wave.calls\n' "$T" > "$S/wave.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) for n in 801 802 803; do printf '%s\tTodo\n' "$n"; done;;
  *issues/8*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# mk <run> <issue> <verdict ts>: a failed run that delivered nothing for the issue
mk() { mkdir -p "$T/co/runs/$1"; echo "$1" >> "$T/st/active-runs.txt"; printf 'CHILD — GitHub issue(s) %s (one PR):\n' "$2" > "$T/co/runs/$1/request.txt"
  printf '{"event": "spawn_accepted", "child": "c1", "definition": "GitHub issue #%s"}\n{"event": "verdict", "verdict": "failed", "reasons": ["x"], "exit_code": 1, "ts": %s}\n' "$2" "$3" > "$T/co/runs/$1/record.jsonl"
  touch -t 202001010000 "$T/co/runs/$1/record.jsonl"; }
L() { SG_STATE="$T/st" SG_REPO=x/y python3 "$S/issue_ledger.py" "$@" >/dev/null; }
mk run-a1 801 1000; mk run-a2 801 1001; mk run-a3 801 1002
L fail 'x/y#801' run-a1 '' 'ended failed: x'; L fail 'x/y#801' run-a2 '' 'ended failed: x'; L rearm 'x/y#801'
mk run-new 802 4000000000
mk run-ent 803 4000000000; : > "$T/st/auto/waved-run-ent"; L wave 'x/y#803' run-ent entered 'entered wave/run-ent'
pass() ( cd "$T/cwd" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 JANITOR_NOTIFY="$T/notify" python3 -I "$S/janitor.py" 2>&1 )
o=$(pass)
/usr/bin/grep -q '^fail 801 ' "$T/wave.calls" 2>/dev/null && ko "A: an old run of a rearmed issue was counted again: $(cat "$T/wave.calls")" || ok "A: a rearmed issue's old runs count nothing"
[ "$(/usr/bin/grep -c '^fail 802 run-new ' "$T/wave.calls" 2>/dev/null)" = 1 ] && ok "B: a run that ended after the rearm counts" || ko "B: run-new not counted: '$(cat "$T/wave.calls" 2>/dev/null)' $o"
/usr/bin/grep -q '^fail 803 ' "$T/wave.calls" 2>/dev/null && ko "C: an issue whose child entered the wave was counted: $(cat "$T/wave.calls")" || ok "C: an issue whose child entered the wave is the wave's"
o=$(pass)
[ "$(/usr/bin/grep -c '^fail 802 ' "$T/wave.calls" 2>/dev/null)" = 1 ] && ok "B: a second pass reads the run folder no more" || ko "B: a run was counted twice: '$(cat "$T/wave.calls" 2>/dev/null)'"
rm -rf "$T"; exit $fail
