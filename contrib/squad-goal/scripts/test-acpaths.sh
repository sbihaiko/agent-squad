#!/bin/bash
# seconds: 1.38
# Regression test for acpaths.py (#729): a ticket's acceptance commands that name a repository path the repository does not
# hold are found before dispatch. A path the ticket creates is not missing. Offline: a throwaway repository and issue files.
# usage: bash test-acpaths.sh [scripts dir]
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
trap 'rm -rf "$T"' EXIT
G=/usr/bin/git; R=$T/repo; mkdir -p "$R/scripts" "$R/tests"
(cd "$R" && $G init -q -b main && echo x > scripts/here.sh && echo x > scripts/onbase.py && $G add -A \
  && $G -c user.email=a@b -c user.name=t commit -q -m init) && rm -f "$R/scripts/onbase.py"
# issue <name> <body>: an issue file in the shape the dispatchers hold (.body)
issue() { jq -n --arg b "$2" '{number:1,title:"t",body:$b}' > "$T/$1.json"; }
ac() { out=$(python3 "$d/acpaths.py" "$T/$1.json" "$R" main 2>&1); rc=$?; }
# 1. AC1: an acceptance command names a missing script: the path is printed, exit 1
issue gone $'## What to build\n\nAn ADR.\n\n## Acceptance criteria\n\n- [ ] `python3 scripts/checks/verify_adr_refs.py` passes.\n- [ ] `bash scripts/here.sh` passes.'
ac gone
[ "$rc" = 1 ] && ok "a missing script exits 1" || ko "a missing script: rc $rc '$out'"
[ "$out" = scripts/checks/verify_adr_refs.py ] && ok "it prints the missing path, and only it" || ko "printed: '$out'"
# 2. AC2: the ticket names the script under What to build: the run creates it, so it is not missing
issue made $'## What to build\n\nAdd `scripts/checks/made.py`.\n\n## Acceptance criteria\n\n- [ ] `python3 scripts/checks/made.py` passes.'
ac made; [ "$rc" = 0 ] && [ -z "$out" ] && ok "a path named under What to build is not missing" || ko "a created path: rc $rc '$out'"
# 2b. the acceptance line says the run creates it
issue line $'## Acceptance criteria\n\n- [ ] A new check `scripts/checks/new_one.py` exits 0.'
ac line; [ "$rc" = 0 ] && ok "a path the acceptance line creates is not missing" || ko "a path the line creates: rc $rc '$out'"
# 2c. a test file: the ticket's TDD writes it (ADR-0136)
issue test $'## Acceptance criteria\n\n- [ ] Test: `tests/test_new_thing.py`.\n- [ ] `bash contrib/x/test-new.sh` passes.'
ac test; [ "$rc" = 0 ] && ok "a test file the ticket writes is not missing" || ko "a test file: rc $rc '$out'"
# 3. AC3: no acceptance command, no acceptance section: nothing is missing
issue none $'## What to build\n\nRun `python3 scripts/nowhere.py` once.'
ac none; [ "$rc" = 0 ] && [ -z "$out" ] && ok "a ticket with no acceptance command passes" || ko "no AC: rc $rc '$out'"
issue empty ''; ac empty; [ "$rc" = 0 ] && ok "an empty body passes" || ko "an empty body: rc $rc '$out'"
# 4. a path the base ref holds is not missing, even when the working tree lacks it
issue base $'## Acceptance criteria\n\n- [ ] `python3 scripts/onbase.py` passes.'
ac base; [ "$rc" = 0 ] && ok "a path the base ref holds is not missing" || ko "a path on the base ref: rc $rc '$out'"
# 5. what is not a repository path: a URL, an absolute path, a placeholder, a glob, a flag value
issue other $'## Acceptance criteria\n\n- [ ] `curl https://x.y/a/b.json`, `cat /etc/hosts.conf`, `bash scripts/<name>.sh`, `ls docs/*.md`, `~/x/y.sh`.'
ac other; [ "$rc" = 0 ] && ok "a URL, an absolute path, a placeholder and a glob are not repository paths" || ko "non-paths: rc $rc '$out'"
# 6. a pytest node id and a path in prose: the node's file is checked; prose outside a code span is not a command
issue node $'## Acceptance criteria\n\n- [ ] `pytest squad/gone_mod.py::TestX -q` passes; see squad/prose_only.py.'
ac node; [ "$out" = squad/gone_mod.py ] && ok "a pytest node id names its file" || ko "node id: rc $rc '$out'"
# 7. an unreadable issue refuses nothing: exit 2
echo '{' > "$T/bad.json"; python3 "$d/acpaths.py" "$T/bad.json" "$R" main >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "an unreadable issue exits 2" || ko "an unreadable issue: rc $rc"
[ "$fail" = 0 ] && { echo "acpaths: all checks green"; exit 0; }
echo "acpaths: FAILED"; exit 1
