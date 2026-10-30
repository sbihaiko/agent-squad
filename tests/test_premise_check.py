"""Guard for ADR-0155: `/premise-check` runs one round in the chat and records the check on the spec.

The reviewer is a stub here. A round resolves what reading proves, sends what only running proves to a probe, and
asks the owner at most five questions, each with a recommended answer that "yes" accepts. The result is one comment
on the spec, found by its marker and rewritten in place.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from squad import modelreg, record
from squad import premise_check as pc

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config/models.registry.json"
MODELS = ROOT / "contrib/squad-goal/scripts/models.json"

REPO = {
    "squad/module.py": "line one\nline two\nthe registry decides the route\nline four\n",
    "docs/design/flow.md": "# Flow\n\nThe loop holds one run.\n",
}


def repo_read(tmp: Path):
    """The repository reader of a test: the file's text, or FileNotFoundError, exactly as the module calls it."""
    for rel, text in REPO.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def read(rel: str) -> str:
        return (tmp / rel).read_text()

    return read


def rows_of(tmp_path, *findings, **kw):
    return pc.check([pc.Finding.of(f) for f in findings], read=repo_read(tmp_path), **kw)


def one_fact(**kw):
    base = {"premise": "the registry decides the route", "kind": "fact", "proof": "squad/module.py:3"}
    base.update(kw)
    return base


def reviewer():
    return pc.Reviewer(id="claude-opus-5-5", label="Claude Opus 5.5", wire="claude-opus-5-5", route="cli")


def body_of(rows, spec=878, round_no=1):
    return pc.render(spec=spec, round_no=round_no, reviewer=reviewer(), rows=rows)


# --- what reading proves is never a question ---------------------------------------------------------------

def test_a_fact_with_a_cited_proof_is_proven_and_never_asked(tmp_path):
    rows = rows_of(tmp_path, one_fact(recommends="accept it"))
    assert [r.state for r in rows] == ["proven"]
    assert rows[0].question is None
    assert rows[0].detail == "`squad/module.py:3`"
    assert rows[0].answered_by == "reading"
    body = body_of(rows)
    assert "**Q1**" not in body and "Recommended answer" not in body


def test_a_proof_the_repository_does_not_carry_leaves_the_fact_open(tmp_path):
    rows = rows_of(tmp_path, one_fact(proof="squad/module.py:99", recommends="accept it"))
    assert [r.state for r in rows] == ["open"]
    assert rows[0].question == 1


def test_a_citation_to_a_missing_file_is_no_proof(tmp_path):
    rows = rows_of(tmp_path, one_fact(proof="squad/gone.py:1", recommends="accept it"))
    assert [r.state for r in rows] == ["open"]


def test_reading_that_contradicts_the_premise_refutes_it_and_asks_nothing(tmp_path):
    rows = rows_of(tmp_path, one_fact(holds=False, recommends="accept it"))
    assert [r.state for r in rows] == ["refuted"]
    assert rows[0].question is None


# --- what only running proves goes to a probe --------------------------------------------------------------

def test_a_run_only_fact_becomes_needs_a_probe(tmp_path):
    rows = rows_of(tmp_path, {"premise": "the wave lands in under ten minutes", "kind": "run",
                              "proof": "run the default workflow once and read the run record", "recommends": "accept it"})
    assert [r.state for r in rows] == ["needs a probe"]
    assert rows[0].question is None
    assert "run the default workflow once" in rows[0].detail
    assert "| needs a probe |" in body_of(rows)


# --- a decision is a question, at most five, with a recommendation -----------------------------------------

def test_a_decision_the_owner_answered_is_decided_and_not_asked(tmp_path):
    rows = rows_of(tmp_path, {"premise": "one comment per spec", "kind": "decision",
                              "answer": "one comment, rewritten in place", "answered_by": "owner"})
    assert [r.state for r in rows] == ["decided"]
    assert rows[0].question is None
    assert rows[0].answered_by == "owner"


def test_a_question_that_depends_on_an_open_question_waits_a_round(tmp_path):
    rows = rows_of(
        tmp_path,
        {"premise": "the check comments on the spec", "kind": "decision", "recommends": "comment on the spec"},
        {"premise": "the comment carries a table", "kind": "decision", "recommends": "one table", "depends_on": "the check comments on the spec"},
        {"premise": "five questions per round", "kind": "decision", "recommends": "five", "depends_on": "the registry decides the route"},
        one_fact(),
    )
    by = {r.premise: r for r in rows}
    assert by["the check comments on the spec"].question == 1
    assert by["the comment carries a table"].question is None
    assert "waits on" in by["the comment carries a table"].held
    assert by["five questions per round"].question == 2      # its dependency is proven, so it is asked
    assert [r.premise for r in rows if r.question] == ["the check comments on the spec", "five questions per round"]


def test_a_round_asks_at_most_five_questions(tmp_path):
    findings = [{"premise": f"decision {i}", "kind": "decision", "recommends": f"answer {i}"} for i in range(7)]
    rows = rows_of(tmp_path, *findings)
    asked = [r for r in rows if r.question]
    assert [r.question for r in asked] == [1, 2, 3, 4, 5]
    assert all("not asked" in r.held for r in rows if not r.question)


def test_a_gap_with_no_recommendation_is_never_asked(tmp_path):
    rows = rows_of(tmp_path, {"premise": "the format", "kind": "decision"})
    assert rows[0].state == "open" and rows[0].question is None
    assert "recommended" in rows[0].held


def test_there_are_five_states_and_no_other(tmp_path):
    assert pc.STATES == ("proven", "refuted", "needs a probe", "decided", "open")
    rows = rows_of(
        tmp_path,
        one_fact(),
        one_fact(holds=False),
        {"premise": "the wave lands in under ten minutes", "kind": "run", "proof": "run the default workflow once"},
        {"premise": "one comment per spec", "kind": "decision", "answer": "one comment", "answered_by": "owner"},
        {"premise": "the format", "kind": "decision", "recommends": "a table"},
    )
    assert [r.state for r in rows] == list(pc.STATES)


# --- one comment, rewritten in place, one round ------------------------------------------------------------

class StubGithub:
    def __init__(self):
        self.calls = []
        self.comments = {}

    def __call__(self, op, **kw):
        self.calls.append(op)
        if op == "comments":
            return [{"id": i, "body": b} for i, b in self.comments.items()]
        if op == "comment":
            self.comments[len(self.comments) + 1] = kw["body"]
            return {"id": len(self.comments)}
        if op == "update_comment":
            self.comments[kw["comment_id"]] = kw["body"]
            return {"id": kw["comment_id"]}
        raise AssertionError(op)


def test_the_check_comment_is_written_once_and_rewritten_in_place(tmp_path):
    gh = StubGithub()
    first = body_of(rows_of(tmp_path, one_fact()), round_no=1)
    second = body_of(rows_of(tmp_path, {"premise": "the format", "kind": "decision", "recommends": "a table"}), round_no=2)
    assert pc.upsert(gh, 878, first) == "created"
    assert pc.upsert(gh, 878, second) == "updated"
    assert gh.calls == ["comments", "comment", "comments", "update_comment"]
    assert len(gh.comments) == 1 and first not in gh.comments.values() and second in gh.comments.values()


def test_the_marker_names_the_spec_so_another_spec_gets_its_own_comment(tmp_path):
    gh = StubGithub()
    pc.upsert(gh, 878, body_of(rows_of(tmp_path, one_fact()), spec=878))
    pc.upsert(gh, 900, body_of(rows_of(tmp_path, one_fact()), spec=900))
    assert len(gh.comments) == 2 and gh.calls == ["comments", "comment", "comments", "comment"]


def test_one_invocation_is_one_round_that_runs_no_probe_and_answers_nothing(tmp_path):
    import inspect

    assert list(inspect.signature(pc.check).parameters) == ["findings", "read"]
    gh = StubGithub()
    rows = rows_of(tmp_path, {"premise": "the wave lands in under ten minutes", "kind": "run",
                              "proof": "run the default workflow once"})
    assert pc.upsert(gh, 878, body_of(rows)) == "created"
    assert gh.calls.count("comment") + gh.calls.count("update_comment") == 1
    assert rows[0].state == "needs a probe" and "probe: run the default workflow once" in body_of(rows)


def test_the_comment_is_one_table_with_the_four_columns_and_who_answered(tmp_path):
    rows = rows_of(
        tmp_path,
        one_fact(),
        {"premise": "the wave lands in under ten minutes", "kind": "run", "proof": "run the default workflow once"},
        {"premise": "one comment per spec", "kind": "decision", "answer": "one comment, rewritten in place", "answered_by": "owner"},
        {"premise": "the format", "kind": "decision", "recommends": "a table"},
    )
    body = body_of(rows)
    assert "<!-- premise-check: spec=878 -->" in body
    assert "| Premise | State | Proof or answer | Answered by |" in body
    for state in ("| proven |", "| needs a probe |", "| decided |", "| open |"):
        assert state in body, state
    assert "| owner |" in body and "| reading |" in body
    assert 'Recommended answer: a table. Answer "yes" to accept it.' in body
    assert body.count("| Premise |") == 1


# --- the reviewer is a model with tools from the registry --------------------------------------------------

def _registry_marking_opus_no_tools(tmp_path):
    doc = json.loads(REGISTRY.read_text())
    for m in doc["models"]:
        if m["id"] == "claude-opus-5-5":
            m["capabilities"]["tools"] = False
    p = tmp_path / "registry.json"
    p.write_text(json.dumps(doc))
    return modelreg.load(p)


def _chain_doc(*ids):
    return {"schema_version": 2, "chains": {"review": [{"id": i} for i in ids]}}


def test_a_model_that_cannot_read_the_repository_never_reviews(tmp_path):
    reg = _registry_marking_opus_no_tools(tmp_path)
    who = pc.reviewer(models=_chain_doc("claude-opus-5-5", "gemini-3.8-flash"), reg=reg, shared=tmp_path)
    assert who.id == "gemini-3.8-flash"
    assert who.wire == modelreg.review_route("gemini-3.8-flash", reg).wire
    assert who.route == "cli" and who.label == reg.entries["gemini-3.8-flash"].label


def test_a_review_chain_of_no_tools_models_has_no_reviewer(tmp_path):
    reg = _registry_marking_opus_no_tools(tmp_path)
    with pytest.raises(pc.NoReviewer) as e:
        pc.reviewer(models=_chain_doc("claude-opus-5-5"), reg=reg, shared=tmp_path)
    assert "cannot read the repository" in str(e.value)


def test_a_named_reviewer_is_checked_against_the_registry(tmp_path):
    reg = _registry_marking_opus_no_tools(tmp_path)
    with pytest.raises(pc.NoReviewer):
        pc.named("claude-opus-5-5", reg=reg)
    with pytest.raises(pc.NoReviewer):
        pc.named("gpt-5.5", reg=reg)                     # retired
    with pytest.raises(pc.NoReviewer):
        pc.named("no-such-model", reg=reg)               # not in the registry
    assert pc.named("claude-sonnet-5-5", reg=reg).id == "claude-sonnet-5-5"


def test_the_shipped_chain_picks_a_registry_entry_with_tools(tmp_path):
    reg = modelreg.load(REGISTRY)
    who = pc.reviewer(models=json.loads(MODELS.read_text()), reg=reg, shared=tmp_path)
    entry = reg.entries[who.id]
    assert entry.retired is False and entry.capabilities.get("tools") is True
    raw = next(e.raw for e in modelreg.chain(json.loads(MODELS.read_text()), "review", reg).entries if e.id == who.id)
    assert who.wire == modelreg.review_route(raw, reg).wire and who.route == modelreg.review_route(raw, reg).route


# --- the ADR, its CLAUDE.md line, and the skill with its door ----------------------------------------------

def test_the_adr_credits_its_sources_and_names_its_guard():
    adr = next((ROOT / "docs/adr").glob("0155-*.md"), None)
    assert adr is not None
    text = adr.read_text()
    assert "## Guard" in text
    for source in ("ub-mode", "grilling", "to-questionnaire", "domain-modeling", "wayfinder"):
        assert source in text, source
    assert "tests/test_premise_check.py" in text.partition("## Guard")[2]


def test_claude_md_names_the_adr_with_its_guard():
    adr = next((ROOT / "docs/adr").glob("0155-*.md"))
    line = next((ln for ln in (ROOT / "CLAUDE.md").read_text().splitlines() if f"](docs/adr/{adr.name})" in ln), None)
    assert line is not None and "Guard:" in line and "`tests/test_premise_check.py`" in line


def test_the_skill_and_its_agent_door_are_wired():
    skill = ROOT / ".claude/skills/premise-check/SKILL.md"
    door = ROOT / ".agents/skills/agent-premise-check/SKILL.md"
    link = ROOT / ".claude/skills/agent-premise-check"
    assert skill.is_file() and door.is_file()
    assert link.is_symlink() and link.resolve() == door.parent.resolve()
    assert "squad premise-check" in skill.read_text()
    assert "squad premise-check" in door.read_text() and "premise-check/SKILL.md" in door.read_text()
    assert "--answers" in door.read_text()        # the door names the answered round too, not only the plain one
    assert "`agent-premise-check`" in (ROOT / "CLAUDE.md").read_text()


# --- an answer rewrites the spec in place and opens the next round -------------------------------------------

class StubRepo:
    """A stub GitHub that also holds the spec's body: the ops an answer round uses, on the one injectable runner."""

    def __init__(self, body=""):
        self.calls = []
        self.comments = {}
        self.issue = {"number": 878, "body": body}

    def __call__(self, op, **kw):
        self.calls.append(op)
        if op == "comments":
            return [{"id": i, "body": b} for i, b in self.comments.items()]
        if op == "comment":
            self.comments[len(self.comments) + 1] = kw["body"]
            return {"id": len(self.comments)}
        if op == "update_comment":
            self.comments[kw["comment_id"]] = kw["body"]
            return {"id": kw["comment_id"]}
        if op == "issue":
            return self.issue
        if op == "update_issue":
            self.issue["body"] = kw["body"]
            return self.issue
        raise AssertionError(op)


def round_of(tmp_path, gh, *findings, answers=(), round_no=1):
    return pc.run_round(878, [pc.Finding.of(f) for f in findings], answers,
                        github=gh, root=tmp_path, round_no=round_no, who=reviewer())


def test_an_answer_rewrites_the_spec_body_and_the_comment(tmp_path):
    gh = StubRepo("# Spec\n\nThe check runs one round.\n")
    finding = {"premise": "one comment per spec", "kind": "decision", "recommends": "one comment, rewritten in place"}
    answer = {"premise": "one comment per spec", "answer": "one comment, rewritten in place"}
    report = round_of(tmp_path, gh, finding, answers=[answer])
    assert "## Answers" in gh.issue["body"]
    assert "one comment, rewritten in place" in gh.issue["body"]
    assert gh.calls.count("update_issue") == 1 and "update_comment" not in gh.calls
    assert "| decided |" in report.body and "one comment, rewritten in place" in report.body
    assert report.applied is True
    # the same answers rewrite nothing twice
    before = gh.issue["body"]
    report2 = round_of(tmp_path, gh, finding, answers=[answer], round_no=2)
    assert gh.issue["body"] == before
    assert report2.applied is False


def test_an_answer_from_an_earlier_round_survives_a_later_round(tmp_path):
    """The answers block is the spec's record of every owner decision, so a later round adds to it, never replaces it."""
    gh = StubRepo("# Spec\n")
    first = {"premise": "the check comments on the spec", "kind": "decision", "recommends": "comment on the spec"}
    second = {"premise": "the comment carries a table", "kind": "decision", "recommends": "one table"}
    answer_a = {"premise": "the check comments on the spec", "answer": "comment on the spec"}
    answer_b = {"premise": "the comment carries a table", "answer": "one table"}
    round_of(tmp_path, gh, first, second, answers=[answer_a], round_no=2)
    # the reviewer reads answer A back from the spec body, and this round answers only B
    first.update(answer="comment on the spec", answered_by="owner")
    report = round_of(tmp_path, gh, first, second, answers=[answer_b], round_no=3)
    body = gh.issue["body"]
    assert "- **the check comments on the spec** — comment on the spec" in body
    assert "- **the comment carries a table** — one table" in body
    assert body.count("## Answers") == 1
    by = {r.premise: r.state for r in report.rows}
    assert by == {"the check comments on the spec": "decided", "the comment carries a table": "decided"}


def test_a_glossary_answer_writes_the_term_into_the_glossary(tmp_path):
    (tmp_path / "GLOSSARY.md").write_text("# Glossary\n\n**Node**:\nOne unit of the graph.\n")
    finding = {"premise": "the term round", "kind": "decision", "recommends": "define the term"}
    answer = {"premise": "the term round", "kind": "glossary", "term": "Round",
              "definition": "One invocation of the premise check."}
    report = round_of(tmp_path, StubRepo("# Spec\n"), finding, answers=[answer])
    text = (tmp_path / "GLOSSARY.md").read_text()
    assert "**Round**:" in text and "One invocation of the premise check." in text
    assert text.count("**Round**:") == 1
    assert report.glossary == (tmp_path / "GLOSSARY.md",)
    round_of(tmp_path, StubRepo("# Spec\n"), finding, answers=[answer])
    assert (tmp_path / "GLOSSARY.md").read_text().count("**Round**:") == 1


def test_a_durable_decision_becomes_an_adr_with_a_readme_line(tmp_path):
    (tmp_path / "docs/adr").mkdir(parents=True)
    (tmp_path / "docs/adr/README.md").write_text(
        "# ADR index\n\n| ADR | Rule |\n| --- | --- |\n| [0000](0000-a-rule.md) | A rule |\n\n## Adding an ADR\n\nTake the next free number.\n")
    finding = {"premise": "answers rewrite the spec", "kind": "decision", "recommends": "rewrite in place"}
    answer = {"premise": "answers rewrite the spec", "kind": "adr",
              "title": "An answer rewrites the spec in place",
              "decision": "An owner answer rewrites the spec body and opens the next round."}
    report = round_of(tmp_path, StubRepo("# Spec\n"), finding, answers=[answer])
    adr = tmp_path / "docs/adr/0001-an-answer-rewrites-the-spec-in-place.md"
    assert adr.is_file() and report.adrs == (adr,)
    text = adr.read_text()
    assert "An answer rewrites the spec in place" in text and "opens the next round" in text
    assert "## Status" not in text and not re.search(r"\d{4}-\d{2}-\d{2}", text)
    assert "#883" not in text and "Refs" not in text and "run-" not in text
    assert pc.ADR_MARKER in text              # only an ADR this tool wrote is reused by a later answer
    line = "| [0001](0001-an-answer-rewrites-the-spec-in-place.md) | An answer rewrites the spec in place |"
    assert line in (tmp_path / "docs/adr/README.md").read_text()
    # the same durable decision reuses its ADR and adds no second README line
    round_of(tmp_path, StubRepo("# Spec\n"), finding, answers=[answer])
    readme = (tmp_path / "docs/adr/README.md").read_text()
    assert readme.count("0001-an-answer-rewrites-the-spec-in-place.md") == 1
    assert not (tmp_path / "docs/adr/0002-an-answer-rewrites-the-spec-in-place.md").exists()


def test_an_accepted_adr_with_the_same_slug_is_never_overwritten(tmp_path):
    """Only an ADR this tool wrote is reused: a title whose slug matches an accepted ADR must not rewrite it."""
    d = tmp_path / "docs/adr"
    d.mkdir(parents=True)
    accepted = d / "0148-bug-first.md"
    original = "# ADR-0148: Bug first\n\n## Status\n\nAccepted.\n\n## Decision\n\n1. Zero bugs.\n"
    accepted.write_text(original)
    answer = {"premise": "the bug order", "kind": "adr", "title": "Bug first", "decision": "Bugs first."}
    finding = {"premise": "the bug order", "kind": "decision", "recommends": "bugs first"}
    gh = StubRepo("# Spec\n")
    with pytest.raises(ValueError, match="0148-bug-first.md"):
        round_of(tmp_path, gh, finding, answers=[answer])
    assert accepted.read_text() == original
    assert "premise-check-adr" not in accepted.read_text()
    assert gh.issue["body"] == "# Spec\n"                 # a refused answer leaves the spec alone
    assert "update_issue" not in gh.calls


def test_each_durable_write_is_atomic(tmp_path, monkeypatch):
    """A kill before the rename leaves the glossary, the ADR and the index exactly as they were (ADR-0030)."""
    glossary = tmp_path / "GLOSSARY.md"
    glossary_text = "# Glossary\n\n**Node**:\nOne unit.\n"
    glossary.write_text(glossary_text)
    d = tmp_path / "docs/adr"
    d.mkdir(parents=True)
    accepted = d / "0000-an-old-rule.md"
    adr_text = "# ADR-0000: An old rule\n\n## Decision\n\n1. Old.\n"
    accepted.write_text(adr_text)
    readme = d / "README.md"
    readme_text = "# ADR index\n\n| ADR | Rule |\n| --- | --- |\n| [0000](0000-an-old-rule.md) | An old rule |\n"
    readme.write_text(readme_text)

    def boom(src, dst):
        raise KeyboardInterrupt("killed mid-write")
    monkeypatch.setattr(record.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        pc.write_glossary(tmp_path, {"premise": "a term", "kind": "glossary", "term": "Round",
                                     "definition": "One invocation."})
    with pytest.raises(KeyboardInterrupt):
        pc.write_adr(tmp_path, {"premise": "a decision", "kind": "adr", "title": "A new rule", "decision": "New."})
    with pytest.raises(KeyboardInterrupt):
        pc._index_adr(d, "0001-a-new-rule.md", "0001", "A new rule")
    assert glossary.read_text() == glossary_text
    assert accepted.read_text() == adr_text
    assert readme.read_text() == readme_text
    assert not (d / "0001-a-new-rule.md").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
    assert [p.name for p in d.iterdir() if p.name.startswith(".")] == []


def test_a_question_unblocked_by_an_answer_is_asked_in_the_next_round(tmp_path):
    gh = StubRepo("# Spec\n")
    first = {"premise": "the check comments on the spec", "kind": "decision", "recommends": "comment on the spec"}
    dependent = {"premise": "the comment carries a table", "kind": "decision", "recommends": "one table",
                 "depends_on": "the check comments on the spec"}
    r1 = round_of(tmp_path, gh, first, dependent)
    by1 = {r.premise: r for r in r1.rows}
    assert by1["the check comments on the spec"].question == 1
    assert by1["the comment carries a table"].question is None and "waits on" in by1["the comment carries a table"].held
    answer = {"premise": "the check comments on the spec", "answer": "comment on the spec", "answered_by": "owner"}
    r2 = round_of(tmp_path, gh, first, dependent, answers=[answer], round_no=2)
    by2 = {r.premise: r for r in r2.rows}
    assert by2["the check comments on the spec"].state == "decided"
    assert by2["the comment carries a table"].question == 1     # unblocked, asked now and not before


def test_i_dont_know_becomes_a_probe_or_a_recorded_open_risk_never_decided(tmp_path):
    with_probe = {"premise": "the wave lands in under ten minutes", "kind": "decision",
                  "proof": "run the default workflow once and read the run record", "recommends": "it lands"}
    no_probe = {"premise": "the format of the comment", "kind": "decision", "recommends": "one table"}
    answers = [{"premise": "the wave lands in under ten minutes", "answer": "I don't know"},
               {"premise": "the format of the comment", "answer": "I don't know"}]
    report = round_of(tmp_path, StubRepo("# Spec\n"), with_probe, no_probe, answers=answers)
    by = {r.premise: r for r in report.rows}
    assert by["the wave lands in under ten minutes"].state == "needs a probe"
    assert "run the default workflow once" in by["the wave lands in under ten minutes"].detail
    assert by["the format of the comment"].state == "open"
    assert by["the format of the comment"].risk is True and "open risk" in by["the format of the comment"].detail
    assert all(r.state not in ("proven", "decided") for r in report.rows)
    assert "open risk" in report.body


def test_a_repeated_refutation_with_no_spec_change_stops_the_check(tmp_path):
    refutation = {"premise": "the module never writes the spec", "kind": "fact",
                  "proof": "squad/premise_check.py:1", "holds": False}
    gh = StubRepo("# Spec\n\nThe registry decides the route.\n")
    first = round_of(tmp_path, gh, refutation, round_no=1)
    assert first.stop_reason == "" and first.ending != "stopped"
    second = round_of(tmp_path, gh, refutation, round_no=2)
    assert second.stop_reason == pc.STALL and second.ending == "stopped"
    assert pc.STALL in second.body
    # a changed spec restarts the count: an answer rewrote the body
    gh2 = StubRepo("# Spec\n\nThe registry decides the route.\n")
    round_of(tmp_path, gh2, refutation, round_no=1)
    open_decision = {"premise": "the spec keeps its answers", "kind": "decision", "recommends": "keep them"}
    answer = {"premise": "the spec keeps its answers", "answer": "keep them"}
    assert round_of(tmp_path, gh2, refutation, open_decision, answers=[answer], round_no=2).stop_reason == ""
    # a changed refutation is a different failure, so the streak restarts
    gh3 = StubRepo("# Spec\n")
    round_of(tmp_path, gh3, refutation, round_no=1)
    other = {"premise": "the module reads a file", "kind": "fact", "proof": "squad/premise_check.py:2", "holds": False}
    assert round_of(tmp_path, gh3, other, round_no=2).stop_reason == ""


def test_the_check_ends_when_no_question_is_open(tmp_path):
    assert "settled" in pc.ENDINGS and "stopped" in pc.ENDINGS
    settled = rows_of(tmp_path, one_fact(),
                      {"premise": "one comment per spec", "kind": "decision", "answer": "one", "answered_by": "owner"})
    assert pc.ending(settled) == "settled"
    assert pc.ending(rows_of(tmp_path, {"premise": "the format", "kind": "decision", "recommends": "a table"})) == "awaiting answers"
    assert pc.ending(rows_of(tmp_path, {"premise": "the wave lands", "kind": "run", "proof": "run it once"})) == "awaiting probes"
    body = pc.render(spec=878, round_no=1, reviewer=reviewer(), rows=settled, ending=pc.ending(settled))
    assert "End state:** settled" in body


def test_a_recorded_open_risk_is_named_and_never_called_settled(tmp_path):
    """An open risk is a gap the owner could not close, so the end state says so instead of claiming the check settled."""
    rows = rows_of(tmp_path, {"premise": "the format", "kind": "decision", "recommends": "a table",
                              "answer": "I don't know"})
    assert rows[0].risk is True
    assert pc.ending(rows) == "open risks"
    body = pc.render(spec=878, round_no=1, reviewer=reviewer(), rows=rows, ending=pc.ending(rows))
    assert "**End state:** open risks." in body
    assert "settled" not in body
    assert "| open |" in body


def test_an_answer_that_names_no_finding_is_refused(tmp_path):
    """A typo in an answer's premise must exit 2, not record an answer that settles nothing."""
    with pytest.raises(ValueError, match="names a premise no finding carries"):
        pc.merge_answers([{"premise": "a real premise", "kind": "decision", "recommends": "yes"}],
                         [{"premise": "a typo", "answer": "yes"}])


def test_a_dry_run_prints_the_comment_the_live_round_would_post(tmp_path):
    """The printed comment carries the same round and body marker the next round's stall check compares."""
    gh = StubRepo("# Spec\n")
    finding = {"premise": "one comment per spec", "kind": "decision", "recommends": "one"}
    report = pc.run_round(878, [pc.Finding.of(finding)], [], github=gh, root=tmp_path, round_no=1,
                          who=reviewer(), dry_run=True)
    assert "comment" not in gh.calls and "update_comment" not in gh.calls and "update_issue" not in gh.calls
    sha = hashlib.sha256(b"# Spec\n").hexdigest()
    assert f"<!-- premise-check-state: round=1 body={sha} " in report.body


def test_the_command_takes_an_answers_file():
    a = pc.parser().parse_args(["878", "--findings", "findings.json", "--answers", "answers.json"])
    assert a.answers == "answers.json"
    assert pc.parser().parse_args(["878", "--findings", "findings.json"]).answers is None


def test_the_command_takes_a_root_for_the_specs_checkout(tmp_path):
    a = pc.parser().parse_args(["878", "--findings", "findings.json", "--root", str(tmp_path)])
    assert a.root == str(tmp_path)
    assert pc.parser().parse_args(["878", "--findings", "findings.json"]).root is None


def test_the_artifact_root_must_be_the_specs_checkout(tmp_path):
    """A round on another repository's spec refuses a root whose origin names yet another repository."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "https://github.com/other/project"], check=True)
    with pytest.raises(ValueError, match="other/project"):
        pc.artifact_root("sbihaiko/agent-squad", tmp_path)
    assert pc.artifact_root("other/project", tmp_path) == tmp_path


def test_the_skill_names_the_answers_input_and_the_end_states():
    text = (ROOT / ".claude/skills/premise-check/SKILL.md").read_text()
    assert "--answers" in text
    for word in ("settled", "awaiting answers", "stopped", "open risks"):
        assert word in text, word
    assert "Do not rewrite the spec's text" not in text
    assert "rewrite the spec body" in text or "rewrites the spec body" in text
    assert "ADR-0142" in text         # the round still never starts the spec's tickets
    assert "draft" in text and "CLAUDE.md" in text      # a generated ADR is a draft and gets no rule line


# --- a proxy answers while the owner is away; an owner answer always wins --------------------------------------

DECISION = {"premise": "the format", "kind": "decision", "recommends": "a table"}


def proxy_answer(premise, answer="one table", **kw):
    base = {"premise": premise, "answer": answer, "answered_by": "proxy", "confident": True}
    base.update(kw)
    return base


def proxied_round(tmp_path, gh, *findings, answers=(), proxy=None, round_no=1):
    return pc.run_round(878, [pc.Finding.of(f) for f in findings], answers, github=gh, root=tmp_path,
                        round_no=round_no, who=reviewer(), proxy=proxy)


def test_a_proxy_equal_to_the_reviewer_is_refused(tmp_path):
    gh = StubRepo("# Spec\n")
    with pytest.raises(pc.NoProxy, match="claude-opus-5-5"):
        proxied_round(tmp_path, gh, DECISION, proxy=pc.proxy_named("opus"))
    assert not {"comment", "update_comment", "update_issue"} & set(gh.calls)


def test_every_proxy_answer_carries_its_model_id(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format")], proxy=pc.proxy_named("sonnet"))
    row = report.rows[0]
    assert row.state == "decided" and row.answered_by == "proxy: claude-sonnet-5-5"
    assert "| proxy: claude-sonnet-5-5 |" in report.body
    assert "- **the format** — one table <!-- answered-by: proxy: claude-sonnet-5-5 -->" in gh.issue["body"]


def test_an_owner_answer_overrides_a_proxy_answer_for_the_same_premise(tmp_path):
    gh = StubRepo("# Spec\n")
    owner = {"premise": "the format", "answer": "a list"}
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format"), owner], proxy=pc.proxy_named("sonnet"))
    row = report.rows[0]
    assert row.answered_by == "owner" and row.detail == "a list"
    assert "proxy" not in gh.issue["body"]


def test_a_later_proxy_answer_never_replaces_an_owner_answer_in_the_spec(tmp_path):
    gh = StubRepo("# Spec\n")
    proxied_round(tmp_path, gh, DECISION, answers=[{"premise": "the format", "answer": "a list"}], round_no=1)
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format", "a table")],
                           proxy=pc.proxy_named("sonnet"), round_no=2)
    assert "- **the format** — a list" in gh.issue["body"]
    assert "a table" not in gh.issue["body"]
    assert report.rows[0].state == "open" and report.rows[0].answered_by != "proxy: claude-sonnet-5-5"


def test_a_proxy_never_answers_a_question_that_touches_an_owner_only_action(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, dict(DECISION, owner_only=True), answers=[proxy_answer("the format")],
                           proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].state == "open" and report.rows[0].question == 1
    assert "update_issue" not in gh.calls


def test_a_proxy_never_approves_a_breakdown(tmp_path):
    gh = StubRepo("# Spec\n")
    finding = {"premise": "the breakdown into tickets", "kind": "decision", "recommends": "approve", "breakdown": True}
    report = proxied_round(tmp_path, gh, finding, answers=[proxy_answer("the breakdown into tickets", "approve")],
                           proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].state == "open" and report.rows[0].question == 1
    assert "update_issue" not in gh.calls


def test_an_unconfident_proxy_answer_leaves_the_question_open(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format", confident=False)],
                           proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].state == "open" and report.rows[0].question == 1


def test_with_no_proxy_set_every_question_waits_for_the_owner(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, DECISION)
    assert report.rows[0].state == "open" and report.rows[0].question == 1
    with pytest.raises(ValueError, match="needs --proxy"):
        proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format")])
    assert "update_issue" not in gh.calls


def test_the_autonomy_panel_is_a_named_proxy(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format")], proxy=pc.proxy_named("panel"))
    assert report.rows[0].answered_by == "proxy: panel"


def test_the_command_takes_a_proxy():
    assert pc.parser().parse_args(["878", "--findings", "f.json", "--proxy", "sonnet"]).proxy == "sonnet"
    assert pc.parser().parse_args(["878", "--findings", "f.json"]).proxy is None


def test_the_squad_command_forwards_a_proxy_to_the_round():
    from squad import cli
    a = cli.parser().parse_args(["premise-check", "878", "--findings", "f.json", "--proxy", "sonnet"])
    assert a.proxy == "sonnet"


def test_an_owner_answer_ending_in_a_proxy_marker_keeps_its_text_and_stays_owner_decided(tmp_path):
    gh = StubRepo("# Spec\n")
    proxied_round(tmp_path, gh, DECISION, answers=[{"premise": "the format", "answer": "use X (proxy: ours)"}], round_no=1)
    assert "- **the format** — use X (proxy: ours)" in gh.issue["body"]
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format", "a table")],
                           proxy=pc.proxy_named("sonnet"), round_no=2)
    assert pc._recorded_answers(gh.issue["body"]) == [("the format", "use X (proxy: ours)", "")]
    assert report.refused == (("the format", "the owner answered this premise"),)


def test_an_answer_carrying_an_html_comment_is_refused(tmp_path):
    with pytest.raises(ValueError, match="HTML comment"):
        pc.Answer.of({"premise": "the format", "answer": "x <!-- answered-by: proxy: y -->"})


@pytest.mark.parametrize("finding, answer, reason", [
    (dict(DECISION, owner_only=True), proxy_answer("the format"), "a proxy never answers an owner-only action"),
    (dict(DECISION, breakdown=True), proxy_answer("the format"), "a proxy never approves a breakdown"),
    (DECISION, proxy_answer("the format", confident=False), "a proxy answers only when it is confident"),
])
def test_a_refused_proxy_answer_is_named_in_the_comment_and_the_report(tmp_path, finding, answer, reason):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, finding, answers=[answer], proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].state == "open" and report.rows[0].question == 1
    assert report.rows[0].refused == reason
    assert f"Proxy not taken: {reason}." in report.body
    assert report.refused == (("the format", reason),)
    assert "update_issue" not in gh.calls


def test_a_proxy_never_answers_a_premise_naming_an_owner_only_action_without_the_flag(tmp_path):
    gh = StubRepo("# Spec\n")
    finding = {"premise": "share the credentials with the vendor", "kind": "decision", "recommends": "yes"}
    report = proxied_round(tmp_path, gh, finding, answers=[proxy_answer("share the credentials with the vendor", "yes")],
                           proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].state == "open" and report.rows[0].question == 1
    assert report.rows[0].refused == "a proxy never answers an owner-only action"
    assert "update_issue" not in gh.calls


def test_a_proxy_answer_naming_another_model_is_refused(tmp_path):
    gh = StubRepo("# Spec\n")
    with pytest.raises(ValueError, match="claude-sonnet-5-5.*panel"):
        proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format", answered_by="proxy: claude-sonnet-5-5")],
                      proxy=pc.proxy_named("panel"))
    assert "update_issue" not in gh.calls


def test_a_proxy_answer_naming_the_runs_proxy_is_recorded_as_that_model(tmp_path):
    gh = StubRepo("# Spec\n")
    report = proxied_round(tmp_path, gh, DECISION, answers=[proxy_answer("the format", answered_by="proxy: claude-sonnet-5-5")],
                           proxy=pc.proxy_named("sonnet"))
    assert report.rows[0].answered_by == "proxy: claude-sonnet-5-5"


def test_the_skill_and_door_document_the_proxy_its_flags_and_its_refusals():
    skill = (ROOT / ".claude/skills/premise-check/SKILL.md").read_text()
    door = (ROOT / ".agents/skills/agent-premise-check/SKILL.md").read_text()
    for word in ("--proxy", "panel", "confident", "owner_only", "breakdown", '"answered_by": "proxy"'):
        assert word in skill, word
    assert "--proxy" in door
    assert "Do not answer a question the owner has not answered" not in skill


def test_the_adr_and_claude_md_admit_a_named_proxy():
    adr = next((ROOT / "docs/adr").glob("0155-*.md")).read_text()
    assert "holds no way to run a probe or to answer a question" not in adr
    assert "proxy" in adr.partition("## Decision")[2]
    line = next(ln for ln in (ROOT / "CLAUDE.md").read_text().splitlines() if "](docs/adr/0155-" in ln)
    assert "proxy" in line
