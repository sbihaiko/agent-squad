#!/bin/bash
# seconds: 6.64
# Regression test for mkreq.sh's dispatch-time slice gate (ADR-0274, Refs #1400, #1519). usage: bash test-mkreq.sh
# Offline: HOME is a throwaway directory, and the only holder of a file is a queued request (#361: the loop reads no pull request).
# The report is buffered so the first line says TDD RED while a fix is missing, TDD GREEN once it is in.
d=$(dirname "$0"); T=$(mktemp -d); log=$T/report; : > "$log"; fail=0
# Hermetic (#1410, the rule of shell-suites.sh): every inherited SG_* is cleared, so the live loop's fork, repo and state can
# never reach an assertion -- SG_FORK_DIR above all, which selects the fork checkout the guard reads the gui/* branches from.
unset $(compgen -e | /usr/bin/grep '^SG_') 2>/dev/null || true
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
S=$T/scripts; mkdir -p "$S" "$T/bin" "$T/home"
cp "$d/mkreq.sh" "$d/address.sh" "$d/models.json" "$d/req-checklist.txt" "$d/modelreg.py" "$d/registry.json" "$S/"   # address.sh: the seam mkreq.sh resolves its state directory through, as env.sh sources it
printf 'export SG_REPO=o/r SG_CHECKOUT=%s\n' "$T/co" > "$S/env.sh"
export HOME=$T/home; R=$HOME/.cache/squad-goal/req
body() { printf 'IMPORTANT: your first and only action is X\nPANEL: %s\nEdit %s\n' "$1" "$2" > "$T/$3.body"; }
run() { bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }
runf() { SG_SLICE_FORCE=1 bash "$S/mkreq.sh" "$1" "$T/$1.body" 2>"$T/$1.err" >"$T/$1.out"; }
# 1. a same-panel overlap refuses, names both requests and the shared path, and writes no request
body p1 scripts/a.py a; body p1 scripts/a.py b
run a || ko "first sibling refused"; [ -f "$R/a.txt" ] && ok "first sibling builds" || ko "no request for a"
run b && ko "same-panel overlap launched" || ok "same-panel overlap refuses"
/usr/bin/grep -q 'request b and request a' "$T/b.err" && /usr/bin/grep -q 'scripts/a.py' "$T/b.err" && ok "refusal names both requests and the path" || ko "refusal text: $(cat "$T/b.err")"
[ -f "$R/b.txt" ] && ko "a refused request left a file" || ok "refused request wrote no file"
# 2. a disjoint panel launches
body p2 scripts/c.py c; run c && ok "disjoint panel launches" || ko "disjoint refused: $(cat "$T/c.err")"
# 3. a body naming a file a queued request holds refuses, not warns (#1519): it names the holder and the path, names the
# override, and writes nothing. It is the holder the later cases (16, 17b, 17c) contest, so request e stays.
body p3 scripts/shared.py e; run e && [ -f "$R/e.txt" ] && ok "the holder request builds" || ko "holder request: $(cat "$T/e.err")"
body p3b scripts/shared.py e2; run e2 && ko "an overlap with a queued request launched" || ok "an overlap with a queued request refuses"
/usr/bin/grep -q 'request e' "$T/e2.err" && /usr/bin/grep -q 'scripts/shared.py' "$T/e2.err" && ok "the refusal names the holder and the path" || ko "refusal text: $(cat "$T/e2.err")"
/usr/bin/grep -q 'SG_SLICE_FORCE=1' "$T/e2.err" && ok "refusal names the SG_SLICE_FORCE=1 override" || ko "refusal hides the override: $(cat "$T/e2.err")"
[ -f "$R/e2.txt" ] && ko "the refusal left a file" || ok "the refusal wrote no file"
runf e2 && [ -f "$R/e2.txt" ] && ok "SG_SLICE_FORCE=1 dispatches the same body" || ko "override refused: $(cat "$T/e2.err")"
/usr/bin/grep -q 'SLICE FORCE' "$T/e2.err" && ok "a forced overlap is announced on stderr" || ko "forced overlap silent: $(cat "$T/e2.err")"
rm -f "$R/e2.txt"
# 4. a queued request of ANOTHER panel refuses too: the rule is not panel-scoped (ADR-0274 is its narrower half)
body p4 scripts/a.py f; run f && ko "another panel's pending request launched" || ok "a pending request of another panel refuses"
/usr/bin/grep -q 'request f and request a' "$T/f.err" && ok "cross-panel refusal names the holder request" || ko "cross-panel text: $(cat "$T/f.err")"
[ -f "$R/f.txt" ] && ko "cross-panel refusal left a file" || ok "cross-panel refusal wrote no file"
# 6b. (#81) the request names what the child RUNS: the suite beside the script it touched and the targeted cases, and the
# machine's full gate as the loop's, once per landing -- a child that runs the full gate too starts a second one on the same box.
G=/usr/bin/grep
for w in "SUITES: run what you touched" "test-<script>.sh" "pytest tests/<file>.py" "belongs to the loop and to the land pass, once per landing: never run it"; do
  $G -qF -- "$w" "$R/c.txt" && ok "PR request carries '$w'" || ko "PR request lacks '$w'"
done
# 8. the request of a PR-bound child carries the literal `Refs #<N>` line it has to paste into the PR body (Refs #1507):
# a PR with no such line is skipped by the autopilot, so the child has to be handed the line, not only the rule.
body p7 scripts/z.py g; run g && [ -f "$R/g.txt" ] && ok "PR request builds" || ko "PR request: $(cat "$T/g.err" 2>/dev/null)"
$G -qF -- 'Refs #<N>' "$R/g.txt" && ok "PR request carries the literal Refs #<N> line" || ko "PR request lacks the literal Refs #<N> line"
# 9. (#1529) a request whose work is over is history, not in flight. Request files are never deleted, so reading every one of
# them as in flight made the guard refuse forever, naming runs that ended days ago (auto-fix-1203-3fbfa13ca, PR #1203 closed
# 2026-10-10): the in-flight set only grew. The run folder is what says whether a request's work is still going -- launch.py
# copies the request into runs/<run>/request.txt and record.jsonl's verdict ends it.
mkdir -p "$T/co/runs" "$T/home/.cache/squad-goal"
fin() { printf 'IMPORTANT: your first and only action is X\nPANEL: p9\nEdit %s\n' "$1" > "$R/$2.txt"; touch -t 202610010000 "$R/$2.txt"; }
# 9a. an old request whose run recorded a verdict is history: it must not refuse a slice naming the same file
fin scripts/gone.py dead-req; mkdir -p "$T/co/runs/run-9001"
cp "$R/dead-req.txt" "$T/co/runs/run-9001/request.txt"; echo '{"event": "verdict"}' > "$T/co/runs/run-9001/record.jsonl"
echo "$T/co/runs/run-9001" >> "$T/home/.cache/squad-goal/active-runs.txt"
body p9 scripts/gone.py m
run m && [ -f "$R/m.txt" ] && ok "a finished run's request does not refuse" || ko "finished run's request still refuses: $(cat "$T/m.err")"
# 9b. a request no run ever carried is in flight only inside the launch window, then it is history too
fin scripts/abandoned.py lost-req
body p9 scripts/abandoned.py o
run o && [ -f "$R/o.txt" ] && ok "an old request with no run behind it does not refuse" || ko "abandoned request still refuses: $(cat "$T/o.err")"
# 9c. the accepted half of the same rule: the run is live (no verdict yet), so a slice naming its file still refuses
fin scripts/live.py live-req; mkdir -p "$T/co/runs/run-9002"
cp "$R/live-req.txt" "$T/co/runs/run-9002/request.txt"; echo '{"event": "spawn_accepted"}' > "$T/co/runs/run-9002/record.jsonl"
echo "$T/co/runs/run-9002" >> "$T/home/.cache/squad-goal/active-runs.txt"
printf 'IMPORTANT: your first and only action is X\nEdit scripts/live.py\n' > "$T/n.body"
run n && ko "a live run's request launched" || ok "a live run's request still refuses"
$G -q 'request live-req' "$T/n.err" && $G -q 'scripts/live.py' "$T/n.err" && ok "the refusal names the live request and the path" || ko "live refusal text: $(cat "$T/n.err")"
[ -f "$R/n.txt" ] && ko "the live refusal left a file" || ok "the live refusal wrote no file"
# 9d. a run that recorded a verdict seconds ago is history at once: age alone must not decide it (the request file is fresh)
printf 'IMPORTANT: your first and only action is X\nPANEL: p9\nEdit scripts/justdone.py\n' > "$R/just-req.txt"; mkdir -p "$T/co/runs/run-9003"
cp "$R/just-req.txt" "$T/co/runs/run-9003/request.txt"; echo '{"event": "verdict"}' > "$T/co/runs/run-9003/record.jsonl"
echo "$T/co/runs/run-9003" >> "$T/home/.cache/squad-goal/active-runs.txt"
body p9 scripts/justdone.py p
run p && [ -f "$R/p.txt" ] && ok "a verdict makes the request history at once" || ko "fresh request of a finished run refuses: $(cat "$T/p.err")"
# 11. (#1774) a path named only inside quoted or code context is not a slice. A frontier/autobug request embeds the full text of
# every issue it dispatches, so a path quoted as evidence counted as a file the request would edit: issue #1769's body indents
# `refusing: request auto-fix-1706-27aaaf5b5 names scripts/checks/verify_report_bug_prefix.sh` as a code block, PR #1706 was
# open on exactly that file, and every frontier and autobug pass embedding that body was refused -- on 2026-10-11 the whole
# wave:01 panel (#1765-#1770) could not dispatch. Only prose counts: fenced blocks, lines indented by 4+ spaces and blockquote
# lines are dropped before matching. Each claimed path below is named ONLY inside one of those three contexts.
cat > "$T/quo.body" <<'B'
IMPORTANT: your first and only action is X
Edit scripts/quoted-free.py

--- issue #9001
Evidence from an earlier pass:

    refusing: request issue-prose names scripts/shared.py, which a queued request already names.

> refusing: request frontier-x names contrib/squad-goal/scripts/hub.py, which the unmerged fork branch gui/1497 touches.

```
refusing: request autobug-y names dashboard/page.py, which the unmerged fork branch gui/1498 touches.
```
B
run quo && [ -f "$R/quo.txt" ] && ok "a path only quoted as evidence does not claim a slice" || ko "quoted prose refused the request: $(cat "$T/quo.err")"
$G -q 'refusing' "$T/quo.err" && ko "the quoted-context body was still refused: $(cat "$T/quo.err")" || ok "the quoted-context body prints no refusal"
$G -qF -- 'scripts/quoted-free.py' "$R/quo.txt" && ok "the prose path of the same body is still the slice" || ko "the accepted body lost its prose path"
# 11b. the prose scan is intact: the same three paths named at column 0 refuse exactly as before (test 5 and 5b are that case)
# 11c. (#1790) prose inside an EMBEDDED ISSUE is not a slice either. Every automatic request embeds the full text of the issues
# it dispatches under a `--- issue #N` marker line, so a path named in the plain prose of a quoted body counted as a file the
# request would edit: the pass that dispatched it claimed every path its issues' prose named, the next pass embedding the same
# issues was refused, and the two passes took turns failing while the issue stayed open. The slice a request claims is what its
# OWN instructions name, so the scan stops at the first marker line: the quoted region is what the body tells the child about
# its ticket, not what it asks it to edit.
cat > "$T/issue-prose.body" <<'B'
IMPORTANT: your first and only action is X
PANEL: p11
Edit scripts/issue-prose-free.py

--- issue #9002
An earlier pass was refused because a queued request already names scripts/shared.py, and a sibling had
claimed contrib/squad-goal/scripts/hub.py while gui/1497 touched dashboard/page.py.
B
run issue-prose && [ -f "$R/issue-prose.txt" ] && ok "a path in an embedded issue's prose does not claim a slice" || ko "embedded-issue prose refused the request: $(cat "$T/issue-prose.err")"
$G -q 'refusing' "$T/issue-prose.err" && ko "the embedded-issue prose body was still refused: $(cat "$T/issue-prose.err")" || ok "the embedded-issue prose body prints no refusal"
$G -qF -- 'scripts/issue-prose-free.py' "$R/issue-prose.txt" && ok "the instruction path of the same body is still the slice" || ko "the accepted body lost its instruction path"
# 11d. the instruction part still claims its slice: the same path named ABOVE the first marker refuses exactly as before
cat > "$T/issue-own.body" <<'B'
IMPORTANT: your first and only action is X
PANEL: p11
Edit scripts/shared.py

--- issue #9002
Fix the bug the issue describes.
B
run issue-own && ko "an instruction path already in flight launched" || ok "a path the instructions name still refuses"
$G -q 'request e' "$T/issue-own.err" && $G -q 'scripts/shared.py' "$T/issue-own.err" && ok "the refusal names the holder and the instruction path" || ko "instruction-path refusal text: $(cat "$T/issue-own.err")"
[ -f "$R/issue-own.txt" ] && ko "the instruction-path refusal left a file" || ok "the instruction-path refusal wrote no file"
# 14. (#1799) an ac_command is a proof that passes at the FINAL HEAD, never a command that writes to the worktree. A child that
# follows the TDD mandate can put its RED demonstration into ac_commands, and the final gate then runs it verbatim. The rejected
# command of run-20261011-155130-1864ee (fork-only, issues #1783/#1786/#1798; verdict failed, reasons ["ac-verify:ac_failed"],
# landed true) was
#   cd ~/wt-as-1783 && git checkout local/next -- contrib/squad-goal/scripts/hub.py && python3 -m pytest tests/test_hub_test_pass_lost.py -q -p no:deepeval
# and its `git checkout local/next -- <file>` IS the RED step: it restores the pre-fix file inside the child's worktree, which is
# what makes the test fail there. Run at the final head it reverts the child's own fix, so the gate fails by construction and the
# committed work is thrown away with it -- indistinguishable on the board from a real test failure. The DONE CHECK of BOTH
# request kinds has to say where the RED demonstration belongs instead. The fixture below keeps the shape and takes a free path:
# the issue's own contrib/squad-goal/scripts/hub.py is held by gui/1497 in this fork.
REDCMD='cd ~/wt-as-1783 && git checkout local/next -- scripts/red-demo.py && python3 -m pytest tests/test_red.py -q -p no:deepeval'
printf 'IMPORTANT: your first and only action is X\nEdit scripts/red-demo.py\n%s\n' "$REDCMD" > "$T/red.body"
run red && [ -f "$R/red.txt" ] && ok "a body carrying a RED-style ac_command builds" || ko "RED-style body refused: $(cat "$T/red.err")"
# 14a. the child DONE CHECK forbids the tree-mutating ac_command and sends the RED demonstration to the issue status comment
for w in 'never an ac_command that writes to the worktree' 'no `git checkout <other-ref> -- <file>`' 'no `git stash`' 'never in ac_commands' 'belongs in the issue status comment' 'not a gate input'; do
  $G -qF -- "$w" "$R/red.txt" && ok "PR DONE CHECK states '$w'" || ko "PR DONE CHECK lacks '$w'"
done
# 14b. (#496) a wave child commits in its own worktree and never pushes, and the final gate runs in the run's target where
# its work is landed (ADR-0106): the DONE CHECK names one shape, a plain command, and no retired route (acpr.sh fetches
# origin/<branch>, which never exists; the fork worktree is gone).
for w in 'acpr.sh' 'fork worktree'; do
  $G -qF -- "$w" "$R/red.txt" && ko "DONE CHECK still names the retired shape '$w'" || ok "DONE CHECK names no '$w'"
done
$G -qF -- "in the run's target" "$R/red.txt" && ok "DONE CHECK says the gate runs in the run's target" || ko "DONE CHECK does not say where the gate runs"
# 15. (#44) a path a request only RUNS is not a slice it edits. Every frontier request carries the preamble line
# `bash contrib/squad-goal/scripts/shell-suites.sh` -- the suite the child must run -- and a fork fix request names the same
# command; the scan read both as a claim on that file, so the two requests refused each other. On 2026-10-12 forkred-10 held
# contrib/squad-goal/scripts/shell-suites.sh against every frontier pass: `refusing: request frontier-20261012-211002 and
# request forkred-10-25f2a596b both name: contrib/squad-goal/scripts/shell-suites.sh`, for twelve passes in a row, and the six
# ready tickets in that batch were never dispatched against ten free run slots. The preamble is shared by EVERY ticket in the
# batch, so the held path contests the whole frontier, which is why nothing was dispatched at all.
bodycmd() { printf 'IMPORTANT: your first and only action is X\nPANEL: %s\nEdit %s\nRun `bash contrib/squad-goal/scripts/shell-suites.sh && uv run --extra dev pytest tests -q -p no:deepeval` there.\n' "$1" "$2" > "$T/$3.body"; }
bodycmd p15a scripts/one.py cmd1; bodycmd p15b scripts/two.py cmd2
run cmd1 || ko "a request naming the file it edits was refused: $(cat "$T/cmd1.err")"
run cmd2 || ko "a request whose only shared path is a command it runs was refused: $(cat "$T/cmd2.err")"
$G -q 'shell-suites.sh' "$T/cmd2.err" && ko "the command path staged a refusal" || ok "a path inside a command is not a slice"
# 15b. the accepted half: naming the same file as work still refuses. The rule is the command span, not an exemption for the path
body p15a contrib/squad-goal/scripts/shell-suites.sh cmd3; body p15b contrib/squad-goal/scripts/shell-suites.sh cmd4
run cmd3 && ok "a plain claim of that path launches" || ko "plain claim refused: $(cat "$T/cmd3.err")"
run cmd4 && ko "a second plain claim of the same path launched" || ok "a plain claim of the same path still refuses"
$G -qF -- 'contrib/squad-goal/scripts/shell-suites.sh' "$T/cmd4.err" && ok "and the refusal still names the path" || ko "refusal text: $(cat "$T/cmd4.err")"
# 16. (#10) A refusal reports every contested file and its holder when SG_SLICE_REPORT names a file.
printf 'IMPORTANT: your first and only action is X\nEdit scripts/free-10.py\nEdit scripts/shared.py\n' > "$T/rep.body"
SG_SLICE_REPORT=$T/rep.report run rep && ko "a body naming a contested file launched" || ok "a body naming a contested file refuses"
printf 'scripts/shared.py\trequest e\n' > "$T/rep.want"
cmp -s "$T/rep.want" "$T/rep.report" && ok "the report lists the contested file with its holder" || ko "report: $(cat "$T/rep.report" 2>&1)"
[ "$($G -c '^refusing:' "$T/rep.err")" = 1 ] && $G -q 'scripts/shared.py' "$T/rep.err" && ok "the refusal is one line, naming the contested file" || ko "refusal text: $(cat "$T/rep.err")"
[ -f "$R/rep.txt" ] && ko "a reported refusal left a file" || ok "a reported refusal wrote no file"
# 16b. a clean body reports nothing, and a stale report never survives: the file is emptied before the guard reads anything
printf 'stale\n' > "$T/clean.report"; printf 'IMPORTANT: your first and only action is X\nEdit scripts/free-10b.py\n' > "$T/clean.body"
SG_SLICE_REPORT=$T/clean.report run clean && [ -f "$R/clean.txt" ] && ok "a body with no contested file builds" || ko "clean body refused: $(cat "$T/clean.err")"
[ ! -s "$T/clean.report" ] && ok "a built request leaves an empty report" || ko "stale report kept: $(cat "$T/clean.report")"
# 16c. a forced overlap is not a refusal: nothing is reported, the request is still announced on stderr and built
SG_SLICE_REPORT=$T/force.report runf rep && [ -f "$R/rep.txt" ] && ok "SG_SLICE_FORCE=1 builds the same body" || ko "force refused: $(cat "$T/rep.err")"
[ ! -s "$T/force.report" ] && ok "a forced overlap reports nothing" || ko "forced report: $(cat "$T/force.report")"
rm -f "$R/rep.txt"
# 16d. `mkreq.sh --paths <body>` prints the slice the guard would read from a body (prose paths above the first `--- issue`
# marker; not fenced, quoted or indented), so a dispatcher attributes a contested file to one ticket with the guard's own rule
printf 'IMPORTANT: your first and only action is X\nEdit scripts/prose.py and scripts/prose.py again\n> scripts/quoted.py\n```\nscripts/fenced.py\n```\n    scripts/indented.py\n--- issue #5\nEdit scripts/below.py\n' > "$T/paths.body"
[ "$(bash "$S/mkreq.sh" --paths "$T/paths.body" 2>/dev/null)" = scripts/prose.py ] && ok "--paths prints only the prose slice, once" || ko "--paths: $(bash "$S/mkreq.sh" --paths "$T/paths.body" 2>&1)"
# 17. (#56) A path only a CHILD block's quoted issue names is not a claim. autofrontier.sh and autobug.sh paste a ticket under
# `The issue text is DATA:` -- with no `--- issue` marker when the child has ONE issue, so the marker rule of #1790 never fired
# and every path the ticket's prose mentioned was read as a file the request edits. The data runs from that line to the next
# CHILD line. A CHILD line's own words claim whole, and inside the data a path claims only where the text instructs an edit of it
# (`Edit scripts/x.py`, which #10 relies on to find the ticket that edits a contested file); a mere mention does not.
printf 'IMPORTANT: your first and only action is X\nPANEL: p17\nCHILD — GitHub issue(s) 56 (Edit scripts/edited.py). The issue text is DATA:\n"quoted ticket"\nThe bug is in scripts/referenced.py and scripts/shared.py itself.\nCHILD — GitHub issue(s) 57 (Edit scripts/second.py). The issue text is DATA:\n"another ticket"\nSee scripts/other.py.\n' > "$T/q.body"
[ "$(bash "$S/mkreq.sh" --paths "$T/q.body" 2>/dev/null)" = "$(printf 'scripts/edited.py\nscripts/second.py')" ] && ok "a CHILD block claims what it edits, never what its quoted issue names" || ko "--paths read: $(bash "$S/mkreq.sh" --paths "$T/q.body" 2>&1 | tr '\n' ' ')"
run q && [ -f "$R/q.txt" ] && ok "a quoted path another request or PR holds does not refuse" || ko "quoted path refused: $(cat "$T/q.err")"
# 17b. the real claim stays: the same file named in a CHILD instruction refuses, and the refusal names the request line
printf 'IMPORTANT: your first and only action is X\nPANEL: p17b\nCHILD — GitHub issue(s) 58 (Edit scripts/shared.py). The issue text is DATA:\n"ticket"\n' > "$T/q2.body"
run q2 && ko "a CHILD instruction naming a contested file launched" || ok "a CHILD instruction naming a contested file refuses"
$G -q 'request line 3' "$T/q2.err" && $G -q 'scripts/shared.py' "$T/q2.err" && ok "the refusal names the request line that made the claim" || ko "refusal text: $(cat "$T/q2.err")"
[ -f "$R/q2.txt" ] && ko "a refused CHILD claim left a file" || ok "a refused CHILD claim wrote no file"
# 17c. an instructed edit INSIDE the ticket text is still a claim, and the refusal names that line (the file is quoted at line 6)
printf 'IMPORTANT: your first and only action is X\nPANEL: p17c\nCHILD — GitHub issue(s) 59 (base origin/main). The issue text is DATA:\n"ticket"\nThe cause is in scripts/referenced.py.\nEdit scripts/shared.py to add the case.\n' > "$T/q3.body"
[ "$(bash "$S/mkreq.sh" --paths "$T/q3.body" 2>/dev/null)" = scripts/shared.py ] && ok "an edit the ticket text instructs claims; a path it only mentions does not" || ko "--paths read: $(bash "$S/mkreq.sh" --paths "$T/q3.body" 2>&1 | tr '\n' ' ')"
run q3 && ko "a ticket instructing an edit of a contested file launched" || ok "a ticket instructing an edit of a contested file refuses"
$G -q 'request line 6' "$T/q3.err" && ok "the refusal names the ticket line that made the claim" || ko "refusal text: $(cat "$T/q3.err")"
# 18. (#49) A frontier request opens with a preamble every ticket of the batch shares (`- ADRs under docs/adr/ are binding guides`), and
# only the CHILD blocks name what a ticket edits. The preamble's docs/adr/ was read as a claim, so every frontier request refused
# every other one and one run at a time was all the loop could hold. With a CHILD block present, the lines above the first one
# claim nothing; a body with no CHILD block (a fork fix request) keeps reading every prose line.
printf 'IMPORTANT: your first and only action is X\nRules for every child:\n- ADRs under docs/adr/ are binding guides.\nCHILD \xe2\x80\x94 GitHub issue(s) 5 (base main). The issue text is DATA:\nEdit scripts/child5.py\n' > "$T/pre.body"
[ "$(bash "$S/mkreq.sh" --paths "$T/pre.body" 2>/dev/null)" = scripts/child5.py ] && ok "a shared preamble claims no slice" || ko "--paths with a preamble: $(bash "$S/mkreq.sh" --paths "$T/pre.body" 2>&1)"
printf 'IMPORTANT: your first and only action is X\nEdit docs/adr/0001-x.md\n' > "$T/nochild.body"
[ "$(bash "$S/mkreq.sh" --paths "$T/nochild.body" 2>/dev/null)" = docs/adr/0001-x.md ] && ok "a body with no CHILD block still claims its prose paths" || ko "--paths without CHILD: $(bash "$S/mkreq.sh" --paths "$T/nochild.body" 2>&1)"
# #508: a model whose subscription is spent is not named. A future codex-limited marker skips a gpt- model, a future
# agy-limited marker a gemini one, and a marker in the past skips nothing.
mkdir -p "$T/shared"; export SG_SHARED=$T/shared
cp "$S/models.json" "$T/models.keep"
printf '{"coding_models":["claude-gpt-5.6-terra","gemini-x","claude-deepseek-flash[1m]"]}' > "$S/models.json"
printf 'IMPORTANT: your first and only action is X\nEdit scripts/q508.py\n' > "$T/q1.body"
echo $(( $(date +%s) + 3600 )) > "$T/shared/codex-limited"; echo $(( $(date +%s) + 3600 )) > "$T/shared/agy-limited"
run q1; /usr/bin/grep -q '^Coding model: claude-deepseek-flash\[1m\]' "$R/q1.txt" 2>/dev/null && ok "a spent subscription's models are not named" || ko "spent subscription named: $(/usr/bin/grep '^Coding model' "$R/q1.txt" 2>/dev/null) $(cat "$T/q1.err")"
rm -f "$R/q1.txt"; echo $(( $(date +%s) - 60 )) > "$T/shared/codex-limited"
run q1; /usr/bin/grep -q '^Coding model: claude-gpt-5.6-terra' "$R/q1.txt" 2>/dev/null && ok "an expired quota marker skips nothing" || ko "expired marker still skips: $(/usr/bin/grep '^Coding model' "$R/q1.txt" 2>/dev/null)"
cp "$T/models.keep" "$S/models.json"; rm -f "$R/q1.txt"
# v2 (ADR-0147): a chain is a list of registry ids; the request names the raw spelling of the first entry that may run.
# The repo file's first entry keeps an hours window, so the case reads which entry may run now through the shipped resolver
# instead of naming one: a night run and a day run start on different entries, and a hardcoded id made the suite red by day.
first_coding=$(cd "$S" && python3 -c 'import time, modelreg
e = modelreg.chain("models.json", "coding").entries
h = time.localtime().tm_hour
print(next((x.raw for x in e if modelreg.eligible(x, hour=h).ok), e[0].raw))')
printf 'IMPORTANT: your first and only action is X\nEdit scripts/v2repo.py\n' > "$T/v2repo.body"
run v2repo; /usr/bin/grep -qF -- "Coding model: $first_coding" "$R/v2repo.txt" 2>/dev/null && ok "the repo models.json (v2) names its first coding model" || ko "v2 repo file names: $(/usr/bin/grep '^Coding model' "$R/v2repo.txt" 2>/dev/null) $(cat "$T/v2repo.err")"
rm -f "$R/v2repo.txt"
printf '{"schema_version":2,"chains":{"coding":[{"id":"gpt-5.6-terra","raw":"claude-gpt-5.6-terra","route":"gateway","on":false},{"id":"grok-4.7","raw":"claude-grok-4.7","route":"gateway"}]}}' > "$S/models.json"
printf 'IMPORTANT: your first and only action is X\nEdit scripts/v2off.py\n' > "$T/v2off.body"
run v2off; /usr/bin/grep -q '^Coding model: claude-grok-4.7' "$R/v2off.txt" 2>/dev/null && ok "a v2 entry switched off is skipped" || ko "v2 off entry named: $(/usr/bin/grep '^Coding model' "$R/v2off.txt" 2>/dev/null) $(cat "$T/v2off.err")"
rm -f "$R/v2off.txt"; cp "$T/models.keep" "$S/models.json"
# (#658) every request, the coding child's and the fix run's, forbids a write outside the worktree and the live skill copy.
printf 'IMPORTANT: your first and only action is X\nEdit scripts/q658.py\n' > "$T/w658.body"
run w658; /usr/bin/grep -qF 'Never write outside your worktree; never run publish-live.sh and never touch ~/.claude/skills' "$R/w658.txt" 2>/dev/null \
  && ok "the request forbids writes outside the worktree and the live skill copy" || ko "no live-copy line in the request: $(cat "$T/w658.err")"
rm -f "$R/w658.txt"
# 19. (#740) The gate commands a request names come from the profile. A child of wz-zesty-agent was told to run
# contrib/squad-goal/scripts/fast-gate.sh and prepush.sh, which that repository does not hold, so it could not satisfy the
# request and ended `failed` (missing-gate-scripts) with correct work. The profile is sourced as env.sh sources it, and
# sg_gate_cmds resolves the commands the profile left unset against the target checkout.
gate() { ( . "$S/address.sh"; SG_CHECKOUT=$1; shift; for kv in "$@"; do export "$kv"; done; sg_gate_cmds 2>/dev/null
  bash "$S/mkreq.sh" "$name" "$T/$name.body" ) >/dev/null 2>"$T/$name.err"; $G '^SUITES:' "$R/$name.txt" 2>/dev/null; }
mkdir -p "$T/zco" "$T/aco/contrib/squad-goal/scripts"   # zco holds no gate script; aco holds agent-squad's
touch "$T/aco/contrib/squad-goal/scripts/"{fast-gate,prepush,shell-suites}.sh
# 19a. a profile that defines no gate command, on a checkout without the scripts: the request names no script, says no fast
# gate is configured, and says a missing gate is not a failure
name=z740a; printf 'IMPORTANT: your first and only action is X\nEdit scripts/z740a.py\n' > "$T/$name.body"
sl=$(gate "$T/zco")
[ -n "$sl" ] && ok "19a the request still carries one SUITES line" || ko "19a no SUITES line: $(cat "$T/$name.err")"
for w in contrib/squad-goal/ fast-gate.sh prepush.sh shell-suites.sh 'test-<script>.sh' 'uv run --extra dev'; do
  case "$sl" in *"$w"*) ko "19a a profile with no gate still names '$w': $sl";; *) ok "19a a profile with no gate names no '$w'";; esac
done
case "$sl" in *"No fast gate is configured"*) ok "19a the request says no fast gate is configured";; *) ko "19a the request does not say no fast gate is configured: $sl";; esac
case "$sl" in *"not a failure"*) ok "19a the request says a missing gate is not a failure";; *) ko "19a the request does not say a missing gate is not a failure: $sl";; esac
# 19b. a profile that defines its commands: the request names exactly those
name=z740b; printf 'IMPORTANT: your first and only action is X\nEdit scripts/z740b.py\n' > "$T/$name.body"
sl=$(gate "$T/zco" 'SG_FAST_GATE_CMD=make fast-gate' 'SG_SUITES_HINT=the targeted cases (`make test K=<case>`)' 'SG_FULL_GATE_HINT=`make all`')
for w in '`make fast-gate`' 'the targeted cases (`make test K=<case>`)' '(`make all`)'; do
  case "$sl" in *"$w"*) ok "19b the request names the profile's '$w'";; *) ko "19b the request lacks the profile's '$w': $sl";; esac
done
case "$sl" in *contrib/squad-goal/*) ko "19b a profile with its own gates still names a contrib/squad-goal path: $sl";; *) ok "19b the profile's gates replace agent-squad's";; esac
# 19c. an empty value in the profile wins over the checkout: a defined none is none
name=z740c; printf 'IMPORTANT: your first and only action is X\nEdit scripts/z740c.py\n' > "$T/$name.body"
sl=$(gate "$T/aco" SG_FAST_GATE_CMD= SG_SUITES_HINT= SG_FULL_GATE_HINT=)
case "$sl" in *contrib/squad-goal/*) ko "19c an empty profile value still names a script: $sl";; *) ok "19c an empty profile value names no script";; esac
# 19d. agent-squad (its checkout holds the scripts, its profile defines none): the line is byte-identical to the one before #740
name=z740d; printf 'IMPORTANT: your first and only action is X\nEdit scripts/z740d.py\n' > "$T/$name.body"
was="SUITES: run what you touched -- the targeted pytest cases (\`uv run --extra dev pytest tests/<file>.py -q -p no:deepeval -k <case>\`) and the shell suite beside the script you changed (\`bash contrib/squad-goal/scripts/test-<script>.sh\`), unless the SUITE COST line names that suite: the fast gate runs it once per head, so a suite the SUITE COST line names is never run by you. Then run \`bash contrib/squad-goal/scripts/fast-gate.sh\` in your worktree before you report done: it is the gate your commit must pass to enter the wave, its pytest takes the bounded worker count it carries. Run it once per commit: exit 0 is a pass; exit 4 is unproven whatever its line names (a bounded battery, an inconclusive battery or a full gate budget), not a defect in your work, and it ends your gate step: the entry gate and the landing measure your commit again under their own clock, so never run it a second time on the same commit, never wait for the machine, the nightly or another gate, and report done with the gate's last line in your result. When that line says unproven, end your result with {\"status\": \"unproven\", \"reasons\": [that line]}: a gate that could not run is no completed proof and no failing child, and the run carries the obligation (ADR-0164). Never list the fast gate in ac_commands: the run's gate runs that command again at its final head, and the proof there is the run's, not yours. Exit 1 names a defect: fix it and run the gate once on the new commit. The machine's full gate (\`bash contrib/squad-goal/scripts/shell-suites.sh\` and the full pytest suite beside it) belongs to the loop and to the land pass, once per landing: never run it."
[ "$(gate "$T/aco")" = "$was" ] && ok "19d agent-squad's SUITES line is unchanged" || ko "19d agent-squad's SUITES line changed: $(gate "$T/aco")"
# 20. (#1527) A child that re-ran a bounded gate until its deadline landed nothing. The request says that exit 4 is unproven, ends
# the child's gate step, and is never run a second time on the same commit.
name=z1527; printf 'IMPORTANT: your first and only action is X\nEdit scripts/z1527.py\n' > "$T/$name.body"
sl=$(gate "$T/aco")
case "$sl" in *'Run it once per commit'*'exit 4'*'unproven'*'never run it a second time on the same commit'*'report done'*) ok "20 the request tells the child that exit 4 is unproven, ends its gate step and is never retried on the same commit (#1527)";; *) ko "20 the request does not tell the child that exit 4 is unproven and never retried: $sl";; esac
# 21. (#1715) A child that met a full gate budget read exit 4 as a failure, because the request tied exit 4 to two words of the
# gate line. The request names exit 4 by its code, with no condition on the line, and still names exit 1 as a defect.
case "$sl" in *'exit 4 is unproven whatever its line names'*'a full gate budget'*'it ends your gate step'*'Exit 1 names a defect'*) ok "21 the request names exit 4 as unproven whatever the gate line says (#1715)";; *) ko "21 the request ties exit 4 to the words of the gate line: $sl";; esac
# 22. (#1844) A delta that adds a test file selects the shard guard, and one that adds a shell suite selects the measured-line
# guard, so the child runs the guard in the gate its commit must pass. A rule the brief never names comes back as a red in the
# child's own gate, one gate late: the request names the shard entry and the write command for the measured line.
rq=$R/z1527.txt   # the request case 20 built, whose body names the same checkout as 19
if /usr/bin/grep -q 'ci_shards.py' "$rq" && /usr/bin/grep -q 'measure_shell_suites.py --write' "$rq"; then
  ok "22 the request names the shard entry and the measured line a new test file and suite need (#1844)"
else
  ko "22 the request names no shard entry or no measured-line command: $(/usr/bin/grep -c 'ci_shards.py' "$rq") shard, $(/usr/bin/grep -c 'measure_shell_suites.py' "$rq") measure"
fi
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
cat "$log"; rm -rf "$T"; exit $fail
