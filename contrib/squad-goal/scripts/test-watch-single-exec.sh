#!/usr/bin/env bash
# seconds: 0.02
# Every script watch.sh runs directly under single.sh (no "bash" before it) is
# executable in git, so the pass never dies on "Permission denied".
cd "$(dirname "$0")/../../.." || exit 1
fail=0
for s in $(grep -oE 'single\.sh" [a-z]+ "\$\(dirname "\$0"\)/[A-Za-z0-9_-]+\.sh"' contrib/squad-goal/scripts/watch.sh | sed -E 's|.*/([A-Za-z0-9_-]+\.sh)"$|\1|' | sort -u); do
  mode=$(git ls-files -s "contrib/squad-goal/scripts/$s" | awk '{print $1}')
  if [ "$mode" = 100755 ]; then echo "ok   $s is executable"; else echo "FAIL $s runs directly under single.sh but its mode is ${mode:-missing}"; fail=1; fi
done
exit $fail
