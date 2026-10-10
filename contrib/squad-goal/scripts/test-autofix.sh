#!/bin/bash
# Regression test for autofix.sh's [conflict] text with stubbed gh (no network, no model). usage: bash test-autofix.sh [path/to/autofix.sh]
# 1) a PR based on main: rebase onto origin/main (unchanged).  2) a PR stacked on an OPEN parent (base != main): rebase onto the parent's branch, never onto main.
src=${1:-$(dirname "$0")/autofix.sh}; T=$(mktemp -d); fail=0; ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/st/cq" "$T/s"; cp "$src" "$T/s/autofix.sh"; : > "$T/s/mkreq.sh"
cat > "$T/s/env.sh" <<J
SG_REPO=x/y; SG_STATE=$T/st; SG_BASE=main; SG_CHECKOUT=$T
J
cat > "$T/bin/gh" <<'J'
#!/bin/bash
case "$*" in
  *'.head.ref+" "'*) echo "feat/child aaaaaaaaaaaa dirty${GH_BASE:+ $GH_BASE}" ;;
  *graphql*) echo 0 ;;
  *) echo "" ;;
esac
J
chmod +x "$T/bin/gh"; export PATH="$T/bin:$PATH"
run() { AF_DRY=1 bash "$T/s/autofix.sh" 9 8 2>&1 | /usr/bin/grep '^\[conflict\]'; }
unset GH_BASE; r=$(run); case "$r" in *"Rebase onto origin/main"*) ok "main base: rebase onto origin/main";; *) ko "main base: '$r'";; esac
export GH_BASE=feat/parent; r=$(run)
case "$r" in *"Rebase onto origin/feat/parent"*) ok "stacked on an open parent: rebase onto the parent's branch";; *) ko "stacked: '$r'";; esac
case "$r" in *"onto origin/main"*) ko "stacked: still mentions rebasing onto main";; *) ok "stacked: no rebase onto main";; esac
case "$r" in *"only this PR's own commits (message says Refs #8"*) ok "stacked: replays only the PR's own commits";; *) ko "stacked: no own-commits hint";; esac
rm -rf "$T"; exit $fail
