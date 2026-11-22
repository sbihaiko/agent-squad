#!/bin/bash
# seconds: 0.75
# The commit guard (commit-guard.sh, ADR-0177): a file whose name ends in `slack.local.yaml` never enters a commit.
# One case per rule: a staged local file is refused and a staged ordinary file passes; the bare name and a project-prefixed
# name are both refused; --install writes an executable pre-commit hook, and a real `git commit` of the local file is
# refused by it while an ordinary commit passes; a second --install changes nothing; a foreign hook is refused, never
# overwritten; a named path is checked with no repository.
# Offline: temp repositories, a fixed git identity. usage: bash test-commit-guard.sh [commit-guard.sh]
d=$(cd "$(dirname "$0")" && pwd); src=${1:-$d/commit-guard.sh}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok - $1"; }
ko() { echo "FAIL - $1"; fail=1; }
G=/usr/bin/git
repo() { mkdir -p "$1"; $G -C "$1" init -q; }
hookpath() { local h; h=$($G -C "$1" rev-parse --git-path hooks); case "$h" in /*) echo "$h/pre-commit";; *) echo "$1/$h/pre-commit";; esac; }

# 1. staged ordinary files pass
R=$T/clean; repo "$R"; echo x > "$R/notes.yaml"; $G -C "$R" add notes.yaml
out=$(cd "$R" && bash "$src" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "an ordinary staged file passes" || ko "an ordinary file exited $rc: $out"

# 2. a staged project-prefixed local file is refused, one line naming it
R=$T/local; repo "$R"; echo x > "$R/zesty.slack.local.yaml"; $G -C "$R" add zesty.slack.local.yaml
out=$(cd "$R" && bash "$src" 2>&1); rc=$?
[ "$rc" = 1 ] && ok "a staged zesty.slack.local.yaml is refused" || ko "the local file exited $rc"
case "$out" in *zesty.slack.local.yaml*) ok "the refusal names the path";; *) ko "the refusal does not name the path: $out";; esac

# 3. the bare name is refused too
R=$T/bare; repo "$R"; echo x > "$R/slack.local.yaml"; $G -C "$R" add slack.local.yaml
out=$(cd "$R" && bash "$src" 2>&1); rc=$?
[ "$rc" = 1 ] && ok "a staged slack.local.yaml is refused" || ko "the bare name exited $rc"

# 4. a file that only ends differently passes
R=$T/near; repo "$R"; echo x > "$R/slack.local.yaml.example"; $G -C "$R" add slack.local.yaml.example
out=$(cd "$R" && bash "$src" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a name that does not end in the suffix passes" || ko "the near name exited $rc: $out"

# 5. --install writes an executable hook, and the hook refuses a real commit of the local file
R=$T/hook; repo "$R"; echo x > "$R/slack.local.yaml"; echo y > "$R/notes.yaml"; $G -C "$R" add notes.yaml
out=$(cd "$R" && bash "$src" --install 2>&1); rc=$?
hook=$(hookpath "$R")
[ "$rc" = 0 ] && [ -x "$hook" ] && ok "--install writes an executable hook" || ko "--install exited $rc: $out"
$G -C "$R" -c user.email=t@t -c user.name=t commit -qm "ordinary" >/dev/null 2>&1
[ $? = 0 ] && ok "an ordinary commit passes the hook" || ko "the hook refused an ordinary commit"
$G -C "$R" add slack.local.yaml
out=$($G -C "$R" -c user.email=t@t -c user.name=t commit -qm "leak" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "the hook refuses the commit of a local file" || ko "the hook let the local file through"
case "$($G -C "$R" log --oneline)" in *leak*) ko "the refused commit landed anyway";; *) ok "the refused commit never landed";; esac

# 6. a second --install changes nothing
before=$(cksum < "$hook")
out=$(cd "$R" && bash "$src" --install 2>&1); rc=$?
[ "$rc" = 0 ] && [ "$before" = "$(cksum < "$hook")" ] && ok "a second --install changes nothing" || ko "a second --install changed the hook or exited $rc: $out"

# 7. a foreign hook is refused, never overwritten
R=$T/foreign; repo "$R"; printf '#!/bin/bash\nexit 0\n' > "$(hookpath "$R")"
out=$(cd "$R" && bash "$src" --install 2>&1); rc=$?
[ "$rc" = 1 ] && ok "a foreign hook stops --install" || ko "a foreign hook exited $rc: $out"
case "$out" in *already*) ok "the refusal names the existing hook";; *) ko "the refusal does not name the hook: $out";; esac

# 8. named paths are checked with no repository
out=$(cd "$T" && bash "$src" a/notes.yaml b/slack.local.yaml 2>&1); rc=$?
[ "$rc" = 1 ] && ok "a named local path is refused outside a repository" || ko "named paths exited $rc: $out"
out=$(cd "$T" && bash "$src" a/notes.yaml 2>&1); rc=$?
[ "$rc" = 0 ] && ok "named ordinary paths pass" || ko "named ordinary paths exited $rc: $out"

[ "$fail" = 0 ] && echo "all commit-guard cases passed"
exit "$fail"
