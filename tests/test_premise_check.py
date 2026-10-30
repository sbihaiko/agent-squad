"""Guard for ADR-0155: `/premise-check` runs one round in the chat and records the check on the spec.

The reviewer is a stub here. A round resolves what reading proves, sends what only running proves to a probe, and
asks the owner at most five questions, each with a recommended answer that "yes" accepts. The result is one comment
on the spec, found by its marker and rewritten in place.
"""
import json
from pathlib import Path

import pytest

from squad import modelreg
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
    assert "`agent-premise-check`" in (ROOT / "CLAUDE.md").read_text()
