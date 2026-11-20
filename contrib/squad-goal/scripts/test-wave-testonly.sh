#!/bin/bash
# seconds: 4.65
# Regression test for #2418: a test-only wave delivers its issue when the issue asks for a test change, and keeps it open
# otherwise. A wave that changes test files only used to close no issue (#533) whether or not the issue asked for a test
# change: the run landed, the issue stayed open in Todo, and the loop dispatched it again.
# case A: the issue's text names test paths and no code path. The landing closes the issue, with the landing fact naming
#         the sha the wave landed as.
# case B: the issue's text names a code path. The landing closes nothing, queues one comment that names the code the wave
#         does not change, and leaves the issue's dispatch marker in place, so the frontier does not dispatch it again in
#         the same cycle (sg_dispatch_claimed is the frontier's own read).
# case D: a batch landing takes the decision for each of its runs, in its own loop and over its own range.
# case E: one classification reads every path once; a path with whitespace stays whole.
# case F: a text that names two code paths gets one comment that names both, comma-joined, from a list built outside any pipe.
# case G: a Refs token that is no issue number reads no issue.
# usage: bash test-wave-testonly.sh [scripts dir]
# mirrors: wave.sh
unset REV REV_BASE REV_SHA SG_SERIAL_HELD SG_GATE_STATUS SG_GATE_KIND SERIAL_WAIT_SECONDS SERIAL_HOLD_SECONDS SERIAL_POLL_SECONDS
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; mkdir -p "$T/s" "$T/st/auto" "$T/st/cq" "$T/co" "$T/bin"
cp "$d/wave.sh" "$d/issue_ledger.py" "$d/ghread.py" "$d/wavepause.py" "$d/child_issues.py" "$d/address.sh" "$d/gate.sh" "$d/caps.sh" "$d/serial.sh" "$d/ghcall.sh" "$d/drainpolicy.py" "$d/base-battery.sh" "$d/basecheck.sh" "$d/startability.sh" "$d/skiplabels.sh" "$d/columnlimit.sh" "$d/issue_state.py" "$T/s/" 2>/dev/null
export SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR=$T/caps
{
  printf 'export SG_STATE=%s SG_REPO=x/y SG_BASE=dev SG_OWNER=o SG_PROJECT=3 SG_CHECKOUT=%s BOARD=%s/board.sh\n' "$T/st" "$T/co" "$T"
  printf 'export SG_LAND_REMOTE=%s/origin.git SG_WAVE_TIP_GATES=true SG_BOARD_VOCAB=shipped SG_FRONTIER_LABEL=frontier\n' "$T"
  printf '. %s/address.sh\n' "$T/s"
} > "$T/s/env.sh"
printf '#!/bin/bash\necho "$*" >> "%s/board.log"\necho "issue_state.py: items read 3; project total: none on the REST items route" >&2\n' "$T" > "$T/board.sh"; chmod +x "$T/board.sh"
# The gh stub: the door's one-issue read answers from the fixture written for that number, in the GraphQL shape the door asks
# and in the REST shape the route names, so the case proves the landing's own code whichever route the door takes. Every
# close answers `{}`. Every argv goes to gh.log, so a case reads the closes the landing made from it.
cat > "$T/answer.py" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
node = {"number": d["number"], "title": d["title"], "body": d["body"], "state": "OPEN",
        "stateReason": None, "createdAt": "2026-01-01T00:00:00Z",
        "repository": {"nameWithOwner": "x/y"}, "labels": {"nodes": []},
        "blockedBy": {"pageInfo": {"hasNextPage": False}, "nodes": []}, "parent": None,
        "subIssuesSummary": None, "subIssues": {"nodes": []}, "comments": {"nodes": []}}
print(json.dumps({"data": {"repository": {"i%d" % d["number"]: node}}}, separators=(",", ":")))
PY
cat > "$T/bin/gh" <<GH
#!/bin/bash
echo "\$*" >> "$T/gh.log"
case "\$*" in
  *"graphql"*"issue(number:"*) n=\$(printf '%s' "\$*" | sed -n 's/.*issue(number: \([0-9][0-9]*\)).*/\1/p')
    f="$T/issue-\$n.json"; if [ -f "\$f" ]; then python3 "$T/answer.py" "\$f"; else echo '{"data":{"repository":{}}}'; fi;;
  "-X PATCH repos/x/y/issues/"*) echo "{}";;
  *"repos/x/y/issues/"*) n=\$(printf '%s' "\$*" | sed -n 's#.*repos/x/y/issues/\([0-9][0-9]*\).*#\1#p' | head -1)
    f="$T/issue-\$n.json"; if [ -f "\$f" ]; then jq -c '{number,title,body,state:"open"}' "\$f"; else echo "{}"; fi;;
  *) echo "{}";;
esac
GH
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
$G init -q -b dev "$T/co"; (cd "$T/co" && echo x > f && $G add f && $G -c user.email=a@b -c user.name=t commit -q -m init)
$G clone -q --bare "$T/co" "$T/origin.git"
# closes: the issue numbers the stub gh was asked to close, one per line, in order.
closes() { sed -n 's#^api -X PATCH repos/x/y/issues/\([0-9]*\) -f state=closed.*#\1#p' "$T/gh.log" 2>/dev/null | tr '\n' ' '; }
# issuedoc <n> <title> <body>: the issue the landing reads through the door.
issuedoc() { jq -nc --arg t "$2" --arg b "$3" '{number:($n|tonumber),title:$t,body:$b,state:"open"}' --argjson n "$1" > "$T/issue-$1.json"; }
# comments <n>: the queued comment bodies for issue n.
comments() { cat "$T"/st/cq/*"#$1"-* 2>/dev/null; }
# mkwave <run> <issues> <message> <paths>: the run's folder with its dispatch request and one entry per issue, its wave branch
# from wave.sh start, then one commit on the wave with that message, changing those paths.
mkwave() { local run=$1 n=$2 msg=$3 paths=$4 rd i p
  rd="$T/co/runs/$run"; mkdir -p "$rd"; : > "$rd/record.jsonl"
  printf 'CHILD — work GitHub issue(s) %s: do it\n' "$n" > "$rd/request.txt"
  for i in ${n//,/ }; do echo "$run" > "$T/st/auto/disp-$i"; done
  (cd "$T/co" && bash "$T/s/wave.sh" start "$run") >/dev/null
  rm -rf "$T/c-$run"; $G clone -q "$T/origin.git" "$T/c-$run" 2>/dev/null
  (cd "$T/c-$run" && $G checkout -q -b w "origin/wave/$run") 2>/dev/null || return 1
  for p in $paths; do mkdir -p "$T/c-$run/$(dirname "$p")"; echo "$run" > "$T/c-$run/$p"; done
  (cd "$T/c-$run" && $G add -- $paths && $G -c user.email=a@b -c user.name=t commit -q -m "$msg") || return 1
  (cd "$T/c-$run" && $G push -q origin "HEAD:refs/heads/wave/$run") 2>/dev/null; }
land() { (cd "$T/co" && bash "$T/s/wave.sh" land "$1" 2>&1); }
wave_line() { printf '%s\n' "$1" | sed -n "/^WAVE wave\//p" | head -1; }
# landed_sha <n> <run>: the sha the issue's landing fact names for that run.
landed_sha() { SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" read "x/y#$1" landed 2>/dev/null | jq -r --arg r "$2" '.[$r].sha // "none"' 2>/dev/null; }
# claimed <n>: the frontier's own read of a dispatch marker (startability.sh sg_dispatch_claimed).
claimed() { SG_STATE="$T/st" bash -c '. "$1/startability.sh"; sg_dispatch_claimed "$2"' _ "$T/s" "$1"; }

# A: the issue asks for a test change. Its text names tests/test_a.py and no code path, so the test-only wave delivers it.
issuedoc 21 "Add the missing regression test" "The suite has no case for the empty wave. Add it to \`tests/test_a.py\`."
mkwave ra 21 "TDD RED: the empty wave test (Refs #21)" "tests/test_a.py"; : > "$T/gh.log"
r=$(land ra); rc=$?
[ $rc = 0 ] && ok "A: the landing exits 0" || ko "A: exit $rc: '$r'"
[ "$(closes)" = "21 " ] && ok "A: a test-only wave closes the issue that asks for a test change" || ko "A: closes '$(closes)': '$r'"
case "$(wave_line "$r")" in *"landed in dev (#21)"*) ok "A: the WAVE line names #21";; *) ko "A: the WAVE line: '$(wave_line "$r")'";; esac
sha=$($G -C "$T/origin.git" rev-parse dev 2>/dev/null)
[ -n "$sha" ] && [ "$(landed_sha 21 ra)" = "$sha" ] && ok "A: the landing fact names the landing sha" || ko "A: landed fact '$(landed_sha 21 ra)' against dev '$sha'"

# B: the issue asks for code. Its text names squad/parser.py, which the test-only wave does not change: the landing closes
# nothing, one comment names the missing code, and the dispatch marker stays so the frontier does not redispatch it now.
issuedoc 22 "The parser drops the last line" "The parser drops the last line of a file. Fix it in \`squad/parser.py\`; a test for it is welcome."
mkwave rb 22 "TDD RED: the last line test (Refs #22)" "tests/test_b.py"; : > "$T/gh.log"
r=$(land rb); rc=$?
[ $rc = 0 ] && ok "B: the landing exits 0" || ko "B: exit $rc: '$r'"
[ -z "$(closes)" ] && ok "B: a test-only wave closes no issue that asks for code" || ko "B: closes '$(closes)': '$r'"
c=$(comments 22); cn=$(find "$T/st/cq" -name "*#22-*" 2>/dev/null | wc -l | tr -d ' ')
[ "$cn" = 1 ] && ok "B: the issue gets exactly one comment" || ko "B: $cn comments for #22"
case "$c" in *"squad/parser.py"*) ok "B: the comment names the missing code path";; *) ko "B: the comment names no missing path: '$c'";; esac
case "$(wave_line "$r")" in *"no issue closed"*"test files only"*) ok "B: the WAVE line says why nothing closed";; *) ko "B: the WAVE line: '$(wave_line "$r")'";; esac
[ "$(SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" read "x/y#22" wave 2>/dev/null | jq -r '.rb.cause // ""' 2>/dev/null)" != "" ] \
  && ok "B: the ledger carries the cause for the run" || ko "B: no ledger cause for run rb"
claimed 22 && ok "B: the dispatch marker stays, so the frontier does not redispatch it in the same cycle" || ko "B: the dispatch marker is gone, so the frontier dispatches it again"

# C: the issue's text names a test path in a pattern the cases above do not carry, and a note whose extension is written in
# capitals. The one read that decides "asks for a test change" and the one that names the missing code both classify those
# two paths the same way: the note is no code, the test path is a test, so the landing closes the issue.
issuedoc 23 "Cover the empty wave end to end" "Add the case to \`tests/test_c.py\`; keep the note in \`docs/NOTES.MD\`."
mkwave rnn 23 "TDD RED: the empty wave test again (Refs #23)" "tests/test_c.py"; : > "$T/gh.log"
r=$(land rnn); rc=$?
[ $rc = 0 ] && ok "C: the landing exits 0" || ko "C: exit $rc: '$r'"
[ "$(closes)" = "23 " ] && ok "C: a new test-file pattern and a capitals note make the issue ask for a test change" \
  || ko "C: closes '$(closes)': '$r'"
case "$(comments 23)" in *"NOTES.MD"*) ko "C: a comment names the note as missing code: '$(comments 23)'";;
  *) ok "C: the capitals note is no missing code, so no comment names it";; esac

# D: a batch landing sees the test-only decision of each of its runs. Two waves land in one batch: one changes code, and one
# changes a test file only while its issue asks for code. The batch closes the code issue, and records on the test-only issue
# why it stays open, as the one-run landing does.
issuedoc 24 "The parser drops the last line" "Fix it in \`squad/parser.py\`."
issuedoc 25 "The wave note drops the issue" "The suite has the case in \`tests/test_wave_note.py\`; the fix goes in \`squad/wave.py\`."
mkwave rn1 25 "TDD RED: the wave note test (Refs #25)" "tests/test_wave_note.py"
mkwave rn2 24 "work 24 (Refs #24)" "squad/parser.py"; : > "$T/gh.log"
printf '%s\n' "$T/co/runs/rn1" > "$T/st/auto/wland-rn1.queued"
r=$(cd "$T/co" && SG_LAND_QUEUE=1 bash "$T/s/wave.sh" land rn2 "$T/co/runs/rn2" 2>&1); rc=$?
[ $rc = 0 ] && ok "D: the batch landing exits 0" || ko "D: exit $rc: '$r'"
case "$r" in *"WAVE BATCH"*) ok "D: the landing is a batch of two waves";; *) ko "D: no batch line: '$r'";; esac
[ "$(closes)" = "24 " ] && ok "D: the batch closes the issue of the wave that changed code" || ko "D: closes '$(closes)': '$r'"
case "$(comments 24)" in *"does not change"*) ko "D: a comment names a missing path on the code issue: '$(comments 24)'";;
  *) ok "D: no comment names a missing path on the code issue";; esac
c=$(comments 25)
case "$c" in *"squad/wave.py"*) ok "D: the batch records on the test-only issue the code it does not change";; *) ko "D: the comment on #25: '$c'";; esac
[ "$(SG_STATE="$T/st" python3 "$T/s/issue_ledger.py" read "x/y#25" wave 2>/dev/null | jq -r '.rn1.cause // ""' 2>/dev/null)" != "" ] \
  && ok "D: the ledger carries the cause of the batch's test-only run" || ko "D: no ledger cause for run rn1"
# The books of a claimed run run inside the batch's own loop, never in a single landing after it: the batch prefixes the
# lines of every run it lands, the last one included.
case "$r" in *"BATCH rn1: WAVE wave/rn1 "*) ok "D: the test-only run took its books in the batch loop";;
  *) ko "D: the books of rn1 are not in the batch loop: '$r'";; esac

# E: the test-only decision reads a path as one path, never as the shell's words. One classification feeds both decisions,
# and a path with whitespace stays whole, in the code list and in the boolean alike.
# A case sources wave.sh under a $0 inside $T/s (the tipcheck form), so wave.sh reads the fixture's env.sh and never the
# real one, which exits where no profile resolves.
inwave() { T="$T" bash -c '. "$1/wave.sh"; . "$2"' "$T/s/probe.sh" "$T/s" "$1" 2>/dev/null; }
cat > "$T/e.sh" <<'EOF'
paths_of() { printf '%s\n' "docs/a note.MD" "squad/with space.py"; }
echo "cls=$(paths_classified body | tr '\n' '|')"
echo "notest=$(paths_nontest body | tr '\n' '|')"
echo "nonote=$(paths_nonnote body | tr '\n' '|')"
EOF
e=$(inwave "$T/e.sh")
case "$e" in *"cls=C squad/with space.py|"*) ok "E: a path with whitespace is one code path";; *) ko "E: the classification: '$e'";; esac
case "$e" in *"nonote=squad/with space.py|"*) ok "E: the whitespace path survives the non-note read";; *) ko "E: the non-note read: '$e'";; esac
case "$e" in *"notest=squad/with space.py|"*) ok "E: the whitespace path reads as code whole";; *) ko "E: the code read: '$e'";; esac

# F: an issue whose text names two code paths gets one comment that names both, each intact and joined by a comma. The list
# is built once, outside any pipe, so no element is lost.
issuedoc 26 "The wave note drops two files" "The fix goes in \`squad/a.py\` and \`squad/b.py\`."
mkwave rn3 26 "TDD RED: the wave note test twice (Refs #26)" "tests/test_wave_note2.py"; : > "$T/gh.log"
r=$(land rn3); rc=$?
[ $rc = 0 ] && ok "F: the landing exits 0" || ko "F: exit $rc: '$r'"
case "$(comments 26)" in *"squad/a.py, squad/b.py"*) ok "F: the comment names both code paths, comma-joined";;
  *) ko "F: the comment on #26: '$(comments 26)'";; esac

# G: the one Refs token is read as an issue number or not at all. A token that is no number reads no issue, so a malformed
# Refs line never turns into a read of some other issue.
cat > "$T/g.sh" <<'EOF'
issue_text() { echo CALLED >> "$T/g.log"; echo "a test at tests/x.py"; }
: > "$T/g.log"
issues=""; amb=""; nontest=""; rfs="x"; testonly_read a b
echo "miss=[$testmiss]"
issues=""; amb=""; nontest=""; rfs="26"; w=wave/x; testonly_read a b
echo "calls=$(wc -l < "$T/g.log" | tr -d ' ')"
EOF
g=$(inwave "$T/g.sh")
case "$g" in *"miss=[]"*) ok "G: a token that is no issue number takes no decision";; *) ko "G: the token was read: '$g'";; esac
case "$g" in *"calls=1"*) ok "G: the one numeric token is the only one read";; *) ko "G: the read count: '$g'";; esac

[ $fail = 0 ] && echo "PASS test-wave-testonly" || echo "FAIL test-wave-testonly"
exit $fail
