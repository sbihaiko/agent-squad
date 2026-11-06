"""#1488: a shell suite never fails on its own total wall time.

Wall time on a shared machine measures load, not the code. A suite that times itself from the top and fails when the total
reaches a cap turns a green suite red under load (the #1413 and #1385 readings). The rule below flags a `ko` or `fail` call
whose line uses a variable that is the difference of a clock read against a start taken at suite top level, before the
first ok/ko call. Per-case timing (a start taken after a check) is not flagged: it times one behavior, not the suite.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"

CHECK_ANY = re.compile(r"(?<![\w$.\-/])(ok|ko|fail)(?=\s)")
CHECK_FAIL = re.compile(r"(?<![\w$.\-/])(ko|fail)(?=\s)")
FUNC_OPEN = re.compile(r"^\s*(?:function\s+)?\w+\s*\(\)\s*\{\s*$")
FUNC_CLOSE = re.compile(r"^\}")
DEF_LINE = re.compile(r"^\s*\w+\(\)\s*\{")   # ok() { ...; } and ko() { ...; } define a check; they are not calls


def skipped(line):
    return line.lstrip().startswith("#") or bool(DEF_LINE.match(line)) or not line.strip()
START = re.compile(r"(?<![\w$])(\w+)=(?:\$\(date \+%s\)|\$SECONDS)(?![\w])")
DIFF = re.compile(r"(\w+)=\$\(\(\s*(?:\$\(date \+%s\)|\$SECONDS)\s*-\s*\$?\{?(\w+)\}?\s*\)\)")


def wall_clock_checks(text):
    """Return (lineno, line) for each ko/fail line that depends on the suite's own total elapsed time."""
    lines = text.splitlines()
    first_check = next((i for i, ln in enumerate(lines) if CHECK_ANY.search(ln) and not skipped(ln)), len(lines))
    starts, in_func = set(), False
    for i, ln in enumerate(lines[:first_check]):
        if FUNC_OPEN.match(ln):
            in_func = True
            continue
        if in_func:
            in_func = not FUNC_CLOSE.match(ln)
            continue
        if ln.lstrip().startswith("#"):
            continue
        starts.update(m.group(1) for m in START.finditer(ln))
    if not starts:
        return []
    elapsed = set()
    for ln in lines:
        for m in DIFF.finditer(ln):
            if m.group(2) in starts:
                elapsed.add(m.group(1))
    if not elapsed:
        return []
    refs = re.compile(r"\$\{?(" + "|".join(sorted(elapsed)) + r")\b")
    return [(i + 1, ln.strip()) for i, ln in enumerate(lines)
            if not skipped(ln) and CHECK_FAIL.search(ln) and refs.search(ln)]


POSITIVE = """\
#!/bin/bash
t0=$(date +%s)
ok() { echo "ok $1"; }
ko() { echo "FAIL $1"; }
ok "a case"
el=$(( $(date +%s) - t0 ))
[ "$el" -lt 300 ] && ok "inside the cap" || ko "took ${el}s over the cap"
"""

NEGATIVE = """\
#!/bin/bash
ok() { echo "ok $1"; }
ko() { echo "FAIL $1"; }
ok "a case"
t0=$(date +%s)
el=$(( $(date +%s) - t0 ))
[ "$el" -lt 300 ] && ok "a case times itself" || ko "a case took ${el}s"
"""


def test_the_rule_flags_a_suite_total_check():
    assert [n for n, _ in wall_clock_checks(POSITIVE)] == [7]


def test_the_rule_passes_a_per_case_timing_that_starts_after_a_check():
    assert wall_clock_checks(NEGATIVE) == []


def test_no_shell_suite_fails_on_its_own_total_wall_time():
    found = {}
    for path in sorted(SCRIPTS.glob("test-*.sh")):
        hits = wall_clock_checks(path.read_text(encoding="utf-8", errors="replace"))
        if hits:
            found[path.name] = hits
    assert not found, "a ko/fail depends on the suite's own total wall time (#1488): " + repr(found)
