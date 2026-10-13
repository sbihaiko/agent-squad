#!/bin/bash
# Regression test for redispatching a clean issue worktree (#65).
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
mkdir -p "$T/bin" "$T/s" "$T/st/auto" "$T/st/req" "$T/st/cq" "$T/co/docs/squad"
: > "$T/st/active-runs.txt"
cp "$d"/{autobug.sh,cluster.py,startability.sh} "$T/s/"
printf 'caps:\n  runs: 1\n' > "$T/co/docs/squad/defaults.yaml"
/usr/bin/git init -q -b main "$T/co"
/usr/bin/git -C "$T/co" -c user.email=a@b -c user.name=t commit -q --allow-empty -m base
mkdir -p "$T/co/worktrees/child"
/usr/bin/git -C "$T/co" worktree add -q -b gui/65 "$T/co/worktrees/child/gui-65" main
cat > "$T/s/env.sh" <<EOF
export SG_REPO=x/y SG_STATE=$T/st SG_BASE=main SG_CHECKOUT=$T/co SG_BUG_LABEL=bug SG_CODING_RULES=rules
EOF
cat > "$T/bin/gh" <<'EOF'
#!/bin/bash
case "$*" in
  *pulls?state=open*) echo "" ;;
  *issues?state=open*labels=bug*) printf '65\037bug\n' ;;
  *issues/65/comments*) echo "" ;;
  *issues/65\ -q*) printf '"Issue 65"\nbody\n' ;;
  *issues/65*) printf '{"number":65,"title":"Issue 65","body":"body","created_at":"2026-10-13T00:00:00Z"}\n' ;;
  *) echo "" ;;
esac
EOF
cat > "$T/s/mkreq.sh" <<EOF
#!/bin/bash
cp "\$2" "$T/body"
echo "\$2"
EOF
cat > "$T/s/launch.py" <<'EOF'
pass
EOF
chmod +x "$T/bin/gh" "$T/s/mkreq.sh"
export PATH="$T/bin:$PATH" SG_SQUAD=$(cd "$d/../../.." && pwd) SQUAD_CAPS_DIR="$T/caps" SG_SINGLE_AUTOBUG=1
run() { rm -f "$T/body" "$T/st/auto/bug-65"; bash "$T/s/autobug.sh" >"$T/run.out" 2>&1; }
run
/usr/bin/grep -qF 'REUSE ISSUE WORKTREE: Use the clean existing worktree' "$T/body" \
  && ok "a clean issue worktree is reused" \
  || ko "a clean issue worktree was not offered for reuse: $(cat "$T/run.out")"
printf 'unfinished work\n' > "$T/co/worktrees/child/gui-65/left.txt"
run
/usr/bin/grep -qF 'PRESERVE ISSUE WORKTREE: The existing worktree' "$T/body" \
  && /usr/bin/grep -qF 'Do not reuse, reset, delete, or replace it.' "$T/body" \
  && ok "a dirty issue worktree is preserved and reported" \
  || ko "a dirty issue worktree was not preserved and reported"
rm -rf "$T"
exit $fail
