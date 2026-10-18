#!/usr/bin/env python3
"""The shape of a squad review (#1777): a short, topic-grouped checklist.

Line 1 is `VERDICT: APPROVE` or `VERDICT: CHANGES` (the exact form the loop parses), then a
`## Blocking` section and/or a `## Debt` section, whichever is non-empty, Blocking first. Under a
section, one bullet per finding, at most two lines: `<file>:<line> — <defect> — <concrete failure>
— <fix>`. A finding about the change as a whole (the branch delivers nothing from its issue, the
commit message misstates it) has no path to cite: its place is the diff, named by a whole-change
label in the location field -- a leading `whole` (`whole diff`, `whole branch`) or the word
`scope` (`Branch scope`, `Issue #249 scope`) (#443), the same room #412 gave a whole-file
concern. Findings that share a theme sit under a `### <theme>` heading, most severe first.
Nothing else: no preamble, no restating the diff, no closing summary.

`review.sh` runs this on every model answer: a review that ignores the shape is a format failure
of that try (the chain moves on and says so), never a silent pass. The loop's own passes ask for
the bullets per section with --summary instead of grepping a tag on the line (ADR-0273 Decision 3/5).

A fix round answers its previous findings in a `## Resolved` section of their own, accepted only
with --fix-round (#1834). An answered finding moves there with the same bullet,
`<file>:<line> — <defect> — fixed: <reason> — <check>`, so it stays auditable; the section is the
answer, not a severity, and it is counted by neither the check nor --summary. `## Blocking` and
`## Debt` keep meaning what they mean (#1777): a previous finding that is `not fixed` stays where
it was raised and still blocks, and the delta may add a new finding to either.

A bullet that answers `fixed` is the answer to a finding, not a finding (#1832): it is counted by
neither the check nor --summary, so APPROVE with every previous Blocking finding fixed or resolved
is the shape and the debt-only path sees blocking=0 (ADR-0273 Decision 3/5). A bullet saying
`not fixed`, and one carrying no marker at all (a full review), keeps its severity (#1777).

`is_finding` is the one predicate that tells a finding from an answer: the severity counts, the
--summary output the loop's own passes read, and the debt filer asks it, so no two readers can
disagree.

A review's findings are also read by their fingerprint (ADR-0134): the fix pass stops a branch's
rounds when a round leaves the findings as they were, and a child fix round asks the same rule. --fingerprint
drops line 1, a moved line number, a sha and whitespace, the reading `branchfixpass.sh` kept, so its
markers still match; --stall counts the failed rounds in a row for one marker set (`<dir>/<prefix>-fp-<key>`
and `-same-<key>`, so each pass keeps its own), and --record-stall keeps the round for the next pass.

usage: revformat.py [--max-lines N] [--fix-round] <review>
                                           exit 0 = the shape holds; 1 = one reason on stdout
       revformat.py --summary <review>     prints "blocking=N debt=N" (0 when the shape is broken)
       revformat.py --shape [--fix-round]  prints the answer shape of that mode, and checks nothing (#1835)
       revformat.py --fingerprint <review> prints the fingerprint of the review's findings
       revformat.py --stall <dir> <prefix> <key> <fingerprint>
                                           prints the failed rounds in a row for that marker set (ADR-0134)
       revformat.py --record-stall <dir> <prefix> <key> <fingerprint> <rounds>
                                           keeps this round's findings and count for the next pass
"""
import argparse
import hashlib
import os
import re
import sys

VERDICT = re.compile(r"^VERDICT: (APPROVE|CHANGES)$")
SECTION = re.compile(r"^##\s*(Blocking|Debt|Resolved)\s*$", re.IGNORECASE)
THEME = re.compile(r"^#{3,}\s*(\S.*?)\s*$")
BULLET = re.compile(r"^\s*[-*]\s+(\S.*)$")
# A section with no finding says so, and that is an empty section, not a malformed bullet (#498): the shape already takes a
# bare `VERDICT: APPROVE`, and a model that must write the heading under it writes `None.` there. Only its first content,
# and it may say why on the same line (`None. The four criteria are met.`, #504); a second line is prose again.
EMPTY = re.compile(r"(?i)^(?:none|no findings|nothing|n/?a|nil)(?:[.!]?$|[.:;!]?\s+\S|\s*[—–-]\s+\S)|^[—–-]$")
LOC = re.compile(r"^`?(?:[^\s:`]+:~?\d+(?:[-–]\d+)?|[^\s:`]*(?:/|\.[A-Za-z0-9]+)[^\s:`,]*)`?(?:[\s,;]|$)")  # path:line, or a path alone for a file-level finding; the first field of a bullet (or the second)
# a finding about the change as a whole, not a file (#443): the branch delivers nothing from its issue, the commit
# message misstates it -- there is no path to cite. Its place is the diff itself, and a whole-change label names it:
# a leading "whole" (whole diff / whole branch / the whole change), or the word "scope" (Scope: ... / Branch scope /
# Issue #249 scope). A bullet that still names no place at all fails (#412), so the vague one is not let in.
WHOLE = re.compile(r"^`?(?:(?:the\s+)?whole\b|.*\bscope\b)", re.IGNORECASE)
SEP = re.compile(r"\s+[—–-]\s+")             # the " — " between the fields
# the fix-round answer to a previous finding, the third field of a bullet (#1832): "fixed: <reason>", a bare "fixed",
# or "fixed — <check>". "not fixed: <reason>" is not the answer, and neither is prose that merely opens with the word
# ("fixed-size buffer"), which is why the separator after it has to be a colon, nothing or a dash.
FIXED = re.compile(r"^fixed(?:\s*:|\s*$|\s+[—–-]\s+)", re.IGNORECASE)
SECTIONS = ("Blocking", "Debt")      # the severity sections: the section a finding sits in is its severity (#1777)
ANSWER = "Resolved"                  # the fix round's section for answered findings: an answer, not a severity (#1834)


def answered_fixed(text):
    """True when a bullet is the fix round's answer to a previous finding, stated as `fixed` (#1832). `text` is the
    bullet's text, its two lines already joined.

    The marker is the third field review.sh's FMTFIX asks for (`<file>:<line> — <defect> — fixed|not fixed: <reason> —
    <check>`), read from the third field on, so a defect that opens with the word is not mistaken for the answer. The
    shape check already rejects a bullet of fewer than three fields, so that field exists on every bullet it counts.
    """
    return any(FIXED.match(f.strip()) for f in SEP.split(text)[2:])


def is_finding(section, text):
    """True when a bullet is a finding: it sits in a severity section (#1777) and is not a fix round's answer to a
    previous finding (#1832, #1834). `section` is where it sits; `text` is the bullet's text, its two lines joined.

    One predicate for every reader -- the severity counts behind the check and --summary, and the debt filer
    (`squad/debt_filing.py` asks this module) -- so none of them can call a different bullet a finding. An unmarked
    bullet (a full review) and one that answers `not fixed` are findings; an answered `fixed` one and one under
    `## Resolved` are answers, wherever they sit.
    """
    return section in SECTIONS and not answered_fixed(text)


def bullets(review):
    """(section, text) for every bullet, with the one extra line the shape allows joined onto the bullet above it."""
    out, section, open_bullet = [], None, False
    for line in review.replace("\r\n", "\n").split("\n"):
        text = line.strip()
        m = SECTION.match(text)
        if m:
            section, open_bullet = m.group(1).capitalize(), False
            continue
        if not text or text.startswith("#"):
            open_bullet = False
            continue
        found = BULLET.match(line)
        if found and section:
            out.append((section, found.group(1))); open_bullet = True
        elif section and open_bullet:
            out[-1] = (out[-1][0], out[-1][1] + " " + text)
    return out


def count(review):
    """The findings per section, in any order and however the shape is broken (--summary).

    The loop's own passes use this to distinguish a review that only raises DEBT from one that raises a Blocking finding,
    and `hub.py` asks it for the R cell: the two read one module so a debt-only CHANGES is not blocking on one surface and
    merged on the other. A bullet that answers a previous finding `fixed`, and one under `## Resolved`, is not a
    finding (#1832, #1834), the same reading `survey` makes -- so a fix round whose Blocking findings are all fixed or
    resolved reads `blocking=0`, and the debt filer files the bullets this counted and no other.
    """
    counts = {name: 0 for name in SECTIONS}
    for section, text in bullets(review):
        if is_finding(section, text):
            counts[section] += 1
    return counts


def findings_fingerprint(review):
    """The fingerprint of a review's findings: a round that fixed nothing keeps it (ADR-0134). `review` is its bytes.

    Line 1 (the verdict) is dropped, a line number after a colon and a sha are removed, and whitespace is squeezed to
    one space, so a moved line or a new sha is not a change; SHA-1 of the result, cut to 16 characters. The bytes are
    the ones branchfixpass.sh read before the reading moved here, so a marker that pass kept still matches.
    """
    body = review.split(b"\n", 1)[1] if b"\n" in review else b""   # tail -n +2: the verdict line is not a finding
    body = re.sub(rb":[0-9]+", b":", body)                          # a line number that moved
    body = re.sub(rb"[0-9a-f]{7,40}", b"", body)                    # a sha, short or whole
    body = re.sub(rb"[ \t\n]+", b" ", body)                         # whitespace of any kind, squeezed (tr -s)
    return hashlib.sha1(body).hexdigest()[:16]


def _marker(path):
    """A marker's text, without the newline the writer gave it; '' when there is no marker."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().rstrip("\n")
    except OSError:
        return ""


def stall_rounds(markers, prefix, key, fingerprint):
    """The failed rounds in a row that this round carries, counted as branchfixpass.sh counted them (ADR-0134).

    A round fails when the findings are the ones the last round left, and the count rises by one. A changed fingerprint
    restarts the count at zero: the next round with those findings counts one. The prefix and the key name the markers
    (`<markers>/<prefix>-fp-<key>` and `-same-<key>`), so each pass keeps its own.
    """
    if _marker(os.path.join(markers, f"{prefix}-fp-{key}")) != fingerprint:
        return 0
    try:
        return int(_marker(os.path.join(markers, f"{prefix}-same-{key}"))) + 1
    except ValueError:
        return 1                    # a marker that carries no count is no count: the round before it was the first


def record_stall(markers, prefix, key, fingerprint, rounds):
    """Keep this round's findings and its count for the next pass, as branchfixpass.sh kept them."""
    os.makedirs(markers, exist_ok=True)
    for name, value in ((f"{prefix}-fp-{key}", fingerprint), (f"{prefix}-same-{key}", rounds)):
        with open(os.path.join(markers, name), "w", encoding="utf-8") as f:
            f.write(f"{value}\n")


def survey(review, max_lines=0, fix_round=False):
    """The first violation of the shape as one sentence, or None when the review is the shape.

    `fix_round` is the mode the answer was asked for: only there is `## Resolved` part of the grammar (#1834). Both
    modes are checked against the same severity rule (#1777) and the same notion of a finding (`is_finding`).
    """
    lines = [l.rstrip() for l in review.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    counts = {name: 0 for name in SECTIONS}
    if not any(l.strip() for l in lines):
        return "the review is empty (a verdict line, then its findings)"
    if not VERDICT.match(lines[0].strip()):
        return f"line 1 is {lines[0].strip()[:60]!r}, not 'VERDICT: APPROVE' or 'VERDICT: CHANGES'"
    approve = lines[0].strip().endswith("APPROVE")
    seen, held, section, bullet = [], set(), None, None     # bullet: (line number, its lines so far), while one is open; held: sections with content
    def close():                               # a bullet is validated once it ends, on its joined two lines
        if bullet is None:
            return None
        n, text = bullet
        joined = " ".join(text)
        fields = SEP.split(joined)
        if len(fields) < 3:
            return (f"line {n}: {text[0][:40]!r} is not 'file:line — defect — concrete failure — fix'"
                    " (three fields at least)")
        if not any(LOC.match(f.strip()) or WHOLE.match(f.strip()) for f in fields[:2]):
            return f"line {n}: the bullet names no file, file:line or whole diff ({fields[0].strip()[:30]!r})"
        if is_finding(section, joined):            # a bullet that answers a previous finding is not a finding
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
            if name == ANSWER and not fix_round:
                return (f"line {n}: '## {ANSWER}' is a fix round's answer to its previous findings, and this is not a"
                        " fix round: a full review has '## Blocking' and/or '## Debt' (the #1777 severity of each"
                        " finding), and nothing else")
            if name in seen:
                return f"line {n}: the {name} section appears twice"
            if name == "Blocking" and any(s in seen for s in SECTIONS):
                return f"line {n}: Blocking comes after Debt; the sections go most severe first"
            seen.append(name); section = name
            continue
        if text.startswith("#"):
            if not THEME.match(text):
                known = "'## Blocking', '## Debt'" + (f", '## {ANSWER}'" if fix_round else "")
                return f"line {n}: {text[:40]!r} is not {known} or a '### <theme>' heading"
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
            held.add(section)
            continue
        if bullet is None and section is not None and section not in held and EMPTY.match(text):
            continue                        # `None.` opens its section as empty (#498); after a bullet it is prose, below
        if bullet is None:
            return (f"line {n}: {text[:50]!r} is neither a section, a bullet nor a theme heading"
                    " — a review is only that")
        bullet[1].append(text)
        if len(bullet[1]) > 2:
            return f"line {n}: the bullet at line {bullet[0]} runs over two lines"
    if (bad := close()):
        return bad
    if approve and counts["Blocking"]:   # only the Blocking bullets that are not answered 'fixed' reach counts (#1832)
        return (f"VERDICT: APPROVE with {counts['Blocking']} Blocking finding(s): the verdict contradicts"
                " the review")
    used = sum(1 for l in lines if l.strip())
    if max_lines and used > max_lines:
        return f"the review is {used} lines, over the cap of {max_lines} (review_max_lines)"
    return None


def shape(fix_round=False):
    """The answer shape of a review mode, as the smallest answer that mode accepts (#1835).

    `review.sh` does not describe the shape in words: it embeds this text, verbatim, in the prompt it sends -- so the
    prompt and the check that reads the answer cannot drift apart, and a wording the check cannot parse is a red test
    (`test-revformat.sh`) instead of a review round lost in the loop.

    It is a template, not a sample: `file:line` and every `<...>` field are the placeholders the model fills with the
    real path and line, the defect, the failure it causes and the fix. Filling them yields a review this module
    accepts, which is what the contract test asserts. The sections are printed from SECTIONS and the answer section
    from ANSWER, so a rename here reprints itself; `## Resolved` is printed only in the fix-round mode, the one mode
    whose grammar has it (#1834).
    """
    blocking, debt = SECTIONS
    finding = "- file:line — <defect> — <concrete failure> — <fix>"
    lines = ["VERDICT: <APPROVE or CHANGES>",
             f"## {blocking}",
             "### <theme, most severe first; a lone finding needs no heading>",
             finding,
             f"## {debt}",
             finding]
    if fix_round:
        # a previous finding that is not fixed keeps the section it was raised in; the one that is fixed moves to the
        # answer section. Both are printed, since a fix round has to tell them apart (#1832, #1834).
        lines[3] = "- file:line — <defect> — not fixed: <one-line reason> — <the failure it still causes>"
        lines += [f"## {ANSWER}",
                  "- file:line — <defect> — fixed: <one-line reason> — <the check that proves it>"]
    return "\n".join(lines)


def normalize(review):
    """The review with its verdict as line 1 in the exact form the loop parses.

    A model writes the verdict in bold, in lower case, or after a paragraph of narration: the shape's
    first line is not left to it (`review.sh`, the loop's own passes and `hub.py` read line 1). A prose preamble before
    the first severity section is dropped; whatever else stands in the way of the shape stays in the text, where the
    check reports it.
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
    # A preamble before the first section ("I'll review the branch against ...") is dropped, so a sound review is not
    # lost to it (owner ruling). Prose that no section follows stays, and the check refuses it.
    # The cut is the first severity section; a list item before it (a finding) keeps the text whole.
    head = next((i for i, line in enumerate(rest) if SECTION.match(line.strip())), None)
    if head is not None and not any(re.match(r"\s*(?:[-*+]|\d+[.)])\s", line) for line in rest[:head]):
        rest = rest[head:]
    return "\n".join([f"VERDICT: {verdict}"] + rest)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the shape of a squad review (a short checklist).")
    p.add_argument("review", nargs="?", help="the review to check, or - for stdin (not needed with --shape)")
    p.add_argument("--max-lines", type=int, default=0,
                   help="line cap for the whole review (review_max_lines in models.json; 0 = no cap)")
    p.add_argument("--fix-round", action="store_true", help="the answer to a previous review of the same PR: it may"
                   " answer its previous findings in a '## Resolved' section, which a full review may not write")
    p.add_argument("--summary", action="store_true", help="print 'blocking=N debt=N' instead of checking"
                   " (a bullet answered 'fixed' is not a finding, #1832)")
    p.add_argument("--normalize", action="store_true",
                   help="print the review with its verdict canonical on line 1, and check nothing")
    p.add_argument("--shape", action="store_true",
                   help="print the answer shape of this mode (with --fix-round, the fix round's), and check nothing:"
                   " what review.sh embeds in the prompt, verbatim (#1835)")
    p.add_argument("--fingerprint", action="store_true",
                   help="print the fingerprint of the review's findings, the reading the fix pass keeps (ADR-0134)")
    p.add_argument("--stall", nargs=4, metavar=("DIR", "PREFIX", "KEY", "FINGERPRINT"),
                   help="print the failed rounds in a row for that marker set (0 when the findings changed)")
    p.add_argument("--record-stall", nargs=5, metavar=("DIR", "PREFIX", "KEY", "FINGERPRINT", "ROUNDS"),
                   help="keep this round's findings and count for the next pass")
    a = p.parse_args(argv)
    if a.stall:
        print(stall_rounds(*a.stall))
        return 0
    if a.record_stall:
        record_stall(*a.record_stall)
        return 0
    if a.shape:
        print(shape(a.fix_round))
        return 0
    if not a.review:
        p.error("a review is required (or - for stdin), unless --shape, --stall or --record-stall is asked for")
    if a.fingerprint:
        print(findings_fingerprint(sys.stdin.buffer.read() if a.review == "-" else open(a.review, "rb").read()))
        return 0
    text = sys.stdin.read() if a.review == "-" else open(a.review, encoding="utf-8", errors="replace").read()
    if a.normalize:
        print(normalize(text), end="" if text.endswith("\n") else "\n")
        return 0
    if a.summary:
        found = count(text)
        print(" ".join(f"{name.lower()}={found[name]}" for name in SECTIONS))
        return 0
    reason = survey(text, a.max_lines, a.fix_round)
    if reason:
        print(reason)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
