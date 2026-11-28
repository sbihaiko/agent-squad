#!/bin/bash
# seconds: 0.65
# Regression test for janitor rule O on a wave that landed without a child (#682). The generic "the wave of run X landed
# without its child" was counted loop-caused whatever the real cause was: an entry gate that failed, a review hold, a rebase
# conflict, a drop at the tip. The reason sent to `wave.sh fail` now names the cause wave.sh recorded for that child, a wave
# fact in the issue's ledger (ADR-0149, issue_ledger.py), or "unknown" when it recorded none, and it never carries the generic
# text. An old auto/excluded-<run>-<n> marker is read no more. A corrupt ledger holds the issue: no count is sent for it.
# Stubbed gh, board and wave.sh. usage: bash test-janitor-wave-exclusion.sh [janitor.py]
# (#3120) The janitor loads squad/procs.py from the tree it runs in: it walks up from its own directory, then SG_SQUAD,
# SG_CHECKOUT and cwd. The daily pass clears every SG_* and runs from a cwd outside the repository, so the suite drops the
# inherited names, symlinks the repository's squad package beside the janitor copy and runs every pass from the fixture's
# own folder. The product walk-up then answers with no SG_SQUAD and no caller cwd to stand in for the tree.
for v in $(compgen -e | /usr/bin/grep -E '^(SG_|SERIAL_)'); do unset "$v"; done   # a caller's SG_SQUAD or SG_CHECKOUT never answers for this suite
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
repo=$(cd "$(dirname "$src")/../../.." && pwd -P)
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/cq" "$T/co/runs"; ln -s "$repo/squad" "$T/s/squad"; cp "$src" "$T/s/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$(dirname "$src")/issue_ledger.py" "$T/s/"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/s/"
printf '#!/bin/bash\necho "$*" >> %s/board.calls\n' "$T" > "$T/s/board.sh"
printf '#!/bin/bash\necho "$*" >> %s/notify.calls\n' "$T" > "$T/notify"; chmod +x "$T/notify"
printf '#!/bin/bash\necho "$*" >> %s/wave.calls\n' "$T" > "$T/s/wave.sh"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *search/issues*) echo 0;;
  *projectsV2/4/fields*) echo FID4;;
  *projectsV2/4/items*) for n in 721 722 723 724; do printf '%s\tIn Progress\n' "$n"; done;;
  *issues/72*) echo open;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
# One run cut a wave (waved-<run>), its wave landed (wave-<run> is gone), and none of its three children entered it.
r=run-wx; mkdir -p "$T/co/runs/$r"; echo "$r" >> "$T/st/active-runs.txt"
printf 'CHILD — GitHub issue(s) 721, 722, 723, 724 (one PR each):\n' > "$T/co/runs/$r/request.txt"
{ printf '{"event": "run_start", "run_id": "rid-wx"}\n'
  for n in 721 722 723 724; do printf '{"event": "spawn_accepted", "child": "c%s", "definition": "GitHub issue #%s"}\n' "$n" "$n"; done
  printf '{"event": "land", "outcome": "pass"}\n{"event": "verdict", "verdict": "completed", "reasons": [], "exit_code": 0}\n'; } > "$T/co/runs/$r/record.jsonl"
echo wave/run-wx > "$T/st/auto/waved-$r"
export SG_STATE="$T/st" SG_REPO=x/y
python3 "$T/s/issue_ledger.py" wave 'x/y#721' "$r" refused 'the local gates failed: FAILED tests/test_x.py::test_y' >/dev/null   # keyed by the folder name
python3 "$T/s/issue_ledger.py" wave 'x/y#722' rid-wx refused 'the rebase on wave/run-wx conflicts: a.py' >/dev/null              # keyed by the run id
echo 'an old marker the ledger replaced' > "$T/st/auto/excluded-$r-723"                                                           # read no more
mkdir -p "$T/st/state"; printf '{"own": ' > "$T/st/state/x__y__724.json"                                                          # corrupt: holds #724
touch -t 202001010000 "$T/co/runs/$r/record.jsonl"
o=$(cd "$T" && SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_REPO=x/y SG_OWNER=o SG_PROJECT=4 JANITOR_NOTIFY="$T/notify" python3 -I "$T/s/janitor.py" 2>&1)
calls=$(sort "$T/wave.calls" 2>/dev/null)
case "$calls" in *"fail 721 $r the child of run rid-wx never entered its wave: the local gates failed: FAILED tests/test_x.py::test_y"*) ok "O: the reason names the cause wave.sh recorded (folder key)";; *) ko "O #721: '$calls' $o";; esac
case "$calls" in *"fail 722 $r the child of run rid-wx never entered its wave: the rebase on wave/run-wx conflicts: a.py"*) ok "O: the reason names the cause wave.sh recorded (run id key)";; *) ko "O #722: '$calls'";; esac
case "$calls" in *"fail 723 $r the child of run rid-wx never entered its wave: unknown"*) ok "O: a child with no recorded cause reads unknown";; *) ko "O #723: '$calls'";; esac
case "$calls" in *"fail 724 "*) ko "O: a corrupt ledger still sent a count: '$calls'";; *) ok "O: a corrupt ledger holds the issue: no count is sent";; esac
case "$o$(cat "$T/st/cycle.log" 2>/dev/null)" in *"#724"*corrupt*) ok "O: the hold names the issue and the corrupt ledger";; *) ko "O: no hold line for #724: $o";; esac
case "$calls" in *"landed without its child"*) ko "O: the generic text is still sent: '$calls'";; *) ok "O: the generic text is never sent";; esac
rm -rf "$T"; exit $fail
