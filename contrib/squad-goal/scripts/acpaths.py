#!/usr/bin/env python3
"""usage: acpaths.py <issue.json> <repo-dir> [ref...]

Prints each repository path that a ticket's acceptance commands name and the repository does not hold, one a line.
Exit 0: no path is missing. Exit 1: one or more paths are missing. Exit 2: the issue cannot be read (the caller
dispatches as before: a check that cannot run refuses nothing).

A path is named in an acceptance command when it sits in an inline code span (`...`) of the "Acceptance criteria"
section, has a directory part and a file extension, and is relative to the repository. A path is not missing when:
- the working tree of <repo-dir> holds it, or one of the git refs given holds it;
- the ticket names it outside the acceptance criteria (under "What to build", "Files:"): the run creates it;
- its acceptance line says the run creates it (new, create, add, write);
- it is a test file (its name starts with "test"): a ticket's TDD writes its own test (ADR-0136).
"""
import json
import os
import re
import subprocess
import sys

HEAD = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
SPAN = re.compile(r"`([^`\n]+)`")
PATH = re.compile(r"(?<![\w./~$<{*-])((?:\./)?[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)+\.[A-Za-z0-9]+)(?![\w/*>}])")
CREATES = re.compile(r"\b(new|create[sd]?|creating|add(s|ed)?|write[s]?|writing)\b", re.IGNORECASE)


def sections(body):
    """(ac_lines, other_text): the lines of every acceptance-criteria section, and all other text."""
    ac, other, inac = [], [], False
    for line in body.splitlines():
        m = HEAD.match(line)
        if m:
            inac = bool(re.search(r"acceptance", m.group(1), re.IGNORECASE))
            continue
        (ac if inac else other).append(line)
    return ac, "\n".join(other)


def named(ac):
    """(path, line) for every path an acceptance command names, in order, once each."""
    seen, out = set(), []
    for line in ac:
        for span in SPAN.findall(line):
            if "://" in span:
                continue
            for p in PATH.findall(span):
                p = p[2:] if p.startswith("./") else p
                if p not in seen:
                    seen.add(p)
                    out.append((p, line))
    return out


def held(repo, refs, path):
    if os.path.exists(os.path.join(repo, path)):
        return True
    for ref in refs:
        r = subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{ref}:{path}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            return True
    return False


def missing(body, repo, refs):
    ac, other = sections(body)
    out = []
    for p, line in named(ac):
        if os.path.basename(p).startswith("test"):
            continue
        if re.search(r"(?<![\w./-])" + re.escape(p) + r"(?![\w/])", other):
            continue
        if CREATES.search(line):
            continue
        if not held(repo, refs, p):
            out.append(p)
    return out


def main(argv):
    if len(argv) < 3:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    try:
        with open(argv[1]) as f:
            issue = json.load(f)
        body = issue.get("body") or ""
    except (OSError, ValueError, AttributeError) as e:
        print(f"acpaths.py: cannot read the issue: {e}", file=sys.stderr)
        return 2
    gone = missing(body, argv[2], argv[3:])
    for p in gone:
        print(p)
    return 1 if gone else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
