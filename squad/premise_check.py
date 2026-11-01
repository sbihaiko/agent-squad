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

An owner answer rewrites the spec body in place and opens the next round. One answer that defines a term is written
into `GLOSSARY.md`, one that records a durable decision becomes an ADR under `docs/adr/`, and "I don't know" becomes
a probe entry or a recorded open risk, never a settled premise. A question unblocked by an answer is asked in the
next round, and the same refutation repeated with no change in the spec stops the check (ADR-0134).

The findings are the reviewer's judgment and come from a file; this module is the deterministic part:

    uv run squad premise-check <spec> --findings findings.json                          one round, written to the spec
    uv run squad premise-check <spec> --findings findings.json --answers answers.json   the next round, after answers
    uv run squad premise-check <spec> --findings findings.json --dry-run                print the comment, write nothing
    uv run squad premise-check <spec> --findings findings.json --answers answers.json --proxy <model>   a proxy's answers that may stand
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from squad import defaults, modelreg
from squad.record import atomic_write

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "config/models.registry.json"
MODELS = "contrib/squad-goal/scripts/models.json"

MAX_QUESTIONS = 5
STATES = ("proven", "refuted", "needs a probe", "decided", "open")
KINDS = ("fact", "run", "decision")
ANSWER_KINDS = ("decision", "glossary", "adr")
PROXY = "proxy"
PANEL = "panel"
ENDINGS = ("awaiting answers", "awaiting probes", "blocked", "refuted", "open risks", "settled", "stopped")
STALL = "the same refutation repeated with no change in the spec"
READING = "reading"
NOBODY = "—"
MARKER = "<!-- premise-check: spec={spec} -->"
ANSWERS_OPEN = "<!-- premise-answers -->"
ANSWERS_CLOSE = "<!-- /premise-answers -->"
ANSWERS_HEADING = "## Answers"
# Marks an ADR this tool wrote: a round reuses only such an ADR, never an accepted one whose title shares its slug.
ADR_MARKER = "<!-- premise-check-adr -->"
# An answer the owner does not know: it settles nothing and is never read as a settled premise.
UNKNOWN = ("i don't know", "i do not know", "i dont know", "don't know", "do not know", "unknown", "not sure",
           "no idea", "unsure", "idk")

_CITE = re.compile(r"^(?P<path>[^\s:]+):(?P<start>\d+)(?:-(?P<end>\d+))?$")
_STATE = re.compile(r"<!-- premise-check-state: round=(?P<round>\d+) body=(?P<sha>[0-9a-f]+)(?: asked=(?P<asked>.*?))? refuted=(?P<refuted>.*?) -->")
# One answer in an owner's comment: the question's number and the owner's words. A phone reply is `Q1: yes`.
_OWNER_ANSWER = re.compile(r"^\s*\**Q(?P<n>\d+)\**\s*[:.=\-—]\s*(?P<text>.+?)\s*$", re.I)
YES = ("yes", "y", "sim")
_ANSWER_BLOCK = re.compile(r"\n*" + re.escape(ANSWERS_OPEN) + r"(?P<content>.*?)" + re.escape(ANSWERS_CLOSE) + r"\n*", re.S)
_ANSWER_LINE = re.compile(r"^- \*\*(?P<premise>.+?)\*\* — (?P<text>.*?)(?: <!-- answered-by: proxy: (?P<by>[^<>]+) -->)?$")


class NoReviewer(Exception):
    """No model may review this round: none can read the repository, none is in the registry, or the named one is not."""


class NoProxy(Exception):
    """The proxy is the reviewer: a model never answers its own check."""


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
    owner_only: bool = False    # the question touches an owner-only action: a proxy never answers it
    breakdown: bool = False     # the question approves a ticket breakdown: a proxy never approves one

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
                   recommends=str(raw.get("recommends") or ""), depends_on=str(raw.get("depends_on") or ""),
                   owner_only=bool(raw.get("owner_only", False)), breakdown=bool(raw.get("breakdown", False)))


@dataclass(frozen=True)
class Answer:
    """One owner answer: the premise it settles, its text, and where a durable record of it goes."""

    premise: str
    answer: str = ""            # the owner's words for a decision
    answered_by: str = "owner"
    kind: str = "decision"      # decision | glossary | adr
    term: str = ""              # glossary: the term it defines
    definition: str = ""        # glossary: what the term means
    title: str = ""             # adr: the decision's one-line title
    decision: str = ""          # adr: the durable decision it records
    confident: bool = False     # a proxy answer stands only when the proxy is confident

    @property
    def text(self) -> str:
        """The answer as the spec body carries it."""
        if self.kind == "glossary":
            return f"{self.term}: {self.definition}"
        if self.kind == "adr":
            return self.title
        return self.answer

    @property
    def by_proxy(self) -> bool:
        return self.answered_by == PROXY or self.answered_by.startswith(f"{PROXY}: ")

    @property
    def proxy_id(self) -> str:
        return self.answered_by.partition(": ")[2] if self.by_proxy else ""

    @classmethod
    def of(cls, raw) -> "Answer":
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            raise ValueError(f"an answer is an object, not {type(raw).__name__}")
        kind = raw.get("kind", "decision")
        if kind not in ANSWER_KINDS:
            raise ValueError(f"an answer's kind is one of {ANSWER_KINDS}, not {kind!r}")
        premise = str(raw.get("premise") or "").strip()
        if not premise:
            raise ValueError("an answer needs a premise")
        answer = cls(premise=premise, answer=str(raw.get("answer") or ""),
                     answered_by=str(raw.get("answered_by") or "owner"), kind=kind,
                     term=str(raw.get("term") or ""), definition=str(raw.get("definition") or ""),
                     title=str(raw.get("title") or ""), decision=str(raw.get("decision") or ""),
                     confident=bool(raw.get("confident", False)))
        if kind == "decision" and not answer.answer:
            raise ValueError(f"the answer to {premise!r} is empty")
        if kind == "glossary" and not (answer.term and answer.definition):
            raise ValueError(f"the glossary answer to {premise!r} needs a term and a definition")
        if kind == "adr" and not (answer.title and answer.decision):
            raise ValueError(f"the ADR answer to {premise!r} needs a title and a decision")
        line = f"{premise} {answer.text}"
        if "<!--" in line or "-->" in line:
            raise ValueError(f"the answer to {premise!r} carries an HTML comment, which the spec body reserves for its proxy marker")
        return answer


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
    risk: bool = False              # a recorded open risk: the owner does not know, so it is never assumed
    refused: str = ""               # why a proxy's answer to it did not stand this round

    @property
    def asked(self) -> bool:
        return self.question is not None


@dataclass(frozen=True)
class Reviewer:
    id: str
    label: str
    wire: str
    route: str


EX_TEMPFAIL = 75   # a refutation that gh or I/O failed: the loop sends it again; any other refusal is final (#888, ADR-0134)
LOOP = Reviewer(id="squad-loop", label="the squad loop", wire="squad-loop", route="loop")   # who records a probe's refutation


def _repo_read(path: str) -> str:
    """The repository as the round reads it: one file's text, from the checkout the command runs in."""
    return (ROOT / path).read_text()


def checkout_repo(root: Path) -> str:
    """The OWNER/NAME the checkout's origin names, or '' when the checkout has no readable origin."""
    try:
        url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"],
                             check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def artifact_root(repo: str, root=None) -> Path:
    """The checkout of the repository that holds the spec: its durable records are written there, never elsewhere."""
    root = Path(root) if root is not None else ROOT
    named = checkout_repo(root)
    if named and named.lower() != repo.lower():
        raise ValueError(f"{root} holds {named}, not {repo}: the durable records are written in the spec's own checkout")
    return root


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


def unknown(answer: str) -> bool:
    """The owner does not know: the words settle nothing, so they are never read as a decided premise."""
    return " ".join(str(answer).lower().split()).strip(" .!?") in UNKNOWN


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
        elif f.answer and unknown(f.answer):
            if f.proof:
                rows.append(Row(f.premise, "needs a probe", f"probe: {f.proof}", NOBODY, f.recommends))
            else:
                rows.append(Row(f.premise, "open", "open risk: the owner does not know", NOBODY, "", risk=True))
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
        if r.risk:
            out.append(replace(r, held="recorded open risk: the owner does not know, so it is never assumed"))
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


def ending(rows: list[Row]) -> str:
    """The check's end state: it waits for answers or probes, is blocked, records its open risks, or is settled."""
    open_rows = [r for r in rows if r.state == "open" and not r.risk]
    if any(r.asked for r in open_rows):
        return "awaiting answers"
    if open_rows:
        return "blocked"
    if any(r.state == "refuted" for r in rows):    # a refutation outranks a pending probe: the spec is back at the check
        return "refuted"
    if any(r.state == "needs a probe" for r in rows):
        return "awaiting probes"
    if any(r.risk for r in rows):
        return "open risks"
    return "settled"


def check(findings, read: Callable[[str], str] | None = None) -> list[Row]:
    """One round: resolve every finding, then number the questions the owner is asked."""
    parsed = [Finding.of(f) for f in findings]
    depends = {f.premise: f.depends_on for f in parsed if f.depends_on}
    return ask(resolve(parsed, read), depends)


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render(spec: int, round_no: int, reviewer: Reviewer, rows: list[Row], ending: str = "",
           body_sha: str = "", stop_reason: str = "") -> str:
    """The round's one comment: the marker, the reviewer, one table of premise, state, proof or answer, who, its end."""
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
        if r.refused:
            answer = " ".join(x for x in (answer, f"Proxy not taken: {r.refused}.") if x)
        lines.append(f"| {_cell(premise)} | {r.state} | {_cell(answer)} | {_cell(r.answered_by)} |")
    if ending:
        lines += ["", f"**End state:** {ending}."]
    if stop_reason:
        lines += ["", f"**Stopped:** {stop_reason}."]
    if body_sha:
        refuted = json.dumps(sorted(r.premise for r in rows if r.state == "refuted"))
        # The open questions, numbered, so an owner's comment can name one by its number (#886): the table is for a
        # person, and this line is for the reader that folds the answer back into the next round.
        asked = json.dumps([{"n": r.question, "premise": r.premise, "recommends": r.recommends}
                            for r in rows if r.asked], separators=(",", ":"))
        lines += ["", f"<!-- premise-check-state: round={round_no} body={body_sha} asked={asked} refuted={refuted} -->"]
    return "\n".join(lines) + "\n"


def state_of(text: str) -> dict | None:
    """The state a previous round recorded in its comment: the round, the spec body it read, its open questions and its
    refutations. A comment written before the questions were recorded reads as no open question."""
    m = _STATE.search(text or "")
    if not m:
        return None
    asked = json.loads(m["asked"]) if m["asked"] else []
    return {"round": int(m["round"]), "sha": m["sha"], "refuted": json.loads(m["refuted"] or "[]"),
            "asked": {int(q["n"]): q for q in asked}}


_CELL = r"((?:\\.|[^|\\])*)"
_TABLE_ROW = re.compile(rf"^\| {_CELL} \| {_CELL} \| {_CELL} \| {_CELL} \|$")
_QUESTION_PREFIX = re.compile(r"^\*\*Q\d+\*\* ")


def _parse_rows(text: str) -> list[Row]:
    """The rows a previous round's table recorded: premise, state, proof or answer, who answered. A row names a state the round writes."""
    out = []
    for line in (text or "").splitlines():
        hit = _TABLE_ROW.match(line)
        if not hit:
            continue
        premise, state, detail, by = (c.replace("\\|", "|").strip() for c in hit.groups())
        if state not in STATES:
            continue
        premise = _QUESTION_PREFIX.sub("", premise)
        out.append(Row(premise, state, detail, by, risk=detail.startswith("open risk:")))
    return out


def find_comment(github, spec: int) -> dict | None:
    """The check's one comment on the spec, found by its marker, or None before the first round."""
    marker = MARKER.format(spec=spec)
    return next((c for c in github("comments", number=spec) or [] if marker in (c.get("body") or "")), None)


def answers_from_comment(github, spec: int, owner: str) -> tuple[list[Answer], list[tuple[str, str]]]:
    """The owner's answers as written in a comment on the spec (#886): the async mode.

    Only a comment whose author is the owner is read, and only the questions that comment names are answered, so a
    partial answer closes exactly what it answers and leaves the rest open. `Q<n>: yes` accepts the round's
    recommendation for that question; any other words are the answer itself, and words that settle nothing ("I don't
    know") are left to `unknown` by the round. A number that names no open question is refused by name, never dropped
    in silence. The round's own comment is never an answer, whichever account posted it."""
    if not owner:
        return [], []
    marker = MARKER.format(spec=spec)
    comments = github("comments", number=spec) or []
    found = next((c for c in comments if marker in (c.get("body") or "")), None)
    asked = ((state_of((found or {}).get("body") or "") or {}).get("asked") or {})
    answers: dict[str, Answer] = {}
    refused: list[tuple[str, str]] = []
    for c in comments:
        body = c.get("body") or ""
        if marker in body:
            continue
        if ((c.get("user") or {}).get("login") or "").lower() != owner.lower():
            continue
        for line in body.splitlines():
            hit = _OWNER_ANSWER.match(line)
            if not hit:
                continue
            n, text = int(hit["n"]), hit["text"].strip()
            q = asked.get(n)
            if q is None:
                refused.append((f"Q{n}", f"Q{n} is not one of this round's open questions"))
                continue
            if text.lower().strip(" .") in YES and q.get("recommends"):
                text = q["recommends"]
            answers[q["premise"]] = Answer(premise=q["premise"], answer=text, answered_by="owner")   # a later comment wins
    return list(answers.values()), refused


def write_comment(github, spec: int, found: dict | None, body: str) -> str:
    """Write the check on the spec once, rewriting the comment found by its marker in place."""
    if found is None:
        github("comment", number=spec, body=body)
        return "created"
    github("update_comment", comment_id=found["id"], body=body)
    return "updated"


def upsert(github, spec: int, body: str) -> str:
    """Write the check on the spec once: the comment is found by its marker, so a later round rewrites it in place."""
    return write_comment(github, spec, find_comment(github, spec), body)


# --- an owner answer: the spec body, the glossary, the ADRs -------------------------------------------------

def _recorded_answers(body: str) -> list[tuple[str, str, str]]:
    """The answers a previous round wrote into the spec body: premise, text, and the proxy that answered ("" for the owner)."""
    m = _ANSWER_BLOCK.search(body or "")
    if not m:
        return []
    out = []
    for line in m["content"].splitlines():
        hit = _ANSWER_LINE.match(line.strip())
        if hit:
            out.append((hit["premise"].strip(), hit["text"].strip(), (hit["by"] or "").strip()))
    return out


def apply_answers(body: str, answers) -> str:
    """Rewrite the spec body's answers block: an answer already recorded is replaced, a new one appended, the rest kept."""
    answers = [Answer.of(a) for a in answers]
    if not answers:
        return body
    merged = _recorded_answers(body)     # the block is the spec's memory: a later round never drops an earlier answer
    for a in answers:
        for i, (premise, _, _) in enumerate(merged):
            if premise == a.premise:
                merged[i] = (a.premise, a.text, a.proxy_id)
                break
        else:
            merged.append((a.premise, a.text, a.proxy_id))
    base = _ANSWER_BLOCK.sub("\n\n", body or "").rstrip()
    block = [ANSWERS_OPEN, ANSWERS_HEADING, ""] + \
            [f"- **{p}** — {t}" + (f" <!-- answered-by: {PROXY}: {by} -->" if by else "") for p, t, by in merged] + [ANSWERS_CLOSE]
    return base + "\n\n" + "\n".join(block) + "\n"


def merge_answers(findings, answers) -> list[Finding]:
    """Fold each owner answer into the finding it answers, so the next round reads that premise as decided."""
    answers = [Answer.of(a) for a in answers]
    parsed = [Finding.of(f) for f in findings]
    premises = {f.premise for f in parsed}
    for a in answers:
        if a.premise not in premises:
            raise ValueError(f"the answer to {a.premise!r} names a premise no finding carries")
    by_premise = {a.premise: a for a in answers}
    out = []
    for f in parsed:
        a = by_premise.get(f.premise)
        if a and not f.answer:
            f = replace(f, answer=a.text, answered_by=a.answered_by)
        out.append(f)
    return out


def write_glossary(root: Path, answer) -> Path:
    """Write a term the owner defined into GLOSSARY.md, once."""
    answer = Answer.of(answer)
    path = Path(root) / "GLOSSARY.md"
    text = path.read_text() if path.exists() else "# Glossary\n"
    if f"**{answer.term}**:" in text:
        return path
    atomic_write(path, text.rstrip() + f"\n\n**{answer.term}**:\n{answer.definition}\n")
    return path


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "decision"


def _adr_target(d: Path, answer) -> tuple[Path, str]:
    """The ADR this answer writes: the one this tool wrote for the title, or the next free number."""
    slug = _slug(answer.title)
    existing = sorted(d.glob(f"[0-9][0-9][0-9][0-9]-{slug}.md"))
    if existing:
        path = existing[0]
        if ADR_MARKER not in path.read_text():
            raise ValueError(f"docs/adr/{path.name} already records a different decision, and a round never rewrites "
                             "an ADR it did not write")
        return path, path.name[:4]
    numbers = [int(p.name[:4]) for p in d.glob("[0-9][0-9][0-9][0-9]-*.md")]
    number = f"{(max(numbers) + 1) if numbers else 1:04d}"
    return d / f"{number}-{slug}.md", number


def write_adr(root: Path, answer) -> Path:
    """Record a durable decision as an ADR: no date, count, status, issue number or run id, and one index line."""
    answer = Answer.of(answer)
    d = Path(root) / "docs/adr"
    d.mkdir(parents=True, exist_ok=True)
    path, number = _adr_target(d, answer)
    atomic_write(
        path,
        f"# ADR-{number}: {answer.title}\n\n"
        f"{ADR_MARKER}\n\n"
        "## Context\n\n"
        "A spec's premise needed the owner's decision, and the owner answered it.\n\n"
        "## Decision\n\n"
        f"1. {answer.decision}\n\n"
        "## Guard\n\n"
        "None yet. This rule is a draft until a named guard test exists (ADR-0018).\n")
    _index_adr(d, path.name, number, answer.title)
    return path


def _index_adr(d: Path, filename: str, number: str, title: str) -> None:
    """Add the ADR's one line to the index, after the last row, and never twice."""
    readme = d / "README.md"
    text = readme.read_text() if readme.exists() else "# ADR index\n\n| ADR | Rule |\n| --- | --- |\n\n"
    if f"]({filename})" in text:
        return
    lines = text.splitlines()
    last = max((i for i, ln in enumerate(lines) if ln.startswith("| [")), default=None)
    row = f"| [{number}]({filename}) | {title} |"
    if last is None:
        lines += [row]
    else:
        lines.insert(last + 1, row)
    atomic_write(readme, "\n".join(lines) + "\n")


def write_artifacts(root: Path, answers) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Write every durable answer where it belongs: a defined term to the glossary, a decision to an ADR."""
    answers = [Answer.of(a) for a in answers]
    d = Path(root) / "docs/adr"
    for a in answers:                     # preflight: a refused ADR leaves every file as it was
        if a.kind == "adr":
            _adr_target(d, a)
    glossary, adrs = [], []
    for a in answers:
        if a.kind == "glossary":
            glossary.append(write_glossary(root, a))
        elif a.kind == "adr":
            adrs.append(write_adr(root, a))
    return tuple(glossary), tuple(adrs)


@dataclass(frozen=True)
class Report:
    """What one round did: its comment, its end state, whether it applied answers, and where the durable records went."""

    body: str
    ending: str
    round_no: int
    action: str = ""
    stop_reason: str = ""
    applied: bool = False
    rows: tuple[Row, ...] = ()
    glossary: tuple[Path, ...] = ()
    adrs: tuple[Path, ...] = ()
    refused: tuple[tuple[str, str], ...] = ()


def run_round(spec: int, findings, answers=(), *, github, root=None, round_no: int | None = None,
              who: Reviewer, dry_run: bool = False, proxy: Reviewer | None = None) -> Report:
    """One round after the owner's answers: rewrite the spec body, fold the answers in, and record the round."""
    root = Path(root) if root is not None else ROOT
    if proxy is not None and proxy.id == who.id:
        raise NoProxy(f"{proxy.id} is the reviewer, so it never answers its own check")
    answers = [Answer.of(a) for a in answers]
    found, before, body = None, None, ""
    if github is not None:             # a dry run reads the round it would write, and writes nothing
        found = find_comment(github, spec)
        before = state_of((found or {}).get("body") or "")
        body = (github("issue", number=spec) or {}).get("body") or ""
    if round_no is None:
        round_no = (before["round"] + 1) if before else 1
    owner_decided = {premise for premise, _, by in _recorded_answers(body) if not by}
    answers, refused = proxied(answers, findings, proxy, owner_decided)
    rows = [replace(r, refused=refused[r.premise]) if r.premise in refused else r
            for r in check(merge_answers(findings, answers))]
    new_body = apply_answers(body, answers)
    applied = new_body != body
    sha = hashlib.sha256(new_body.encode()).hexdigest()
    refuted = sorted(r.premise for r in rows if r.state == "refuted")
    stop_reason = STALL if before and refuted and before["refuted"] == refuted and before["sha"] == sha else ""
    end = "stopped" if stop_reason else ending(rows)
    if stop_reason:
        rows = [replace(r, question=None, held="not asked: the check stopped") if r.asked else r for r in rows]
    comment = render(spec, round_no, who, rows, ending=end, body_sha=sha, stop_reason=stop_reason)
    if github is None or dry_run:
        return Report(body=comment, ending=end, round_no=round_no, stop_reason=stop_reason,
                      applied=applied, rows=tuple(rows), refused=tuple(refused.items()))
    glossary, adrs = (write_artifacts(root, answers) if answers else ((), ()))
    if applied:
        github("update_issue", number=spec, body=new_body)
    action = write_comment(github, spec, found, comment)
    return Report(body=comment, ending=end, round_no=round_no, action=action, stop_reason=stop_reason,
                  applied=applied, rows=tuple(rows), glossary=glossary, adrs=adrs, refused=tuple(refused.items()))


def refute(spec: int, premise: str, *, github, who: Reviewer, dry_run: bool = False) -> Report:
    """A probe refuted one premise: its row becomes refuted, the state line names it, and the next round is recorded."""
    found = find_comment(github, spec)
    before = state_of((found or {}).get("body") or "")
    if before is None:
        raise ValueError(f"no premise-check comment on spec {spec}")
    rows = _parse_rows((found or {}).get("body") or "")
    if premise in before["refuted"]:   # a retry of a refutation the check already records: a no-op, so the loop may retry
        end = ending(rows)
        return Report(body=(found or {}).get("body") or "", ending=end, round_no=before["round"], action="unchanged", rows=tuple(rows))
    if not any(r.premise == premise and r.state == "needs a probe" for r in rows):
        raise ValueError(f"{premise!r} was never recorded as needs a probe, so no probe settles it")
    asked = {q["premise"]: (n, q) for n, q in before["asked"].items()}
    out = []
    for r in rows:
        if r.premise == premise:
            r = replace(r, state="refuted", detail=f"refuted by {r.detail}")
        elif r.premise in asked and r.state == "open":
            n, q = asked[r.premise]
            r = replace(r, question=n, recommends=q["recommends"])
        out.append(r)
    round_no = before["round"] + 1
    end = ending(out)
    comment = render(spec, round_no, who, out, ending=end, body_sha=before["sha"])
    if dry_run:
        return Report(body=comment, ending=end, round_no=round_no, rows=tuple(out))
    action = write_comment(github, spec, found, comment)
    return Report(body=comment, ending=end, round_no=round_no, action=action, rows=tuple(out))


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


def proxy_named(raw: str, reg=None) -> Reviewer:
    """The proxy the run names: the autonomy panel, or a registry model that can read the repository."""
    if raw == PANEL:
        return Reviewer(id=PANEL, label="autonomy panel", wire=PANEL, route=PANEL)
    return named(raw, reg)


def touches_owner_only(text: str) -> bool:
    """Whether the text names an action on the owner-only list the autonomy fragment carries."""
    norm = " ".join(text.lower().split())
    return any(item.lower() in norm for item in defaults.owner_only())


def _refusal(a: Answer, f: Finding) -> str:
    """Why a proxy may not stand for this answer, or '' when it may."""
    if a.kind != "decision":
        return "a proxy answers only a decision"
    if f.breakdown:
        return "a proxy never approves a breakdown"
    if f.owner_only or touches_owner_only(f"{a.premise} {a.answer}"):
        return "a proxy never answers an owner-only action"
    if not a.confident:
        return "a proxy answers only when it is confident"
    return ""


def proxied(answers, findings, proxy: Reviewer | None, owner_decided=frozenset()) -> tuple[list[Answer], dict[str, str]]:
    """The answers that stand, and the premises whose proxy answer did not, with why: an owner answer always wins."""
    answers = [Answer.of(a) for a in answers]
    if not any(a.by_proxy for a in answers):
        return answers, {}
    if proxy is None:
        raise ValueError("a proxy answer needs --proxy: with no proxy named, every question waits for the owner")
    owned = set(owner_decided) | {a.premise for a in answers if not a.by_proxy}
    gates = {f.premise: f for f in (Finding.of(raw) for raw in findings)}
    out, refused = [], {}
    for a in answers:
        if not a.by_proxy:
            out.append(a)
            continue
        if a.premise not in gates:
            raise ValueError(f"the answer to {a.premise!r} names a premise no finding carries")
        if a.proxy_id not in ("", proxy.id):
            raise ValueError(f"the answer to {a.premise!r} names proxy {a.proxy_id!r}, but this run's proxy is {proxy.id!r}")
        why = "the owner answered this premise" if a.premise in owned else _refusal(a, gates[a.premise])
        if why:
            refused[a.premise] = why
        else:
            out.append(replace(a, answered_by=f"{PROXY}: {proxy.id}"))
    return out, refused


# --- the real GitHub runner --------------------------------------------------------------------------------

def _sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def rest_runner(repo: str, shell=None):
    """The real runner: REST through `gh api`; the ops are the comment path and the issue body an answer rewrites."""
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
        if op == "issue":
            return api("GET", f"issues/{kw['number']}", json_out=True)
        if op == "update_issue":
            return api("PATCH", f"issues/{kw['number']}", [("body", kw["body"])], json_out=True)
        raise ValueError(f"unknown op {op!r}")

    return gh


# --- the command -------------------------------------------------------------------------------------------

def parser():
    p = argparse.ArgumentParser(prog="squad premise-check", description="run one premise-check round on a spec and record it there")
    p.add_argument("spec", type=int, help="the number of the published spec the round checks")
    p.add_argument("--findings", help="the reviewer's findings, a JSON array of objects (required unless --refute)")
    p.add_argument("--refute", metavar="PREMISE", help="a probe refuted this premise: it must be one the check records as needs a probe")
    p.add_argument("--answers", help="the owner's answers, a JSON array of objects; each rewrites the spec and opens the next round")
    p.add_argument("--answers-from-comment", dest="from_comment", action="store_true",
                   help="read the owner's answers from a comment on the spec: only the owner's words, and a partial answer "
                        "closes only the questions it names (#886)")
    p.add_argument("--round", dest="round_no", type=int, default=None, help="the round number; the next one after the recorded round by default")
    p.add_argument("--reviewer", help="the model this round actually ran under: checked against the registry")
    p.add_argument("--proxy", help="the registry model or 'panel' that answers while the owner is away; it must differ from the reviewer")
    p.add_argument("--repo", default="sbihaiko/agent-squad", help="OWNER/NAME of the repository that holds the spec")
    p.add_argument("--root", default=None, help="the checkout that holds the spec's repository; the durable records go there")
    p.add_argument("--dry-run", action="store_true", help="print the comment the round would post, and write nothing")
    return p


# gh stderr that a later call may not repeat: a 5xx, a network failure, a timeout, a rate limit (#888).
_TRANSIENT_GH = re.compile(r"HTTP 5\d\d|connect|time(d ?)?out|rate limit", re.I)


def main(argv=None) -> int:
    p = parser()
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if a.refute is None and a.findings is None:
        p.error("--findings is required unless --refute names a premise")
    try:
        # A refutation is a gate result, so it names the loop and needs no review-chain model (#888).
        who = named(a.reviewer) if a.reviewer else (LOOP if a.refute is not None else reviewer())
        github = rest_runner(a.repo)     # a dry run reads the round it would write, and writes nothing
        refused: list[tuple[str, str]] = []
        if a.refute is not None:
            report = refute(a.spec, a.refute, github=github, who=who, dry_run=a.dry_run)
        else:
            findings = json.loads(Path(a.findings).read_text())
            answers = list(json.loads(Path(a.answers).read_text())) if a.answers else []
            proxy = proxy_named(a.proxy) if a.proxy else None
            root = artifact_root(a.repo, a.root)
            if a.from_comment:           # the async mode (#886): the owner's own comment on the spec is the answer
                said, refused = answers_from_comment(github, a.spec, a.repo.split("/")[0])
                answers += said
            report = run_round(a.spec, findings, answers, github=github, root=root, round_no=a.round_no,
                               who=who, dry_run=a.dry_run, proxy=proxy)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = e.stderr.strip() if isinstance(e.stderr, str) else ""
        if isinstance(e, subprocess.TimeoutExpired) or _TRANSIENT_GH.search(err):   # gh failed: a later call may pass
            print(f"premise-check: transient: {e}" + (f": {err}" if err else ""), file=sys.stderr)
            return EX_TEMPFAIL
        # a 4xx (or any other gh refusal) never changes on a retry: exit 2, the refusal path, never a retry forever (ADR-0134)
        print(f"premise-check: gh refused: {e}" + (f": {err}" if err else ""), file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as e:   # a refutation reads no file, so its I/O and JSON errors are gh's answer
        if a.refute is None and isinstance(e, json.JSONDecodeError):
            print(f"premise-check: {e}", file=sys.stderr)
            return 2
        print(f"premise-check: {'transient: ' if a.refute is not None else ''}{e}", file=sys.stderr)
        return EX_TEMPFAIL if a.refute is not None else 2
    except (ValueError, NoReviewer, NoProxy) as e:
        print(f"premise-check: {e}", file=sys.stderr)
        return 2
    if a.dry_run:
        print(report.body)
        for name, why in refused:
            print(f"premise-check: {name} not read: {why}", file=sys.stderr)
        return 0
    for name, why in refused:
        print(f"premise-check: {name} not read: {why}", file=sys.stderr)
    wrote = ", ".join(str(p) for p in report.glossary + report.adrs)
    print(f"{report.action}: {report.ending}" + (f", stopped — {report.stop_reason}" if report.stop_reason else "")
          + (f"; wrote {wrote}" if wrote else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
