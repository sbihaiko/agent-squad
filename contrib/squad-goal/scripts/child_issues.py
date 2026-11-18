#!/usr/bin/env python3
"""The issues a child's spawn text names (#555): the one reader janitor.py, childcc.py and wave.sh share.

The router words a child in its own way ("GOAL (issue 548, ...)"), so a reader that knew only the dispatcher's
"GitHub issue #N" read a live child as unmapped. Both dispatchers make every child carry `Refs #N`, so that line maps too.
A reader often hands in JSON-encoded text (a record line, json.dumps of a definition), where a newline is the two characters
backslash and n: "\\nRefs #715" has no word boundary before Refs, so a bundled child read as unmapped (#725). The text is
read with each escaped newline or tab as the whitespace it encodes.
usage: child_issues.py first   reads text on stdin, prints the first issue it names, or nothing."""
import re
import sys

# First match wins, in this order: the dispatcher's own forms, the hash-less forms, the Refs line, then a branch name.
_FIRST = (r"GitHub issue #(\d+)", r"\bissue #(\d+)", r"GitHub issues (\d+)", r"GitHub issue (\d+)", r"\bRefs #(\d+)",
          r"(?:gui/|wt-as-)(\d+)")
_NAMED = re.compile(r"GitHub issues? #?([0-9][0-9, ]*)|\bissue #(\d+)|\bRefs #(\d+)")


def plain(text):   # an escaped newline or tab, as JSON writes it, reads as the whitespace it encodes (#725)
    return (text or "").replace("\\n", "\n").replace("\\t", "\t")


def first(text):
    """The one issue a child works: the first pattern that matches, else None."""
    text = plain(text)
    for p in _FIRST:
        m = re.search(p, text)
        if m: return m.group(1)
    return None


def named(text):
    """Every issue the text names, a list form ("GitHub issues 11, 12") counted whole."""
    found = set()
    for m in _NAMED.finditer(plain(text)):
        found.update(x for x in re.split(r"[^0-9]+", m.group(1) or m.group(2) or m.group(3) or "") if x)
    return found


# The dispatcher's own cluster form: `GitHub issue #897` or `GitHub issue(s) 897, 908, 910`. Only this form names the
# issues one child works (#954); a reference the pasted ticket text quotes is context, not the child's work.
_CLUSTER = re.compile(r"GitHub issues?(?:\s*\(s\))?\s*#?([0-9][0-9,\s]*)")


def cluster(text):
    """Every issue one child works, in the order its dispatcher names it: empty when the text carries no cluster form."""
    out = []
    for m in _CLUSTER.finditer(plain(text)):
        for x in re.split(r"[^0-9]+", m.group(1)):
            if x and x not in out:
                out.append(x)
    return out


def request_issues(text):
    """The issues a run was dispatched for: the cluster form on the request's own `CHILD` lines, in order (#2276)."""
    out = []
    for line in plain(text).splitlines():
        if line.startswith("CHILD"):
            out += [x for x in cluster(line) if x not in out]
    return out


def of_run(text, run):
    """The issue one child of a run works, and the foreign reference its text named first, or None (#2276).

    The router words a child in its own way, and a ticket body it pastes may cite another issue before the child's own
    `Refs #N`. A child maps only to an issue its run was dispatched for: when the text's first match is none of them, the
    first reference that is one of them wins, and with no such reference the run's first issue. A run with no known
    issues keeps the first match."""
    mine = (cluster(text) or [None])[0] or first(text)
    run = [str(x) for x in run or []]
    if not run or mine in run: return mine, None
    for m in _NAMED.finditer(plain(text)):
        for x in re.split(r"[^0-9]+", m.group(1) or m.group(2) or m.group(3) or ""):
            if x in run: return x, mine
    return run[0], mine


if __name__ == "__main__" and sys.argv[1:2] == ["first"]:
    n = first(sys.stdin.read())
    if n: print(n)
