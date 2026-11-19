#!/bin/bash
# seconds: 0.58
# childfix.sh starts one fix run in a child's kept worktree, and none while the loop is paused (auto/off, #1792).
d="$(cd "$(dirname "$0")" && pwd)"; T=$(mktemp -d); fail=0; log=$T/log; : > "$log"
ok() { printf 'ok   %s\n' "$1" >> "$log"; }; ko() { printf 'FAIL %s\n' "$1" >> "$log"; fail=1; }
S=$T/scripts; mkdir -p "$S" "$T/bin" "$T/state/auto" "$T/co/runs" "$T/wt"
cp "$d/childfix.sh" "$S/"
printf 'export SG_STATE=%s SG_CHECKOUT=%s SG_REPO=x/y SG_CODING_RULES=rules\nsg_profile_resolves() { false; }\nsg_profiles_dir() { echo none; }\n' "$T/state" "$T/co" > "$S/env.sh"
printf '#!/bin/bash\necho req-fix\n' > "$S/mkreq.sh"
printf '#!/bin/bash\nexit 0\n' > "$T/bin/gh"; chmod +x "$T/bin/gh"
cat > "$S/launch.py" <<'PY'
import os, sys
open(os.environ["LAUNCHED"], "a").write(sys.argv[1] + "\n")
run = os.path.join(os.environ["SG_CHECKOUT"], "runs", "run-fix-1")
open(os.path.join(os.environ["SG_STATE"], "active-runs.txt"), "a").write(run + "\n")
print("run-fix-1")
PY
git -C "$T/wt" init -q -b child/x
git -C "$T/wt" -c user.email=a@b -c user.name=t commit -q --allow-empty -m base
printf 'VERDICT: CHANGES\n- [Blocking] a.py:1 fix\n' > "$T/state/review-child_x.md"
export PATH="$T/bin:$PATH" LAUNCHED="$T/launched"

out=$(cd "$T/wt" && bash "$S/childfix.sh" run-parent 1792 2>"$T/err")
[ "$(wc -l < "$LAUNCHED" 2>/dev/null | tr -d ' ')" = 1 ] && ok "without auto/off childfix.sh starts one fix run" || ko "without auto/off: $(cat "$LAUNCHED" 2>/dev/null) $(cat "$T/err")"
case "$out" in *run-fix-1) ok "without auto/off childfix.sh prints the run folder";; *) ko "without auto/off no run folder: '$out'";; esac

rm -f "$LAUNCHED"; : > "$T/state/active-runs.txt"; : > "$T/state/auto/off"
out=$(cd "$T/wt" && bash "$S/childfix.sh" run-parent 1792 2>"$T/err")
[ ! -s "$LAUNCHED" ] && ok "with auto/off childfix.sh starts no run" || ko "with auto/off a run started: $(cat "$LAUNCHED")"
[ -z "$out" ] && ok "with auto/off childfix.sh prints no run folder" || ko "with auto/off printed '$out'"
[ "$(grep -c 'no run starts' "$T/err")" = 1 ] && [ "$(grep -c . "$T/err")" = 1 ] && ok "with auto/off childfix.sh prints one skip line" || ko "with auto/off stderr: '$(cat "$T/err")'"

cat "$log"
[ $fail = 0 ] && printf 'TDD GREEN\n' || printf 'TDD RED\n'
rm -rf "$T"; exit $fail
