#!/usr/bin/env python3
# drainpolicy.py: the one drain policy. It maps the open loop-bug issues to the four restrictions they block (#1980).
# usage: drainpolicy.py <dispatch|child|landing> < issues.json   prints the blocking issue and exits 0, exits 1 when none
#        drainpolicy.py publish <sha> < issues.json              exits 0 and prints the refusing issue, exits 1 when the sha may publish
# Exit 2 means the list could not be read, and every caller holds on it.
import json
import re
import sys

DISPATCH, CHILD, LANDING, PUBLISH = "dispatch", "child", "landing", "publish"
RESTRICTIONS = (DISPATCH, CHILD, LANDING, PUBLISH)
ALL = frozenset(RESTRICTIONS)
CLASSES = {
    "loop-bug": ALL,
    "loop-bug:landing": frozenset({DISPATCH, CHILD, LANDING}),
    "loop-bug:publish": frozenset({PUBLISH}),
}
REVIEWED_SHA = re.compile(r"^reviewed-sha:\s*([0-9a-f]{40})\s*$", re.MULTILINE)


def _loop_bug_issues(issues):
    for issue in issues:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        names = [label.get("name") if isinstance(label, dict) else label for label in issue.get("labels") or []]
        loop_names = [n for n in names if isinstance(n, str) and (n == "loop-bug" or n.startswith("loop-bug:"))]
        if loop_names:
            yield issue, loop_names


def _restrictions_of(loop_names):
    blocked = set()
    for name in loop_names:
        blocked |= CLASSES.get(name, ALL)
    return frozenset(blocked)


def restrictions(issues):
    blocked = set()
    for _, loop_names in _loop_bug_issues(issues):
        blocked |= _restrictions_of(loop_names)
    return frozenset(blocked)


def held(restriction, issues):
    for issue, loop_names in _loop_bug_issues(issues):
        if restriction in _restrictions_of(loop_names):
            return issue["number"]
    return None


def publish_refusal(issues, sha):
    for issue, loop_names in _loop_bug_issues(issues):
        if PUBLISH in _restrictions_of(loop_names) and sha not in REVIEWED_SHA.findall(issue.get("body") or ""):
            return issue["number"]
    return None


def main(argv):
    try:
        issues = json.load(sys.stdin)
    except ValueError:
        return 2
    if not isinstance(issues, list):
        return 2
    if len(argv) == 3 and argv[1] == PUBLISH:
        number = publish_refusal(issues, argv[2])
        restricted = number is not None
    elif len(argv) == 2 and argv[1] in RESTRICTIONS:
        number = held(argv[1], issues)
        restricted = number is not None
    else:
        return 2
    if not restricted:
        return 1
    print(number)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
