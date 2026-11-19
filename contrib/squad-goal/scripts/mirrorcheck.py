#!/usr/bin/env python3
"""Check a suite's `# mirrors:` line against the modules its fixture loads (#2357).

A shell suite beside this script copies a small tree into a temporary live-copy shape, and its first `# mirrors:`
line names that tree. The line is written by hand, so the check reads the suite's own fixture commands for
`python3 <name>.py` and `import <name>`, keeps the names this repository carries beside the suite, and fails when the
list and the loads disagree: a load the line does not carry, or a name the line carries and the repository does not.
A suite with no `# mirrors:` line declares no copy, and the gate never picks it by a mirrored file, so the check
leaves it alone.

A bare `python3 <name>.py` counts; a path such as `$T/<name>.py` or `$d/<name>.py` is a fixture of the suite or a
direct read of the repository, never a module of the copy, so it does not. A name the suite writes itself with a
redirect is its own fixture too, so it does not count either.

usage: python3 mirrorcheck.py [suite ...]      no suite checks every test-*.sh beside this script
Exit 0 when every suite agrees and prints one line; exit 1 and one FAIL line per defect otherwise.
"""
import re
import sys
from pathlib import Path

MIRRORS = re.compile(r"^# mirrors:[ \t]*(.*?)[ \t]*$")
PY_CALL = re.compile(r"\bpython3[ \t]+(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
IMPORT = re.compile(r"^[ \t]*import[ \t]+(.+?)[ \t]*$")
FROM = re.compile(r"^[ \t]*from[ \t]+([A-Za-z_][\w.]*)[ \t]+import\b")
WRITE = re.compile(r">[ \t]*\"?([^\s\"'<>]+\.py)\"?")
LOCAL = re.compile(r"^[A-Za-z_]\w*\.py$")
MODULE = re.compile(r"^[A-Za-z_]\w*$")


def mirror_line(text):
    """The names in the suite's first `# mirrors:` line, or None when it has none."""
    for line in text.splitlines():
        m = MIRRORS.match(line)
        if m:
            return m.group(1).split()
    return None


def loads(text):
    """Every file name the suite's fixture commands load by a bare name."""
    names = set()
    for line in text.splitlines():
        m = PY_CALL.search(line)
        if m:
            token = next(g for g in m.groups() if g is not None).strip()
            if LOCAL.match(token):
                names.add(token)
        m = IMPORT.match(line)
        if m:
            for part in m.group(1).split(","):
                word = part.strip()
                if not word:
                    continue
                module = word.split()[0].split(".")[0]
                if MODULE.match(module):
                    names.add(module + ".py")
        m = FROM.match(line)
        if m:
            names.add(m.group(1).split(".")[0] + ".py")
    return names


def written(text):
    """The file names the suite writes into its own fixture, so it carries them itself."""
    return {Path(p).name for p in WRITE.findall(text)}


def check_suite(suite):
    """The FAIL reasons for one suite; None when it declares no copy."""
    text = suite.read_text(errors="replace")
    mirrors = mirror_line(text)
    if mirrors is None:
        return None
    beside = suite.parent
    carried = set(mirrors)
    local = {n for n in loads(text) - written(text) if (beside / n).is_file()}
    reasons = [
        f"{suite.name}: the fixture loads {n}, which its # mirrors: line does not carry" for n in sorted(local - carried)
    ]
    reasons += [
        f"{suite.name}: the # mirrors: line names {n}, which the repository does not carry"
        for n in mirrors
        if not (beside / n).is_file()
    ]
    return reasons


def main(argv):
    suites = [Path(a) for a in argv] or sorted(Path(__file__).resolve().parent.glob("test-*.sh"))
    reasons, checked = [], 0
    for suite in suites:
        if not suite.is_file():
            reasons.append(f"{suite.name}: the suite does not exist")
            continue
        found = check_suite(suite)
        if found is None:
            continue
        checked += 1
        reasons.extend(found)
    for reason in reasons:
        print(f"mirrorcheck: FAIL {reason}")
    if reasons:
        return 1
    print(f"mirrorcheck: ok: {checked} suites name the modules their fixtures load")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
