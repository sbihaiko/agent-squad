#!/bin/bash
# seconds: 0.37
# Regression test for mirrorcheck.py (#2357): a suite's `# mirrors:` line names the tree its live copy carries, and the
# check holds it against the modules the suite's own fixture commands load. A load the line does not carry fails, and so
# does a name the line carries that this repository does not. A list and its loads that agree pass with one line.
# usage: bash test-mirrorcheck.sh [scripts dir]
# mirrors: mirrorcheck.py
d=${1:-$(cd "$(dirname "$0")" && pwd)}; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; fail=0
ok() { echo "ok   $1"; }; ko() { echo "FAIL $1"; fail=1; }
py=python3
mkdir -p "$T/s"; printf 'x = 1\n' > "$T/s/a.py"; printf 'y = 2\n' > "$T/s/b.py"

# a module the fixture loads that the list does not carry: the check fails and names the suite and the file
printf '#!/bin/bash\n# mirrors: a.py\npython3 b.py\n' > "$T/s/test-miss.sh"
out=$("$py" "$d/mirrorcheck.py" "$T/s/test-miss.sh" 2>&1); rc=$?
[ "$rc" != 0 ] && ok "a load the mirror list does not carry fails" || ko "the missing load passed: rc=$rc $out"
case "$out" in *test-miss.sh*b.py*) ok "the failure names the suite and the missing file";; *) ko "no suite and file in: $out";; esac

# a name the list carries that the repository lacks: the check fails and names that file
printf '#!/bin/bash\n# mirrors: a.py ghost.py\nimport a\n' > "$T/s/test-ghost.sh"
out=$("$py" "$d/mirrorcheck.py" "$T/s/test-ghost.sh" 2>&1); rc=$?
if [ "$rc" != 0 ]; then case "$out" in *ghost.py*) ok "a mirror name the repository lacks fails and names the file";; *) ko "no ghost name in: $out";; esac; else ko "a ghost name passed: $out"; fi

# a list and its loads that agree: one line, exit 0, and the load may be a command or an import
printf '#!/bin/bash\n# mirrors: a.py b.py\npython3 b.py\nimport a\n' > "$T/s/test-ok.sh"
out=$("$py" "$d/mirrorcheck.py" "$T/s/test-ok.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a list and its loads agree and pass" || ko "an agreeing suite failed: rc=$rc $out"
[ "$(printf '%s\n' "$out" | /usr/bin/grep -c .)" = 1 ] && ok "a passing check prints one line" || ko "pass lines: $out"

# a suite with no mirrors line declares no copy, so the check leaves it alone
printf '#!/bin/bash\npython3 b.py\n' > "$T/s/test-noline.sh"
out=$("$py" "$d/mirrorcheck.py" "$T/s/test-noline.sh" 2>&1); rc=$?
[ "$rc" = 0 ] && ok "a suite with no mirrors line declares no copy and passes" || ko "no-line suite: rc=$rc $out"

exit $fail
