"""GitHub issue #3033: another profile's project node is edited from any hub page (SPEC 3031).

The tree draws the projects of every profile the hub knows. The owner edits the Persona and the Language of a project
node that belongs to another profile; the page posts the edit with the target project, and the hub writes that project's
Slack file through that project's own save path. The edit saves by itself, as an edit of the page's own project does,
and each project keeps its own pending save, refusal, retry and undo, so a switch of project never redirects one.

The seams are the hub's tree, the edit route `slack_docs_edit`, the save `slack_files_set`, and the page's own functions
run in node over stubbed timers and a stubbed fetch. Every id and name is a neutral placeholder, and every repository is
a temporary fixture.
"""
import json
import pathlib
import subprocess

import yaml

from tests import test_hub_slack_clients as C

hub = C.hub   # the shared module fixture, registered here under its own name
_node, _slices = C._node, C._slices

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALPHA = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": "T0ALPHA001", "label": "alpha-team", "class": "owned",
                    "channels": [{"id": "C0ALPHA001", "name": "alpha-room", "mode": "listen"}]}],
}
BETA = {
    "version": 1,
    "project": {"language": "es-ES", "persona": "nova"},
    "personas": [{"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Short."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": "T0BETA0001", "label": "beta-team", "class": "owned",
                    "channels": [{"id": "C0BETA0001", "name": "beta-room", "mode": "listen"}]}],
}
LOCAL = {"version": 1}


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def repo(tmp_path, name, doc):
    """A project checkout that is a git repository at origin/dev: `doc` is its committed Slack file, and None leaves the
    repository without one. The bare origin answers the commits the save lands."""
    origin = tmp_path / f"origin-{name}.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/dev")
    co = tmp_path / "checkouts" / name
    co.mkdir(parents=True)
    git(co, "init", "-q", "-b", "dev")
    git(co, "config", "user.name", "fixture")
    git(co, "config", "user.email", "fixture@localhost")
    c = co / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    if doc is None:
        git(co, "commit", "-q", "--allow-empty", "-m", "seed the empty repository")
    else:
        c.write_text(yaml.safe_dump(doc, sort_keys=False))
        git(co, "add", "--", "docs/squad/slack.yaml")
        git(co, "commit", "-q", "-m", "seed the Slack file")
    git(co, "remote", "add", "origin", str(origin))
    git(co, "push", "-q", "origin", "dev")
    git(co, "fetch", "-q", "origin")
    return co, origin


def wire(tmp_path, monkeypatch, hub, projects):
    """The hub's own seam: one git checkout and one profile file per project, the client map, and its state directory.
    `projects` maps a slug to its committed document, or None for a project whose repository holds no Slack file."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, doc in projects.items():
        co, origin = repo(tmp_path, slug, doc)
        (prof / f"{slug}.sh").write_text(
            f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={slug}}}"; '
            f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
        loc = prof / f"{slug}.slack.local.yaml"
        loc.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
        made[slug] = {"checkout": co, "origin": origin, "committed": co / "docs" / "squad" / "slack.yaml", "local": loc}
    (prof / "clients.local.yaml").write_text(yaml.safe_dump(
        {"version": 1, "clients": [{"id": "acme", "name": "Acme", "projects": sorted(projects)}]}, sort_keys=False))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))   # the page's own project, never a seat
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)   # each fixture owns its own derived worktree per project
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    return prof, made


def origin_file(made, slug):
    """The committed document at origin/dev of one project."""
    text = git(made[slug]["origin"], "show", "dev:docs/squad/slack.yaml")
    return yaml.safe_load(text)


def origin_commits(made, slug):
    return int(git(made[slug]["origin"], "rev-list", "--count", "dev"))


def staging_new(doc, persona=None, language=None):
    new = json.loads(json.dumps(doc))
    if persona is not None:
        new["project"]["persona"] = persona
    if language is not None:
        new["project"]["language"] = language
    return new


# ---------------------------------------------------------------- the edit route writes the project's own documents

def test_an_edit_of_another_projects_node_stages_that_projects_own_documents(hub, tmp_path, monkeypatch):
    """#3033 acceptance 2 and 12: the edit names the target project, and the hub reads and answers that project's own two
    documents: the page's own project takes no part."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": ALPHA, "beta": BETA})
    out = hub.slack_docs_edit({"project": "beta",
                               "op": {"kind": "field", "scope": "project", "field": "persona", "value": "nova"}})
    assert out.get("err") is None, out
    assert out["documents"]["committed"]["project"]["persona"] == "nova", out["documents"]
    assert out["documents"]["committed"]["personas"][0]["id"] == "nova", out["documents"]
    assert out["documents"]["local"] == LOCAL, out["documents"]
    assert made["alpha"]["committed"].read_text() == yaml.safe_dump(ALPHA, sort_keys=False), \
        "the page's own project is not read nor written by the edit"


def test_a_project_whose_file_does_not_read_is_refused_with_the_reason(hub, tmp_path, monkeypatch):
    """#3033 acceptance 9: a file the reader cannot parse refuses the edit, and the message names the file and the key."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA})
    made["beta"]["committed"].write_text("project: [\n")
    out = hub.slack_docs_edit({"project": "beta",
                               "op": {"kind": "field", "scope": "project", "field": "persona", "value": "nova"}})
    assert out.get("err"), out
    assert "does not read" in out["err"] and "slack.yaml" in out["err"], out["err"]


# ---------------------------------------------------------------- the save lands one commit in the project's repository

def test_a_save_of_another_projects_node_lands_one_commit_in_its_repository(hub, tmp_path, monkeypatch):
    """#3033 acceptance 2, 3 and 5: the save writes the project's own Slack file, and the change is one commit of that
    project's repository, landed on its dev branch."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"alpha": ALPHA, "beta": BETA})
    before = origin_commits(made, "beta")
    out = hub.slack_files_post({"project": "beta", "committed": staging_new(BETA, persona="nova", language="en-US"),
                                "local": LOCAL, "confirm": True})
    assert out.get("err") is None and out.get("sha"), out
    assert origin_commits(made, "beta") == before + 1, origin_commits(made, "beta")
    assert origin_file(made, "beta")["project"] == {"language": "en-US", "persona": "nova"}
    assert origin_commits(made, "alpha") == 1 and origin_file(made, "alpha")["project"]["persona"] == "hal", \
        "the page's own project takes no commit"


def test_an_unchanged_document_creates_no_commit(hub, tmp_path, monkeypatch):
    """#3033 acceptance 4: a save of the document the branch already holds answers no sha and lands no commit."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA})
    first = hub.slack_files_post({"project": "beta", "committed": staging_new(BETA, language="en-US"),
                                  "local": LOCAL, "confirm": True})
    assert first.get("err") is None and first.get("sha"), first
    before = origin_commits(made, "beta")
    again = hub.slack_files_post({"project": "beta", "committed": staging_new(BETA, language="en-US"),
                                  "local": LOCAL, "confirm": True})
    assert again.get("err") is None, again
    assert not again.get("sha") and again.get("committed_unchanged") is True, again
    assert origin_commits(made, "beta") == before, origin_commits(made, "beta")


def test_a_project_with_no_worktree_gets_one_through_the_provisioning_path(hub, tmp_path, monkeypatch):
    """#3033 acceptance 6: the hub derives the project's own worktree, makes it from the project's checkout, and lands
    the edit from it."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA})
    derived = tmp_path / "state" / "slack-worktree" / "beta"
    assert not derived.exists()
    out = hub.slack_files_post({"project": "beta", "committed": staging_new(BETA, language="en-US"),
                                "local": LOCAL, "confirm": True})
    assert out.get("err") is None and out.get("sha"), out
    assert derived.is_dir(), derived
    assert origin_file(made, "beta")["project"]["persona"] == "nova"


def test_a_failed_provisioning_is_refused_and_names_the_reason(hub, tmp_path, monkeypatch):
    """#3033 acceptance 7: a checkout the hub cannot hang a worktree from refuses the save, and the message names the
    reason."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": BETA})
    made["beta"]["checkout"].joinpath(".git").rename(made["beta"]["checkout"].joinpath(".git-away"))
    out = hub.slack_files_post({"project": "beta", "committed": staging_new(BETA, language="en-US"),
                                "local": LOCAL, "confirm": True})
    assert out.get("err"), out
    assert "could not be created" in out["err"], out["err"]


def test_an_absent_slack_file_is_not_a_refusal(hub, tmp_path, monkeypatch):
    """#3033 acceptance 8: a project whose repository holds no Slack file still takes the edit, and the save creates the
    file."""
    _prof, made = wire(tmp_path, monkeypatch, hub, {"beta": None})
    out = hub.slack_files_post({"project": "beta", "committed": BETA, "local": LOCAL, "confirm": True})
    assert out.get("err") is None and out.get("sha"), out
    assert origin_file(made, "beta")["project"]["persona"] == "nova"


# ---------------------------------------------------------------- the page

TIMERS = ('let TQ=[],TN=0;globalThis.setTimeout=(f,ms)=>{const id=++TN;TQ.push({id,f,ms});return id};'
          'globalThis.clearTimeout=id=>{TQ=TQ.filter(t=>t.id!==id)};'
          'const fire=()=>{const q=TQ;TQ=[];for(const t of q)t.f()};')
TREE = {
    "clients": [{"id": "c0", "kind": "client", "name": "Acme", "children": [
        {"id": "c0.p0", "kind": "project", "name": "alpha", "here": True, "children": []},
        {"id": "c0.p1", "kind": "project", "name": "beta", "here": False, "hub": "/beta/#settings",
         "edit": {"project": "beta"}, "children": []}]}]
}
ALPHA_DOCS = {"committed": {"version": 1, "project": {"language": "en-US", "persona": "hal"}}, "local": {"version": 1}}
BETA_DOCS = {"committed": {"version": 1, "project": {"language": "es-ES", "persona": "nova"}}, "local": {"version": 1}}
PAGE_STATE = (f'slDocs=slClone({json.dumps(ALPHA_DOCS)});slSavedDocs=slClone(slDocs);'
              f'slT={json.dumps(TREE)};slTsel="c0.p1";slRender=()=>{{}};'
              'const ticks=async()=>{for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r))};')


def _run(hub, script):
    tail = hub.PAGE[hub.PAGE.index("async function slLoad()"):hub.PAGE.index('$("#sbody").addEventListener("change"')]
    body = _slices(hub.PAGE) + "\n" + tail + "\n" + TIMERS + PAGE_STATE + script
    return json.loads(_node(body))


def test_an_edit_of_another_projects_node_saves_by_itself_its_own_documents(hub):
    """#3033 acceptance 5, 12 and 13: the edit stages through the project's target and saves by itself after the same
    debounce as the page's own project; the save carries only that project's documents, and a switch of project before
    the debounce ends still saves to the edit's own project."""
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(b.op'
              '?{committed:{version:1,project:{language:"es-ES",persona:"nova"}},local:{version:1}}'
              ':b.project?{sha:"abc1234"}:{})})};'
              '(async()=>{slPoField("beta","persona",{value:"nova"});await slPoSt("beta").chain;await ticks();'
              'const ms=TQ.map(t=>t.ms),n=TQ.length;slTsel="c0.p0";fire();await ticks();'
              'console.log(JSON.stringify({ms,n,sent:SENT.map(s=>({keys:Object.keys(s).sort(),project:s.project,'
              'confirm:s.confirm,persona:s.committed&&s.committed.project?s.committed.project.persona:null})),'
              'own:slDocs,beta:slPoSt("beta").docs}))})()')
    got = _run(hub, script)
    assert got["n"] == 1 and got["ms"] == [2000], got
    op, save = got["sent"]
    assert op["keys"] == ["op", "project"] and op["project"] == "beta", op
    assert save["keys"] == ["committed", "confirm", "local", "project"], save
    assert save["project"] == "beta" and save["confirm"] is True and save["persona"] == "nova", save
    assert got["own"] == ALPHA_DOCS, "the page's own project keeps its documents"
    assert got["beta"]["committed"]["project"]["persona"] == "nova", got["beta"]


def test_a_response_after_a_switch_updates_only_its_own_project(hub):
    """#3033 acceptance 14: a response that arrives after a switch of project writes only the project the edit came
    from; the page's own project and the rendered values of the selected node keep their own documents."""
    script = ('let REL=[];globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);'
              'return new Promise(r=>REL.push(()=>r({json:()=>Promise.resolve({committed:'
              '{version:1,project:{language:"es-ES",persona:"nova"}},local:{version:1}})})))};'
              '(async()=>{slPoSet("beta",' + json.dumps(BETA_DOCS) + ');slPoField("beta","persona",{value:"nova"});'
              'await ticks();slTsel="c0.p0";REL.shift()();await slPoSt("beta").chain;await ticks();'
              'const alpha=slTSettingVal(slTNode("c0.p0"),{key:"persona"},{field:"persona",scope:"project"});'
              'console.log(JSON.stringify({own:slDocs,beta:slPoSt("beta").docs,alpha,sel:slTsel}))})()')
    got = _run(hub, script)
    assert got["own"] == ALPHA_DOCS, got["own"]
    assert got["beta"]["committed"]["project"]["persona"] == "nova", got["beta"]
    assert got["alpha"] == "hal" and got["sel"] == "c0.p0", got


def test_retry_and_undo_act_on_the_project_the_edit_came_from(hub):
    """#3033 acceptance 15: a refusal offers Retry on that project, and Undo puts back that project's documents."""
    script = ('let SENT=[],OK=false;globalThis.fetch=(u,o)=>{const b=JSON.parse(o.body);SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(OK?{sha:"abc1234"}:{err:"the Slack file does not read"})})};'
              '(async()=>{const s=slPoSt("beta");s.saved=slClone(' + json.dumps(BETA_DOCS) + ');s.docs=slClone(s.saved);'
              's.docs.committed.project.language="en-US";await slPoSave("beta");'
              'const failed=s.msg.startsWith("!")&&typeof s.retry==="function";OK=true;s.retry();await ticks();'
              'const saved=s.msg==="saved",undo=slUndo&&slUndo.project;slUndoLast();await ticks();'
              'console.log(JSON.stringify({failed,saved,undo,sent:SENT.map(x=>x.project),'
              'lang:slPoSt("beta").docs.committed.project.language}))})()')
    got = _run(hub, script)
    assert got["failed"] is True and got["saved"] is True, got
    assert got["undo"] == "beta", got
    assert got["sent"] == ["beta", "beta", "beta"], got
    assert got["lang"] == "es-ES", "Undo puts back the project's saved document"


def test_the_page_own_project_keeps_its_autosave(hub):
    """#3033 acceptance 16: an edit of the page's own project still saves through the global path, and the other
    projects' state takes no part in it."""
    script = ('let SENT=[];globalThis.fetch=(u,o)=>{const b=o&&o.body?JSON.parse(o.body):null;if(b)SENT.push(b);'
              'return Promise.resolve({json:()=>Promise.resolve(b?{sha:"abc1234"}'
              ':{docs:SENT[SENT.length-1],tree:{clients:[]}})})};'
              '(async()=>{slDocs.committed.project.language="pt-BR";slAutoSchedule();fire();await ticks();'
              'console.log(JSON.stringify({sent:SENT,own:slDocs.committed.project.language,'
              'other:Object.keys(slPo).length}))})()')
    got = _run(hub, script)
    assert got["sent"] and got["sent"][0].get("project") in (None, ""), got["sent"]
    assert got["own"] == "pt-BR" and got["other"] == 0, got
