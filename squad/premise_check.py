"""`/premise-check`: one round in the chat turns a published spec's premises into facts, probes and questions.

The reviewer reads the spec and the repository and lists the gaps as findings. The reviewer is a model with tools
from the review chain of the model registry (ADR-0147): a model whose entry says it has no tools cannot read the
repository, so it never reviews. This module holds the rules of a round, so the same findings give the same check:

- a fact whose cited proof the repository carries is `proven`, or `refuted` when the reading contradicts it;
- a fact only running verifies is `needs a probe`;
- a decision the owner already answered is `decided`;
- any other gap is `open`, and the round asks the owner about at most `MAX_QUESTIONS` of them, each with a
  recommended answer that "yes" accepts; a gap that depends on another open gap waits a round.

The round writes ONE comment on the spec, found by its marker and rewritten in place, so a second round replaces
the first instead of adding to it.

The findings are the reviewer's judgment and come from a file; this module is the deterministic part:

    uv run squad premise-check <spec> --findings findings.json                one round, written to the spec
    uv run squad premise-check <spec> --findings findings.json --dry-run      print the comment, write nothing
"""
import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from squad import modelreg

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "config/models.registry.json"
MODELS = "contrib/squad-goal/scripts/models.json"

MAX_QUESTIONS = 5
STATES = ("proven", "refuted", "needs a probe", "decided", "open")
KINDS = ("fact", "run", "decision")
READING = "reading"
NOBODY = "—"
MARKER = "<!-- premise-check: spec={spec} -->"

_CITE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")


class NoReviewer(Exception):
    """No model may review this round: none can read the repository, none is in the registry, or the named one is not."""


@dataclass(frozen=True)
class Finding:
    """One gap the reviewer listed: a premise, how it is settled, and the reviewer's evidence or recommendation."""

    premise: str
    kind: str                   # fact | run | decision
    proof: str = ""             # a citation `path:line` for a fact; the probe for a run-only fact
    holds: bool = True          # for a fact: the proof supports the premise (proven) or contradicts it (refuted)
    answer: str = ""            # a decision the owner already answered
    answered_by: str = ""       # who answered that decision
    recommends: str = ""        # the recommended answer of an open decision; "yes" accepts it
    depends_on: str = ""        # the premise of another gap this one waits on

    @classmethod
    def of(cls, raw) -> "Finding":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            raise ValueError(f"a finding is an object, not {type(raw).__name__}")
        kind = raw.get("kind")
        if kind not in KINDS:
            raise ValueError(f"a finding's kind is one of {KINDS}, not {kind!r}")
        premise = str(raw.get("premise") or "").strip()
        if not premise:
            raise ValueError("a finding needs a premise")
        return cls(premise=premise, kind=kind, proof=str(raw.get("proof") or ""), holds=bool(raw.get("holds", True)),
                   answer=str(raw.get("answer") or ""), answered_by=str(raw.get("answered_by") or ""),
                   recommends=str(raw.get("recommends") or ""), depends_on=str(raw.get("depends_on") or ""))


@dataclass(frozen=True)
class Row:
    """One line of the check: the premise, its state, the proof or the answer, and who answered."""

    premise: str
    state: str
    detail: str = ""
    answered_by: str = ""
    recommends: str = ""
    question: int | None = None     # the number it is asked as this round
    held: str = ""                  # why it is not asked this round

    @property
    def asked(self) -> bool:
        return self.question is not None


@dataclass(frozen=True)
class Reviewer:
    id: str
    label: str
    wire: str
    route: str


def _repo_read(path: str) -> str:
    """The repository as the round reads it: one file's text, from the checkout the command runs in."""
    return (ROOT / path).read_text()


def cited(proof: str, read: Callable[[str], str]) -> str | None:
    """The citation when the repository carries it: the path reads and the cited lines are there and not blank."""
    m = _CITE.match((proof or "").strip())
    if not m:
        return None
    start, end = int(m["start"]), int(m["end"] or m["start"])
    try:
        text = read(m["path"])
    except (OSError, ValueError):
        return None
    lines = text.splitlines()
    if start < 1 or end < start or end > len(lines):
        return None
    if not any(line.strip() for line in lines[start - 1:end]):
        return None
    return m.group(0)


def resolve(findings, read: Callable[[str], str] | None = None) -> list[Row]:
    """One row per finding: what reading proves, what only running proves, what is decided, and what is still open."""
    read = read or _repo_read
    rows = []
    for raw in findings:
        f = Finding.of(raw)
        if f.kind == "run":
            probe = f"probe: {f.proof}" if f.proof else "probe: none named"
            rows.append(Row(f.premise, "needs a probe", probe, NOBODY, f.recommends))
        elif f.kind == "fact":
            cite = cited(f.proof, read)
            if cite is None:
                reason = "no proof yet" if not f.proof else f"the repository does not carry {f.proof}"
                rows.append(Row(f.premise, "open", reason, NOBODY, f.recommends))
            else:
                rows.append(Row(f.premise, "proven" if f.holds else "refuted", f"`{cite}`", READING, f.recommends))
        elif f.answer:
            rows.append(Row(f.premise, "decided", f.answer, f.answered_by or "owner", f.recommends))
        else:
            rows.append(Row(f.premise, "open", "", NOBODY, f.recommends))
    return rows


# `depends_on` lives on the finding, not on the rendered row: `ask` reads it back from the findings it was given.
def ask(rows: list[Row], depends: dict[str, str] | None = None) -> list[Row]:
    """Number the questions this round asks: at most `MAX_QUESTIONS`, and never one that waits on an open gap."""
    depends = depends or {}
    open_premises = {r.premise for r in rows if r.state == "open"}
    out, asked = [], 0
    for r in rows:
        if r.state != "open":
            out.append(r)
            continue
        if not r.recommends:
            out.append(replace(r, held="not asked: it names no recommended answer"))
            continue
        waits_on = depends.get(r.premise, "")
        if waits_on and waits_on in open_premises:
            out.append(replace(r, held=f"not asked: it waits on {waits_on!r}"))
            continue
        if asked >= MAX_QUESTIONS:
            out.append(replace(r, held=f"not asked: a round asks at most {MAX_QUESTIONS} questions"))
            continue
        asked += 1
        out.append(replace(r, question=asked))
    return out


def check(findings, read: Callable[[str], str] | None = None) -> list[Row]:
    """One round: resolve every finding, then number the questions the owner is asked."""
    parsed = [Finding.of(f) for f in findings]
    depends = {f.premise: f.depends_on for f in parsed if f.depends_on}
    return ask(resolve(parsed, read), depends)


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render(spec: int, round_no: int, reviewer: Reviewer, rows: list[Row]) -> str:
    """The round's one comment: the marker, the reviewer, and one table of premise, state, proof or answer, who."""
    lines = [MARKER.format(spec=spec), "",
             f"Premise check, round {round_no}. Reviewer: {reviewer.label} (`{reviewer.wire}`), route `{reviewer.route}`.",
             "",
             "| Premise | State | Proof or answer | Answered by |",
             "| --- | --- | --- | --- |"]
    for r in rows:
        premise = f"**Q{r.question}** {r.premise}" if r.asked else r.premise
        if r.asked:
            answer = f'Recommended answer: {r.recommends}. Answer "yes" to accept it.'
        elif r.held:
            answer = r.held
        else:
            answer = r.detail
        lines.append(f"| {_cell(premise)} | {r.state} | {_cell(answer)} | {_cell(r.answered_by)} |")
    return "\n".join(lines) + "\n"


def upsert(github, spec: int, body: str) -> str:
    """Write the check on the spec once: the comment is found by its marker, so a later round rewrites it in place."""
    marker = MARKER.format(spec=spec)
    found = next((c for c in github("comments", number=spec) or [] if marker in (c.get("body") or "")), None)
    if found is None:
        github("comment", number=spec, body=body)
        return "created"
    github("update_comment", comment_id=found["id"], body=body)
    return "updated"


def named(raw: str, reg=None) -> Reviewer:
    """A reviewer the caller names, checked against the registry: an unknown, retired or no-tools model never reviews."""
    reg = reg or modelreg.load(ROOT / REGISTRY)
    ref = modelreg.normalize(raw, reg)
    entry = reg.entries.get(ref.id)
    if entry is None:
        raise NoReviewer(f"{raw!r} is not in the model registry, so it never reviews")
    if entry.retired:
        raise NoReviewer(f"{entry.id} is retired, so it never reviews")
    route = modelreg.review_route(ref.id, reg)
    if not route.tools:
        raise NoReviewer(f"{entry.id} has no tools, so it cannot read the repository, so it never reviews")
    return Reviewer(id=entry.id, label=entry.label, wire=route.wire, route=route.route)


def reviewer(models=None, reg=None, shared=None, hour=None) -> Reviewer:
    """The reviewer from the registry's review chain: the first entry that can read the repository and may run now."""
    reg = reg or modelreg.load(ROOT / REGISTRY)
    doc = models if models is not None else json.loads((ROOT / MODELS).read_text())
    skipped = []
    for entry in modelreg.chain(doc, "review", reg).entries:
        found = reg.entries.get(entry.id)
        if found is None or found.retired:
            skipped.append(f"{entry.raw} (not in the registry)")
            continue
        route = modelreg.review_route(entry.raw, reg)
        if not route.tools:
            skipped.append(f"{entry.raw} (no tools: it cannot read the repository)")
            continue
        why = modelreg.eligible(entry, shared=shared, hour=hour, reg=reg)
        if not why.ok:
            skipped.append(f"{entry.raw} ({why.reason})")
            continue
        return Reviewer(id=found.id, label=found.label, wire=route.wire, route=route.route)
    raise NoReviewer("no model in the review chain can review now: " + "; ".join(skipped))


# --- the real GitHub runner --------------------------------------------------------------------------------

def _sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def rest_runner(repo: str, shell=None):
    """The real runner: REST through `gh api`; the ops are `comments`, `comment` and `update_comment`."""
    call = shell or _sh

    def api(method, path, fields=(), json_out=False):
        args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"] if method != "GET" else ["gh", "api", f"repos/{repo}/{path}"]
        for k, v in fields:
            args += ["-f", f"{k}={v}"]
        out = call(args)
        return json.loads(out) if json_out else out

    def gh(op, **kw):
        if op == "comments":
            found, page = [], 1
            while True:
                batch = api("GET", f"issues/{kw['number']}/comments?per_page=100&page={page}", json_out=True) or []
                found += batch
                if len(batch) < 100:
                    return found
                page += 1
        if op == "comment":
            return api("POST", f"issues/{kw['number']}/comments", [("body", kw["body"])], json_out=True)
        if op == "update_comment":
            return api("PATCH", f"issues/comments/{kw['comment_id']}", [("body", kw["body"])], json_out=True)
        raise ValueError(f"unknown op {op!r}")

    return gh


# --- the command -------------------------------------------------------------------------------------------

def parser():
    p = argparse.ArgumentParser(prog="squad premise-check", description="run one premise-check round on a spec and record it there")
    p.add_argument("spec", type=int, help="the number of the published spec the round checks")
    p.add_argument("--findings", required=True, help="the reviewer's findings, a JSON array of objects")
    p.add_argument("--round", dest="round_no", type=int, default=1, help="the round number this check is")
    p.add_argument("--reviewer", help="the model this round actually ran under: checked against the registry")
    p.add_argument("--repo", default="sbihaiko/agent-squad", help="OWNER/NAME of the repository that holds the spec")
    p.add_argument("--dry-run", action="store_true", help="print the comment and write nothing")
    return p


def main(argv=None) -> int:
    a = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        findings = json.loads(Path(a.findings).read_text())
        who = named(a.reviewer) if a.reviewer else reviewer()
        rows = check(findings)
    except (OSError, ValueError, NoReviewer) as e:
        print(f"premise-check: {e}", file=sys.stderr)
        return 2
    body = render(a.spec, a.round_no, who, rows)
    if a.dry_run:
        print(body)
        return 0
    print(upsert(rest_runner(a.repo), a.spec, body))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
