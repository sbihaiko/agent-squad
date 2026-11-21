#!/usr/bin/env python3
"""The shape of a squad review (#1777): a short, topic-grouped checklist.

Line 1 is `VERDICT: APPROVE` or `VERDICT: CHANGES` (the exact form the loop parses), then a
`## Blocking` section and/or a `## Debt` section, whichever is non-empty, Blocking first. Under a
section, one bullet per finding, at most two lines: `<file>:<line> — <defect> — <concrete failure>
— <fix>`. A finding about the change as a whole (the branch delivers nothing from its issue, the
commit message misstates it) has no path to cite: its place is the diff, named by a whole-change
label in the location field -- a leading `whole` (`whole diff`, `whole branch`) or the word
`scope` (`Branch scope`, `Issue #249 scope`) (#443), the same room #412 gave a whole-file
concern. A location may name a symbol for its line (`path:symbol`), and an acceptance-criteria finding names
its criterion (`AC1`, `Issue AC ...`) as a whole-change location (#625). Findings that share a theme sit under a `### <theme>` heading, most severe first.
Nothing else: no preamble, no restating the diff, no closing summary.

`review.sh` runs this on every model answer: a review that ignores the shape is a format failure
of that try (the chain moves on and says so), never a silent pass. The loop's own passes ask for
the bullets per section with --summary instead of grepping a tag on the line (ADR-0273 Decision 3/5).
A JSON answer (#632) is rendered before this check, so the render meets every layout rule and only the semantic rules
can fail it: a finding names its place, and APPROVE carries no Blocking finding (#634). The layout rules stay for the
markdown answer, which is still accepted.

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
       revformat.py --classify <review> <sha-file> <tip> [--fix-round]
                                           prints stale|malformed|changes|approves (#1376): the one reading the
                                           landing pass makes, so it never reads a first line or a count
       revformat.py --shape [--fix-round]  prints the answer shape of that mode, and checks nothing (#1835)
       revformat.py --fingerprint <review> prints the fingerprint of the review's findings
       revformat.py --schema               prints the JSON schema of an answer (#632)
       revformat.py --answer [--keep F] <answer>
                                           prints the review: a JSON answer rendered, a markdown one normalized;
                                           exit 1 = a JSON answer off the schema, its reason on stderr
       revformat.py --stall <dir> <prefix> <key> <fingerprint>
                                           prints the failed rounds in a row for that marker set (ADR-0134)
       revformat.py --record-stall <dir> <prefix> <key> <fingerprint> <rounds>
                                           keeps this round's findings and count for the next pass
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

VERDICT = re.compile(r"^VERDICT: (APPROVE|CHANGES)$")
SECTION = re.compile(r"^##\s*(Blocking|Debt|Resolved)\s*$", re.IGNORECASE)
THEME = re.compile(r"^#{3,}\s*(\S.*?)\s*$")
BULLET = re.compile(r"^\s*[-*]\s+(\S.*)$")
# A section with no finding says so, and that is an empty section, not a malformed bullet (#498): the shape already takes a
# bare `VERDICT: APPROVE`, and a model that must write the heading under it writes `None.` there. Only its first content,
# and it may say why on the same line (`None. The four criteria are met.`, #504); a second line is prose again.
EMPTY = re.compile(r"(?i)^(?:none|no findings|nothing|n/?a|nil)(?:[.!]?$|[.:;!]?\s+\S|\s*[—–-]\s+\S)|^[—–-]$")
# ... and the marker may come wrapped, as Opus writes it: `(none)`, `*None*`, `_none_`, `[none]` (#618).
WRAPPED_EMPTY = re.compile(r"(?i)^[(\[*_]+\s*(?:none|no findings|nothing|n/?a|nil)[.!]?\s*[)\]*_]+[.!]?$")
LOC = re.compile(r"^`?(?:[^\s:`]+:~?\d+(?:[-–]\d+)?|[^\s:`]*(?:/|\.[A-Za-z0-9]+)[^\s:`,]*"
                 r"(?::`?[A-Za-z_][\w.]*(?:\(\))?`?)?)`?(?:[\s,;]|$)")  # path:line, path:symbol (#625), or a path alone for a file-level finding; the first field of a bullet (or the second)
# a finding about the change as a whole, not a file (#443): the branch delivers nothing from its issue, the commit
# message misstates it -- there is no path to cite. Its place is the diff itself, and a whole-change label names it:
# a leading "whole" (whole diff / whole branch / the whole change), or the word "scope" (Scope: ... / Branch scope /
# Issue #249 scope). A bullet that still names no place at all fails (#412), so the vague one is not let in.
# An acceptance-criteria finding (#625) names its criterion -- AC1, AC 2, Acceptance criterion 3, Issue AC ... -- the
# review prompt asks for a check against the issue's criteria, and a criterion is a place in the issue, not in a file.
WHOLE = re.compile(r"^`?(?:(?:the\s+)?whole\b|repo-wide\b|.*\bscope\b|(?:issue\s+)?AC\s*\d*\b|acceptance\s+criteri(?:on|a)\b)",
                   re.IGNORECASE)   # "Repo-wide" too (#624)
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


def body_fingerprint(body):
    """The fingerprint of the findings in `body` (a review without its verdict line), the one reading of ADR-0134.

    A line number after a colon and a sha are removed, and whitespace is squeezed to one space, so a moved line or a
    new sha is not a change; SHA-1 of the result, cut to 16 characters.
    """
    body = re.sub(rb":[0-9]+", b":", body)                          # a line number that moved
    body = re.sub(rb"[0-9a-f]{7,40}", b"", body)                    # a sha, short or whole
    body = re.sub(rb"[ \t\n]+", b" ", body)                         # whitespace of any kind, squeezed (tr -s)
    return hashlib.sha1(body).hexdigest()[:16]


def findings_fingerprint(review):
    """The fingerprint of a review's findings: a round that fixed nothing keeps it (ADR-0134). `review` is its bytes.

    Line 1 (the verdict) is dropped and `body_fingerprint` hashes the rest. The bytes are the ones branchfixpass.sh read
    before the reading moved here, so a marker that pass kept still matches.
    """
    body = review.split(b"\n", 1)[1] if b"\n" in review else b""   # tail -n +2: the verdict line is not a finding
    return body_fingerprint(body)


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


def classify(review_path, sha_path, tip, fix_round=False):
    """The four-way reading of a stopped review file, the one the landing pass asks (#1376).

    `stale` is no review file, or a recorded sha that is not the branch tip `tip`; `malformed` is a file outside the
    accepted shape; `changes` is a valid review with at least one Blocking finding; `approves` is a valid review without
    one. The shape is `survey`'s, the one --check enforces, and `fix_round` reads the mode whose grammar has
    `## Resolved`. The answer is one word and never a reason: the pass holds the branch and names the shape itself.

    A file that is missing and a sha that is missing are both `stale`, never `malformed`, so no absent file can be read
    as a review at all. The classification is computed afresh on every pass, so a corrected file at the same tip lands
    on the next pass; nothing is written here.
    """
    try:
        with open(review_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "stale"
    if _marker(sha_path) != tip.strip():
        return "stale"
    text = _legacy_verdict(text)
    if survey(text, 0, fix_round) is not None:
        return "malformed"
    c = count(text)
    # A verdict of CHANGES that lists no bullet at all releases nothing: the reviewer asked for a change and said what
    # for nowhere, which is outside the shape exactly as an APPROVE carrying a Blocking finding is (survey's own rule).
    # The #653 downgrade turns a debt-only CHANGES into an approval, so Debt is what keeps that path open (#1376).
    # The bullets are counted, not the findings: in a fix round every bullet may be answered `fixed` and read as no
    # finding, and that round did answer what the reviewer asked -- only a CHANGES that lists nothing at all is empty.
    if text.split("\n", 1)[0].strip() == "VERDICT: CHANGES" \
            and not any(section in ("Blocking", "Debt") for section, _ in bullets(text)):
        return "malformed"
    return "changes" if c["Blocking"] else "approves"


LEGACY_VERDICT = re.compile(r"^VERDICT: (APPROVE|CHANGES) \(.*\)$")


def _legacy_verdict(text):
    """Line 1 with a note after the verdict, as review.sh wrote it before #1376, read as the bare verdict.

    `review.sh` once wrote `VERDICT: APPROVE (the model wrote CHANGES with only Debt findings)` (#653). The strict line
    is the rule from this change on, and that file is kept under the branch key and never rewritten: reading it as
    malformed would hold a branch that was approved, for ever, with a message telling the owner to correct a file no pass
    will ever correct (#1376).
    """
    first, sep, rest = text.partition("\n")
    m = LEGACY_VERDICT.match(first.strip())
    if not m:
        return text
    return f"VERDICT: {m.group(1)}{sep}{rest}"


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
        if bullet is None and section is not None and section not in held and (EMPTY.match(text) or WRAPPED_EMPTY.match(text)):
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


# #632: a full review answers in JSON with this schema (#627), and `render` writes the markdown above from it, so every
# reader of review-<key>.md works unchanged. The schema is strict (codex --output-schema asks for every key required and
# no other key); `parse_answer` reads it the same way, with no library.
FIELDS = ("location", "defect", "failure", "fix")
# #1521: a Blocking finding names a repro, one shell line that exits non-zero while the defect is present. A finding with
# no repro is Debt (parse_answer); codex wants every key required, so repro is always present and may be empty.
REPRO = "repro"
REPRO_TIMEOUT = 60          # seconds a repro may run; a longer one did not run (#1521)
# #1525: run_repro's status when sandbox-exec could not apply its profile (rc 71, "sandbox-exec: sandbox_apply:" on stderr): a session already under a sandbox (claude-sandboxed.sh) refuses a nested one. The repro never started, so the finding stays Blocking.
REPRO_REFUSED = -71
# #2658: the sandbox executable itself could not start (a host with no sandbox-exec, as the Linux CI runner). The sandbox
# could not apply, so the repro proved nothing and the finding stays Blocking, as for REPRO_REFUSED. A 127 from the shell
# means something else: the sandbox ran, and the repro's own command was not found.
REPRO_NO_SANDBOX = -72
# #1525: SQUAD_REPRO_EXEC names a test seam: a double that takes the same argv (`-p <profile> sh -c <cmd>`), set only by a suite that runs inside a child sandbox.
REPRO_EXEC = "/usr/bin/sandbox-exec"
REPRO_BASE_BUDGET = 120     # seconds all base runs of one answer may take (#2565); a run past it proves nothing
# The signal wall a child session runs under (squad/sandbox.py SIGNAL_PROFILE, #1469). review.sh is not launched with
# SQUAD_SIGNAL_PROFILE set, so the same profile is the default here; a set variable wins.
SIGNAL_PROFILE = "(version 1)(allow default)(deny signal)(allow signal (target self))(allow signal (target same-sandbox))"
_FINDING = {"type": "object", "additionalProperties": False, "required": list(FIELDS) + [REPRO],
            "properties": {f: {"type": "string"} for f in FIELDS + (REPRO,)}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "blocking", "debt", "resolved"],
          "properties": {"verdict": {"type": "string", "enum": ["APPROVE", "CHANGES"]},
                         "blocking": {"type": "array", "items": _FINDING},
                         "debt": {"type": "array", "items": _FINDING},
                         "resolved": {"type": "array", "items": _FINDING}}}
FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.DOTALL)


def json_candidate(text):
    """The JSON object an answer holds, as text, or None when the answer is not a JSON one (a markdown answer).

    The answer may be bare, in a code fence, or after a line of prose; it is a JSON answer when it names "verdict"
    inside braces. The text read is the first fenced block, else the span from the first `{` to the last `}`.
    """
    if '"verdict"' not in text or "{" not in text:
        return None
    fenced = FENCE.search(text)
    if fenced and "{" in fenced.group(1):
        return fenced.group(1).strip()
    start = text.index("{")
    return text[start:text.rindex("}") + 1] if "}" in text[start:] else text[start:]


def parse_answer(text, repro=True):
    """The answer as a dict that matches SCHEMA. Raises ValueError with the parser's reason when it does not.

    A missing list is an empty list, and a verdict in another case is read in upper case. Every finding has the four
    fields as non-empty strings and a repro string that may be empty. A Blocking finding with a blank repro is Debt
    (#1521), unless `repro` is False: a plan review reads no code, so it names no repro and none is demoted for it
    (ADR-0171). Nothing else is checked here: the semantic rules stay with `survey`.
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"the answer is not JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError("the answer is not a JSON object")
    verdict = obj.get("verdict")
    if not isinstance(verdict, str) or verdict.strip().upper() not in ("APPROVE", "CHANGES"):
        raise ValueError(f'verdict is {verdict!r}, not "APPROVE" or "CHANGES"')
    out = {"verdict": verdict.strip().upper()}
    for key in ("blocking", "debt", "resolved"):
        items = obj.get(key) or []
        if not isinstance(items, list):
            raise ValueError(f"{key} is not a list")
        for n, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"{key}[{n}] is not an object")
            for f in FIELDS:
                if not isinstance(item.get(f), str) or not item[f].strip():
                    raise ValueError(f"{key}[{n}].{f} is missing or empty")
            if not isinstance(item.get(REPRO), str):
                raise ValueError(f"{key}[{n}].{REPRO} is missing or not a string")
        out[key] = items
    if repro:
        demoted, kept = [], []
        for item in out["blocking"]:
            if not item[REPRO].strip():
                demoted.append(item)                       # a finding with no repro is Debt (ADR-0172)
            else:
                kept.append(item)
        out["debt"] = out["debt"] + demoted
        out["blocking"] = kept
    return out


def write_wall(tree, scratch):
    """The SBPL rules that refuse every file write except in `tree` and `scratch` (#1521), and the null devices a shell
    redirects to. A reviewer-written repro is a shell line, so the signal wall alone leaves it the whole filesystem. The
    paths are resolved first: SBPL matches the real path, and a temp dir is under /var, a link to /private/var.
    """
    def sub(p):
        real = os.path.realpath(p).replace("\\", "\\\\").replace('"', '\\"')
        return f'(subpath "{real}")'
    return ("(deny file-write*)(allow file-write* " + sub(tree) + " " + sub(scratch)
            + ' (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/fd/") (regex #"^/dev/std(out|err)$"))')


def repro_env(scratch):
    """The environment a repro runs in: the gate's own (#2647). The checkout's venv comes first on PATH, when
    $SG_CHECKOUT holds one, and pytest carries -p no:deepeval, so a repro that names a test the gate passes runs it the
    way the gate does instead of dying at the plugin's startup."""
    env = dict(os.environ, TMPDIR=scratch)
    venv = os.path.join(env.get("SG_CHECKOUT", ""), ".venv", "bin")
    if env.get("SG_CHECKOUT") and os.path.isdir(venv):
        env["PATH"] = venv + os.pathsep + env.get("PATH", "")
    addopts = env.get("PYTEST_ADDOPTS", "")
    if "no:deepeval" not in addopts:
        env["PYTEST_ADDOPTS"] = (addopts + " -p no:deepeval").strip()
    return env


def demote_markdown(path):
    """Move the Blocking findings of a markdown review to Debt (#2647, ADR-0172): a markdown answer names no repro, so
    none of its findings is proven. Each moved bullet ends with the reason; the file is rewritten atomically. The
    bullets go after the Debt header when there is one; with none, the Blocking header becomes the Debt header."""
    lines = open(path, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    heads = [(i, m.group(1).lower()) for i, l in enumerate(lines) if (m := SECTION.match(l.strip()))]
    blk = next((i for i, n in heads if n == "blocking"), None)
    if blk is None:
        return 0
    end = next((i for i, _ in heads if i > blk), len(lines))
    body = []
    for l in lines[blk + 1:end]:
        if BULLET.match(l) and is_finding("Blocking", BULLET.match(l).group(1)):
            l = l.rstrip() + " — no repro: markdown answer"
        body.append(l)
    while body and not body[-1].strip():
        body.pop()
    rest = lines[:blk] + lines[end:]
    debt = next((i for i, l in enumerate(rest) if (m := SECTION.match(l.strip())) and m.group(1).lower() == "debt"), None)
    if debt is None:
        out = lines[:blk] + ["## Debt"] + body + lines[end:]
    else:
        out = rest[:debt + 1] + [l for l in body if l.strip()] + rest[debt + 1:]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.replace(tmp, path)
    return sum(1 for l in body if l.endswith("no repro: markdown answer"))


def run_repro(cmd, checkout, timeout=None):
    """The exit status of a repro command, run by sh under the signal wall in `checkout` (#1521): 124 when it timed out
    (its process group is killed with it), 127 when the repro's command could not start, REPRO_REFUSED when the sandbox
    could not apply (a nested sandbox, #1525), REPRO_NO_SANDBOX when the sandbox executable cannot start at all
    (a host with no sandbox-exec, #2658). `timeout` caps the run below REPRO_TIMEOUT (a base run, #2565). The wall is the
    profile in SQUAD_SIGNAL_PROFILE,
    else SIGNAL_PROFILE: the one a child session runs under (claude-sandboxed.sh). A write wall (write_wall) refuses a
    write outside `checkout` and a scratch dir the repro's TMPDIR points at; the scratch dir is removed after the run.
    """
    scratch = tempfile.mkdtemp(prefix="repro-1521-")
    try:
        profile = (os.environ.get("SQUAD_SIGNAL_PROFILE") or SIGNAL_PROFILE) + write_wall(checkout, scratch)
        env = repro_env(scratch)
        exe = os.environ.get("SQUAD_REPRO_EXEC") or REPRO_EXEC
        # #1525: the repro's stderr is a file in the scratch dir, so the write wall allows it and sandbox-exec's refusal is readable.
        with open(os.path.join(scratch, "stderr"), "w+b") as err:
            try:
                proc = subprocess.Popen([exe, "-p", profile, "sh", "-c", cmd],
                                        cwd=checkout, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=err, start_new_session=True)
            except OSError:   # the sandbox executable itself is not on this host: nothing ran, so nothing is proven (#2658)
                return REPRO_NO_SANDBOX
            try:
                rc = proc.wait(timeout=REPRO_TIMEOUT if timeout is None else timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return 124
            err.seek(0)
            head = err.read(256)
        if rc == 71 and head.startswith(b"sandbox-exec: sandbox_apply:"):
            return REPRO_REFUSED
        return rc
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def repro_key(cmd):
    """A repro with its whitespace normalized: two rounds name the same repro when their keys are equal (#2565)."""
    return " ".join(cmd.split())


def prev_repros(prev):
    """The repro keys of the Blocking findings of the previous answer `prev` (ANSWER, SHA), and its head SHA (#2565). A
    previous answer that cannot be read holds none, so no finding is demoted for a repeat."""
    if not prev:
        return set(), None
    try:
        with open(prev[0], encoding="utf-8") as f:
            return {repro_key(i[REPRO]) for i in parse_answer(f.read())["blocking"]}, prev[1]
    except (OSError, ValueError) as exc:
        print(f"revformat: the previous answer {prev[0]} is not readable ({exc}); no finding is a repeat", file=sys.stderr)
        return set(), None


def repro_check(path, checkout, sha, base=None, prev=None):
    """Run each Blocking finding's repro at `sha` in `checkout`, and rewrite the answer at `path` (#1521).

    A repro that exits 0 shows the defect is not present, so the finding moves to Debt with "repro passed at <sha>". A
    repro that cannot run (124, 126, 127) moves it to Debt with "repro did not run at <sha>". Any other non-zero status
    is a reproduction, and the finding stays Blocking. The rewrite is atomic; a failed read or write leaves the answer
    as it was, and the caller keeps its verdict. A repro whose sandbox cannot apply (REPRO_REFUSED) or cannot start
    (REPRO_NO_SANDBOX) proved nothing: the finding stays Blocking and the reason is printed.

    #2565: with `base` (TREE, SHA), a repro red at the head also runs at the base, in TREE, within REPRO_BASE_BUDGET for
    all base runs, each capped at the budget left. Green at the base: the finding stays Blocking. Red at the base: it stays Blocking with "repro also fails
    at base <sha>", unless the previous answer `prev` (ANSWER, SHA) holds the same repro in its Blocking findings and SHA
    is another head; then it moves to Debt, naming the base and both heads. A base run that proves nothing keeps the finding Blocking, and the
    reason is printed.
    """
    with open(path, encoding="utf-8") as f:
        answer = parse_answer(f.read())
    seen, prev_sha = prev_repros(prev)
    blocking, spent = [], 0.0
    for item in answer["blocking"]:
        rc = run_repro(item[REPRO], checkout)
        defect = " ".join(item["defect"].split())
        if rc == REPRO_REFUSED:
            print(f"revformat: repro sandbox refused at {sha} (sandbox-exec exit 71, sandbox_apply: a nested sandbox): "
                  f"{defect}; the finding stays Blocking", file=sys.stderr)
            blocking.append(item)
            continue
        if rc == REPRO_NO_SANDBOX:
            print(f"revformat: the repro sandbox cannot start at {sha} (the sandbox executable is not on this host): "
                  f"{defect}; the finding stays Blocking", file=sys.stderr)
            blocking.append(item)
            continue
        note = {0: "repro passed at", 124: "repro did not run at", 126: "repro did not run at",
                127: "repro did not run at"}.get(rc)
        if note is None:
            if base is None:
                blocking.append(item)
                continue
            btree, bsha = base
            why = None
            if not os.path.isdir(btree):
                why = f"no base tree {btree}"
            elif spent >= REPRO_BASE_BUDGET:
                why = f"the base budget of {REPRO_BASE_BUDGET} s is spent"
            else:
                t0 = time.monotonic()
                brc = run_repro(item[REPRO], btree, min(REPRO_TIMEOUT, REPRO_BASE_BUDGET - spent))
                spent += time.monotonic() - t0
                if brc == 0:
                    blocking.append(item)
                    continue
                if brc == REPRO_REFUSED:
                    why = "repro sandbox refused (sandbox-exec exit 71)"
                elif brc == REPRO_NO_SANDBOX:
                    why = "the repro sandbox cannot start"
                elif brc in (124, 126, 127):
                    why = f"the repro did not run (exit {brc})"
                elif brc < 0:
                    why = f"the repro was killed by signal {-brc}"
            if why:
                print(f"revformat: no base run proves anything at {bsha} ({why}): {defect}; the finding stays Blocking",
                      file=sys.stderr)
                blocking.append(item)
            elif prev_sha and prev_sha != sha and repro_key(item[REPRO]) in seen:
                reason = f"repro fails at base {bsha} and at heads {prev_sha}, {sha}"
                print(f"revformat: {reason}: {defect}; the finding moves to Debt", file=sys.stderr)
                answer["debt"].append(dict(item, failure=f"{reason}; {item['failure']}"))
            else:
                blocking.append(dict(item, failure=f"repro also fails at base {bsha}; {item['failure']}"))
            continue
        print(f"revformat: {note} {sha}: {defect}", file=sys.stderr)
        answer["debt"].append(dict(item, failure=f"{note} {sha}; {item['failure']}"))
    answer["blocking"] = blocking
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(answer, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def _bullet(item):
    """One finding as a bullet: the four fields, then `repro: <command>` when the finding names one (#1521)."""
    fields = [" ".join(item[f].split()) for f in FIELDS]
    if item[REPRO].strip():
        fields.append("repro: " + " ".join(item[REPRO].split()))
    return "- " + " — ".join(fields)


def render(answer):
    """The markdown review of a parsed answer: line 1 `VERDICT: ...`, then each non-empty section, most severe first,
    one bullet per finding, `<location> — <defect> — <failure> — <fix>` on one line. The line breaks in a field become
    one space. The text ends with a newline, as a model's answer does, so the fingerprint is the one of the equivalent
    markdown answer (ADR-0134).
    """
    lines = [f"VERDICT: {answer['verdict']}"]
    for key, name in (("blocking", SECTIONS[0]), ("debt", SECTIONS[1]), ("resolved", ANSWER)):
        if answer.get(key):
            lines.append(f"## {name}")
            lines += [_bullet(item) for item in answer[key]]
    return "\n".join(lines) + "\n"


def round_review(onto, ref, files):
    """The loop's own CHANGES review that asks one rebase round (#1960): the one writer for wave.sh and branchland.sh.

    `onto` is the branch the rebase conflicted with, `ref` the ref the round rebases on, `files` the conflicting paths
    (blank or unreadable: none named). It is rendered as any answer is, so the landing pass classifies it as
    changes. Its repro (ADR-0172) is a trial merge of the head with `ref`: it exits non-zero while they conflict.
    """
    paths = [p for p in files.split() if p != "(unreadable)"]
    item = {"location": "whole branch",   # a bare file name (Makefile, g) is no location the shape reads, so the paths go in the defect
            "defect": f"the rebase on {onto} conflicts in {', '.join(paths) if paths else 'no path named by git'}",
            "failure": f"the branch cannot land on {onto} until each conflicting file is resolved",
            "fix": f"Rebase this branch on {ref}, resolve the conflict in each file, keep the work, and push.",
            REPRO: f"git merge-tree --write-tree HEAD {ref} >/dev/null"}
    return render({"verdict": "CHANGES", "blocking": [item], "debt": [], "resolved": []})


def answer_review(text, keep=None, plan=False):
    """The review of a model answer. A JSON answer is parsed and rendered, and its raw JSON is written to `keep`; a
    markdown answer is normalized as before. Raises ValueError for a JSON answer off the schema. `plan` is a plan
    review (ADR-0171): its findings name no repro, so a blank one is not a reason to demote."""
    raw = json_candidate(text)
    if raw is None:
        return normalize(text)
    answer = parse_answer(raw, repro=not plan)
    if keep:
        with open(keep, "w", encoding="utf-8") as f:
            json.dump(json.loads(raw), f, ensure_ascii=False, indent=1)
            f.write("\n")
    return render(answer)


def main(argv=None):
    p = argparse.ArgumentParser(description="Check the shape of a squad review (a short checklist).")
    p.add_argument("review", nargs="?", help="the review to check, or - for stdin (not needed with --shape)")
    p.add_argument("--max-lines", type=int, default=0,
                   help="line cap for the whole review (review_max_lines in models.json; 0 = no cap)")
    p.add_argument("--fix-round", action="store_true", help="the answer to a previous review of the same PR: it may"
                   " answer its previous findings in a '## Resolved' section, which a full review may not write")
    p.add_argument("--summary", action="store_true", help="print 'blocking=N debt=N' instead of checking"
                   " (a bullet answered 'fixed' is not a finding, #1832)")
    p.add_argument("--classify", nargs=3, metavar=("REVIEW", "SHA-FILE", "TIP"),
                   help="print one word for a review file: stale (missing, or another tip's), malformed (outside the"
                        " shape), changes (valid with a Blocking finding) or approves (valid without one) (#1376)")
    p.add_argument("--normalize", action="store_true",
                   help="print the review with its verdict canonical on line 1, and check nothing")
    p.add_argument("--shape", action="store_true",
                   help="print the answer shape of this mode (with --fix-round, the fix round's), and check nothing:"
                   " what review.sh embeds in the prompt, verbatim (#1835)")
    p.add_argument("--schema", action="store_true", help="print the JSON schema of an answer, and check nothing (#632)")
    p.add_argument("--answer", action="store_true", help="print the review of a model answer: JSON rendered, markdown"
                   " normalized; exit 1 with the parser's reason on stderr for JSON off the schema (#632)")
    p.add_argument("--keep", metavar="FILE", help="with --answer, write the raw JSON answer there")
    p.add_argument("--plan", action="store_true", help="with --answer, the answer is a plan review (ADR-0171): a Blocking"
                   " finding with a blank repro stays Blocking, since a plan has no code to run one on (#1521)")
    p.add_argument("--repro", nargs=3, metavar=("ANSWER", "CHECKOUT", "SHA"),
                   help="run each Blocking finding's repro at SHA in CHECKOUT and rewrite the JSON ANSWER: a finding whose"
                   " repro passes or cannot run moves to Debt; one whose sandbox cannot apply stays Blocking (#1525)")
    p.add_argument("--repro-base", nargs=2, metavar=("TREE", "SHA"),
                   help="with --repro, run a repro red at the head also at the base SHA in TREE (#2565): red there keeps the"
                   " finding Blocking with a base note, or moves it to Debt when --repro-prev holds the same repro")
    p.add_argument("--repro-prev", nargs=2, metavar=("ANSWER", "SHA"),
                   help="with --repro-base, the previous round's JSON ANSWER and its head SHA (#2565)")
    p.add_argument("--demote-markdown", metavar="FILE", help="move the Blocking findings of a markdown review to Debt,"
                   " each with the reason 'no repro: markdown answer', in place (#2647, ADR-0172)")
    p.add_argument("--fingerprint", action="store_true",
                   help="print the fingerprint of the review's findings, the reading the fix pass keeps (ADR-0134)")
    p.add_argument("--stall", nargs=4, metavar=("DIR", "PREFIX", "KEY", "FINGERPRINT"),
                   help="print the failed rounds in a row for that marker set (0 when the findings changed)")
    p.add_argument("--record-stall", nargs=5, metavar=("DIR", "PREFIX", "KEY", "FINGERPRINT", "ROUNDS"),
                   help="keep this round's findings and count for the next pass")
    p.add_argument("--round-review", nargs=3, metavar=("ONTO", "REF", "FILES"),
                   help="print the loop's CHANGES review that asks one rebase round on REF after a conflict with ONTO in"
                   " FILES (#1960): the one writer of that review")
    a = p.parse_args(argv)
    if a.round_review:
        print(round_review(*a.round_review), end="")
        return 0
    if a.stall:
        print(stall_rounds(*a.stall))
        return 0
    if a.record_stall:
        record_stall(*a.record_stall)
        return 0
    if a.classify:
        print(classify(*a.classify, fix_round=a.fix_round))
        return 0
    if (a.repro_base and not a.repro) or (a.repro_prev and not a.repro_base):
        p.error("--repro-base needs --repro, and --repro-prev needs --repro-base")
    if a.demote_markdown:
        moved = demote_markdown(a.demote_markdown)
        if moved:
            print(f"revformat: {moved} Blocking finding(s) of a markdown answer move to Debt: no repro", file=sys.stderr)
        return 0
    if a.repro:
        repro_check(*a.repro, base=a.repro_base, prev=a.repro_prev)
        return 0
    if a.shape:
        print(shape(a.fix_round))
        return 0
    if a.schema:
        print(json.dumps(SCHEMA, separators=(",", ":")))
        return 0
    if not a.review:
        p.error("a review is required (or - for stdin), unless --shape, --schema, --stall or --record-stall is asked for")
    if a.fingerprint:
        print(findings_fingerprint(sys.stdin.buffer.read() if a.review == "-" else open(a.review, "rb").read()))
        return 0
    text = sys.stdin.read() if a.review == "-" else open(a.review, encoding="utf-8", errors="replace").read()
    if a.answer:
        try:
            review = answer_review(text, a.keep, plan=a.plan)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(review, end="" if review.endswith("\n") else "\n")
        return 0
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
