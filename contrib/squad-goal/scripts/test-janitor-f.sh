#!/bin/bash
# seconds: 1.39
# Regression test for janitor rule F (report-only: open issue named by a fork commit subject "(#N)"), stubbed gh and a throwaway fork repo. usage: bash test-janitor-f.sh [path/to/janitor.py]
# (#3070) SG_SQUAD names the tree this suite sits in, and each pass runs from the fixture folder: the janitor reads squad/
# from SG_SQUAD first and from the caller's cwd only as a fallback. The daily pass clears every SG_* setting and runs from a
# cwd that holds no squad/, so the fallback reached /squad/procs.py and the pass died at import with a traceback.
src=${1:-$(dirname "$0")/janitor.py}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
here=$(cd "$(dirname "$src")" && pwd); repo=$(cd "$here/../../.." && pwd)
mkdir -p "$T/bin" "$T/st/auto" "$T/co" "$T/fork"; cp "$src" "$T/janitor.py"; cp "$(dirname "$src")/issue_state.py" "$T/issue_state.py"; cp "$(dirname "$src")/board_columns.py" "$(dirname "$src")/board-definition.json" "$T/"
cat > "$T/bin/gh" <<'J'
#!/bin/bash
echo "$*" >> "${GH_LOG:-/dev/null}"
case "$*" in *issues/5*) echo open;; *issues/6*) echo closed;; *search/issues*) echo "${GH_PRS:-0}";; *) echo "";; esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
g() { git -C "$T/fork" -c user.email=a@b -c user.name=t "$@"; }
g init -q -b local/next; echo 1 > "$T/fork/f"; g add f
GIT_COMMITTER_DATE="2020-01-01T00:00:00" g commit -q --date="2020-01-01T00:00:00" -m "squad-goal: fix for a bug (#5)"
echo 2 > "$T/fork/f"; GIT_COMMITTER_DATE="2020-01-01T00:00:00" g commit -qam "squad-goal: another (#6)" --date="2020-01-01T00:00:00"
run() { ( cd "$T" && SG_SQUAD="$repo" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_FORK_DIR="$T/fork" SG_FORK_BASE=local/next SG_REPO=x/y JANITOR_DRY=1 python3 -I "$T/janitor.py" 2>&1 ); }
r=$(run); case "$r" in *"FIXED-NOT-CLOSED #5"*) ok "open issue named by an old fork commit is reported";; *) ko "no report for #5: '$r'";; esac
case "$r" in *"#6"*) ko "closed issue #6 reported";; *) ok "closed issue not reported";; esac
GH_PRS=1; export GH_PRS; r=$(run); case "$r" in *"#5"*) ko "reported although an open PR names it";; *) ok "an open PR names it: not reported";; esac
# #525: a commit whose issue is closed is settled: a second pass never reads that issue again.
unset GH_PRS; : > "$T/gh.log"; for i in 1 2; do ( cd "$T" && SG_SQUAD="$repo" SG_STATE="$T/st" SG_CHECKOUT="$T/co" SG_FORK_DIR="$T/fork" SG_FORK_BASE=local/next SG_REPO=x/y GH_LOG="$T/gh.log" python3 -I "$T/janitor.py" >/dev/null 2>&1 ); done
[ "$(/usr/bin/grep -c 'issues/6 ' "$T/gh.log")" = 1 ] && ok "a closed issue named by a fork commit is read once over two passes" || ko "issue 6 read $(/usr/bin/grep -c 'issues/6 ' "$T/gh.log") times"
rm -rf "$T"; exit $fail
