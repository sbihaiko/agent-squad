"""Ticket #139: run_start records the hash of every skill the graph can load."""
import json
from pathlib import Path

import pytest

from squad import record, skills
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc
from squad.options import OptionError
from tests.gitrepo import make_repo, sh
from tests.helpers import router


def vendor_fixture(tmp_path):
    """A git repo that holds a vendor/skills copy with two skills. The router graph allows `tdd` and not `unused`."""
    root = Path(make_repo(tmp_path / "vendorrepo"))
    v = root / "vendor" / "skills"
    versions = {}
    for name, text in (("tdd", "tdd text"), ("unused", "other text")):
        d = v / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(text)
        versions[name] = {"files": 1, "sha256": skills.tree_hash(d), "source": f"skill:{name}"}
    (v / "VERSIONS.json").write_text(json.dumps(versions))
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-qm", "vendor")
    return v, versions, sh(root, "git", "rev-parse", "HEAD")


@pytest.mark.anyio
async def test_run_start_carries_each_loadable_skills_hash_and_the_vendor_commit(tmp_path):
    v, versions, commit = vendor_fixture(tmp_path)
    run = tmp_path / "run"
    eng = Engine(from_doc(router()), ScriptedAdapter({}, repeat_last=True), run, vendor=v)
    eng._start()
    start = record.read(run / "record.jsonl")[0]
    assert start["event"] == "run_start"
    assert start["skills"]["tdd"] == {"hash": versions["tdd"]["sha256"], "source": "skill:tdd"}
    assert "unused" not in start["skills"]                      # only a skill the graph can load
    assert start["skills_commit"] == commit


def test_a_record_without_the_skills_field_still_loads(tmp_path):
    f = tmp_path / "record.jsonl"
    f.write_text(json.dumps({"event": "run_start", "pos": 1, "run_id": "r", "graph_name": "g", "graph_hash": "h", "engine_options": {}, "root": "main", "plan": []}) + "\n")
    assert record.read(f)[0]["run_id"] == "r"


def test_load_reads_versions_json_and_a_missing_file_is_empty(tmp_path):
    v, versions, _ = vendor_fixture(tmp_path)
    assert skills.load(v)["tdd"] == {"hash": versions["tdd"]["sha256"], "source": "skill:tdd"}
    assert skills.load(tmp_path / "nowhere") == {}


# Ticket #142: a resume refuses when a recorded skill hash differs from the current one.
def make_skill_engine(v, run):
    from tests.test_resume import wait_doc
    ok = {"output": {"x": 1, "y": 1}, "spend": 0.1}
    return Engine(from_doc(wait_doc()), ScriptedAdapter({"a": ok, "b": ok}, repeat_last=True), run, vendor=v)


async def run_then_pause(v, run):
    """Run a graph that pauses at a wait, then record the router's skills in run_start, as a graph that loads them would."""
    await make_skill_engine(v, run).run()
    f = run / "record.jsonl"
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    lines[0]["skills"] = skills.snapshot(router(), v)
    lines[0]["skills_commit"] = skills.commit(v)
    f.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return lines[0]


@pytest.mark.anyio
async def test_a_resume_refuses_a_changed_skill_and_writes_no_event(tmp_path):
    from squad.engine import ResumeRefused
    v, versions, commit = vendor_fixture(tmp_path)
    run = tmp_path / "run"
    start = await run_then_pause(v, run)
    n = len(record.read(run / "record.jsonl"))
    old = start["skills"]["tdd"]["hash"]
    (v / "skills" / "tdd" / "SKILL.md").write_text("changed text")
    versions["tdd"]["sha256"] = skills.tree_hash(v / "skills" / "tdd")
    (v / "VERSIONS.json").write_text(json.dumps(versions))
    with pytest.raises(ResumeRefused) as err:
        await make_skill_engine(v, run).resume(answer="pass")
    msg = str(err.value)
    assert all(x in msg for x in ("tdd", old, versions["tdd"]["sha256"], commit))
    assert len(record.read(run / "record.jsonl")) == n


@pytest.mark.anyio
async def test_an_unchanged_skill_resumes_and_the_resume_event_carries_the_hashes(tmp_path):
    v, versions, _ = vendor_fixture(tmp_path)
    run = tmp_path / "run"
    await run_then_pause(v, run)
    await make_skill_engine(v, run).resume(answer="pass")
    res = [e for e in record.read(run / "record.jsonl") if e["event"] == "resume"][0]
    assert res["skills"]["tdd"]["hash"] == versions["tdd"]["sha256"]


@pytest.mark.anyio
async def test_an_older_record_without_skills_resumes_as_before(tmp_path):
    v, _, _ = vendor_fixture(tmp_path)
    run = tmp_path / "run"
    await make_skill_engine(v, run).run()
    (v / "skills" / "tdd" / "SKILL.md").write_text("changed text")
    (v / "VERSIONS.json").write_text("{}")
    await make_skill_engine(v, run).resume(answer="pass")

# Ticket #140: a start refuses on a dirty or hand-edited vendor/skills, before run_start is written.

def _refused(tmp_path, v, match):
    run = tmp_path / "run"
    eng = Engine(from_doc(router()), ScriptedAdapter({}, repeat_last=True), run, vendor=v)
    with pytest.raises(OptionError, match=match):
        eng._start()
    assert not (run / "record.jsonl").exists()                  # no record, as --expect-graph-hash


def test_a_clean_vendor_tree_starts(tmp_path):
    v, _, _ = vendor_fixture(tmp_path)
    Engine(from_doc(router()), ScriptedAdapter({}, repeat_last=True), tmp_path / "run", vendor=v)._start()
    assert record.read(tmp_path / "run" / "record.jsonl")[0]["event"] == "run_start"


def test_a_modified_skill_file_refuses_and_names_the_path(tmp_path):
    v, _, _ = vendor_fixture(tmp_path)
    (v / "skills" / "tdd" / "SKILL.md").write_text("edited")
    _refused(tmp_path, v, "skills/tdd/SKILL.md")


def test_a_staged_change_refuses(tmp_path):
    v, _, _ = vendor_fixture(tmp_path)
    (v / "skills" / "tdd" / "SKILL.md").write_text("edited")
    sh(v, "git", "add", "-A")
    _refused(tmp_path, v, "skills/tdd/SKILL.md")


def test_an_untracked_file_refuses(tmp_path):
    v, _, _ = vendor_fixture(tmp_path)
    (v / "skills" / "tdd" / "NEW.md").write_text("new")
    _refused(tmp_path, v, "NEW.md")


def test_bytes_that_differ_from_versions_json_refuse_and_name_the_skill(tmp_path):
    v, versions, _ = vendor_fixture(tmp_path)
    (v / "skills" / "tdd" / "SKILL.md").write_text("edited")    # committed, so the tree is clean; VERSIONS.json is stale
    sh(v, "git", "commit", "-qam", "hand edit")
    _refused(tmp_path, v, "tdd")


# Review fixes: a real run records the skills, a failing git status refuses, a missing commit reads cleanly.
def skill_wait_engine(v, run):
    d = router()
    d["nodes"][0]["children"] = ["route", "ask", "final_gate"]
    d["nodes"].append({"name": "ask", "kind": "wait", "owner": "rafael", "deadline": 100, "default": "pass"})
    ad = ScriptedAdapter({"router": {"output": {"spawns": []}, "spend": 0.1}}, repeat_last=True)
    return Engine(from_doc(d), ad, run, vendor=v)


@pytest.mark.anyio
async def test_a_real_run_records_skills_and_a_resume_refuses_after_the_skill_changes(tmp_path):
    from squad.engine import ResumeRefused
    v, versions, _ = vendor_fixture(tmp_path)
    run = tmp_path / "run"
    await skill_wait_engine(v, run).run()
    start = record.read(run / "record.jsonl")[0]
    assert start["skills"]["tdd"]["hash"] == versions["tdd"]["sha256"]      # the run wrote it, no hand edit
    n = len(record.read(run / "record.jsonl"))
    (v / "skills" / "tdd" / "SKILL.md").write_text("changed text")
    versions["tdd"]["sha256"] = skills.tree_hash(v / "skills" / "tdd")
    (v / "VERSIONS.json").write_text(json.dumps(versions))
    with pytest.raises(ResumeRefused, match="tdd"):
        await skill_wait_engine(v, run).resume(answer="pass")
    assert len(record.read(run / "record.jsonl")) == n


def test_a_failing_git_status_inside_a_repo_refuses(tmp_path, monkeypatch):
    v, _, _ = vendor_fixture(tmp_path)
    real = skills.subprocess.run

    def fake(cmd, *a, **kw):
        if cmd[:2] == ["git", "status"]:
            raise skills.subprocess.CalledProcessError(128, cmd, stderr="fatal: bad index")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(skills.subprocess, "run", fake)
    assert any("git status failed" in x for x in skills.dirty(v))


def test_outside_a_git_repo_only_the_byte_check_runs(tmp_path):
    v = tmp_path / "plain"
    d = v / "skills" / "tdd"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("t")
    (v / "VERSIONS.json").write_text(json.dumps({"tdd": {"sha256": skills.tree_hash(d), "source": "s"}}))
    assert skills.dirty(v) == []


def test_a_missing_commit_reads_cleanly_and_hash_of_follows_the_vendor(tmp_path):
    v, versions, _ = vendor_fixture(tmp_path)
    assert skills.hash_of("tdd", v) == versions["tdd"]["sha256"]
    assert skills.hash_of("nope", v) == ""
    assert skills.commit_label(None) == "no commit recorded"
    assert skills.commit_label("abc") == "vendor commit abc"
