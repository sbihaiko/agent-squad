#!/usr/bin/env python3
"""The shape of a squad review (#1777): a short, topic-grouped checklist.

Line 1 is `VERDICT: APPROVE` or `VERDICT: CHANGES` (the exact form the loop parses), then a
`## Blocking` section and/or a `## Debt` section, whichever is non-empty, Blocking first. Under a
section, one bullet per finding, at most two lines: `<file>:<line> — <defect> — <concrete failure>
— <fix>`. Findings that share a theme sit under a `### <theme>` heading, most severe first.
Nothing else: no preamble, no restating the diff, no closing summary.

`review.sh` runs this on every model answer: a review that ignores the shape is a format failure
of that try (the chain moves on and says so), never a silent pass. `autopilot.sh` asks for the
bullets per section with --summary instead of grepping a tag on the line (ADR-0273 Decision 3/5).

usage: revformat.py [--max-lines N] <review>   exit 0 = the shape holds; 1 = one reason on stdout
       revformat.py --summary <review>         prints "blocking=N debt=N" (0 when the shape is broken)
"""
import argparse
import re
import sys

VERDICT = re.compile(r"^VERDICT: (APPROVE|CHANGES)$")
SECTION = re.compile(r"^##\s*(Blocking|Debt)\s*$", re.IGNORECASE)
THEME = re.compile(r"^#{3,}\s*(\S.*?)\s*$")
BULLET = re.compile(r"^\s*[-*]\s+(\S.*)$")
LOC = re.compile(r"^[^\s:]+:\d+")            # path:line, the first field of a bullet (or the second)
SEP = re.compile(r"\s+[—–-]\s+")             # the " — " between the fields
SECTIONS = ("Blocking", "Debt")


def count(review):
    """The bullets per section, in any order and however the shape is broken (--summary).

    `autopilot.sh` asks this to tell a review that only raises DEBT from one that raises a Blocking finding.
    """
    counts, section = {name: 0 for name in SECTIONS}, None
    for line in review.replace("\r\n", "\n").split("\n"):
        m = SECTION.match(line.strip())
        if m:
            section = m.group(1).capitalize()
        elif not line.lstrip().startswith("#") and section and BULLET.match(line):
            counts[section] += 1
    return counts


def survey(review, max_lines=0):
    """The first violation of the shape as one sentence, or None when the review is the shape."""
    lines = [l.rstrip() for l in review.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    counts = {name: 0 for name in SECTIONS}
    if not any(l.strip() for l in lines):
        return "the review is empty (a verdict line, then its findings)"
    if not VERDICT.match(lines[0].strip()):
        return f"line 1 is {lines[0].strip()[:60]!r}, not 'VERDICT: APPROVE' or 'VERDICT: CHANGES'"
    approve = lines[0].strip().endswith("APPROVE")
    seen, section, bullet = [], None, None     # bullet: (line number, its lines so far), while one is open
    def close():                               # a bullet is validated once it ends, on its joined two lines
        if bullet is None:
            return None
        n, text = bullet
        fields = SEP.split(" ".join(text))
        if len(fields) < 3:
            return (f"line {n}: {text[0][:40]!r} is not 'file:line — defect — concrete failure — fix'"
                    " (three fields at least)")
        if not any(LOC.match(f.strip()) for f in fields[:2]):
            return f"line {n}: the bullet names no file:line ({fields[0].strip()[:30]!r})"
        counts[section] += 1
        return None
    for n, line in enumerate(lines[1:], start=2):
        text = line.strip()
        if not text:
            if (bad := close()):
                return bad                    # a blank line ends a bullet: prose after it is prose
            bullet = None
            continue
        m = SECTION.match(text)
        if m:
            if (bad := close()):
                return bad
            bullet = None
            name = m.group(1).capitalize()
            if name in seen:
                return f"line {n}: the {name} section appears twice"
            if name == "Blocking" and seen:
                return f"line {n}: Blocking comes after Debt; the sections go most severe first"
            seen.append(name); section = name
            continue
        if text.startswith("#"):
            if not THEME.match(text):
                return f"line {n}: {text[:40]!r} is not '## Blocking', '## Debt' or a '### <theme>' heading"
            if section is None:
                return f"line {n}: a theme heading with no '## Blocking' / '## Debt' section above it"
            if (bad := close()):
                return bad
            bullet = None
            continue
        m = BULLET.match(line)
        if m:
            if section is None:
                return f"line {n}: a finding outside any section: put it under '## Blocking' or '## Debt'"
            if (bad := close()):
                return bad                    # the previous bullet may not claim this line
            bullet = (n, [m.group(1)])
            continue
        if bullet is None:
            return (f"line {n}: {text[:50]!r} is neither a section, a bullet nor a theme heading"
                    " — a review is only that")
        bullet[1].append(text)
        if len(bullet[1]) > 2:
            return f"line {n}: the bullet at line {bullet[0]} runs over two lines"
    if (bad := close()):
        return bad
    if approve and counts["Blocking"]:
        return (f"VERDICT: APPROVE with {counts['Blocking']} Blocking finding(s): the verdict contradicts"
                " the review")
    used = sum(1 for l in lines if l.strip())
    if max_lines and used > max_lines:
        return f"the review is {used} lines, over the cap of {max_lines} (review_max_lines)"
    return None


def normalize(review):
    """The review with its verdict as line 1 in the exact form the loop parses.

    A model writes the verdict in bold, in lower case, or after a paragraph of narration: the shape's
    first line is not left to it (revci, autopilot and hub.py read line 1). Whatever stands in the way
    of the shape stays in the text, where the check reports it.
    """
    lines = review.replace("\r\n", "\n").split("\n")
    verdict, rest = "", []
    for line in lines:
        found = re.findall(r"VERDICT:\s*(APPROVE|CHANGES)", line, re.IGNORECASE)
        if not found:
            rest.append(line)
            continue
        verdict = found[-1].upper()
        left = re.sub(r"[*`]*VERDICT:\s*(?:APPROVE|CHANGES)[*`]*", "", line, flags=re.IGNORECASE).strip()
        if left:
            rest.append(left)
    if not verdict:
        return review
    return "\n".join([f"VERDICT: {verdict}"] + rest)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the shape of a squad review (a short checklist).")
    p.add_argument("review", help="the review to check, or - for stdin")
    p.add_argument("--max-lines", type=int, default=0,
                   help="line cap for the whole review (review_max_lines in models.json; 0 = no cap)")
    p.add_argument("--summary", action="store_true", help="print 'blocking=N debt=N' instead of checking")
    p.add_argument("--normalize", action="store_true",
                   help="print the review with its verdict canonical on line 1, and check nothing")
    a = p.parse_args(argv)
    text = sys.stdin.read() if a.review == "-" else open(a.review, encoding="utf-8", errors="replace").read()
    if a.normalize:
        print(normalize(text), end="" if text.endswith("\n") else "\n")
        return 0
    if a.summary:
        found = count(text)
        print(" ".join(f"{name.lower()}={found[name]}" for name in SECTIONS))
        return 0
    reason = survey(text, a.max_lines)
    if reason:
        print(reason)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
