"""GitHub issue #2694: the save path of the Communication theme in the hub Settings page.

The theme reads the committed `slack.yaml` and the profile's `slack.local.yaml` (#2693). This slice saves them: the hub
shows the diff of both files before it writes, writes the local file atomically in the profile folder, writes and lands
the committed file only in the hub's own worktree, records the landed sha in a local registry outside every repository,
and refuses an invalid file with a message that names the key.

The seams under test are the hub's own save function, `slack_files_set`, and the theme's save-dialog renderer in node.
No browser runs here. Every id is a placeholder, and every file is a temporary fixture.
"""
import copy
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [{"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."}],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "persona": "hal",
         "channels": [{"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak",
                       "roles": ["manager"], "announce": ["stg_release"]}]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "hal",
         "channels": [{"id": "C0CLISUP01", "name": "support", "mode": "listen"}]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module comes from this tree and no caller's SG_CHECKOUT
    answers for it. Every value is undone when the module ends."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_save", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def files(tmp_path, committed=None, local=None):
    """The two files of one fixture project: the committed file at the checkout root and the profile's local file."""
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def worktree(tmp_path, committed=None):
    """A git worktree the hub owns, one commit above origin/dev, and the bare origin it fetches and pushes."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, timeout=60)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/dev")
    wt = tmp_path / "hub-wt"
    wt.mkdir()
    git(wt, "init", "-q", "-b", "dev")
    git(wt, "config", "user.name", "fixture")
    git(wt, "config", "user.email", "fixture@localhost")
    c = wt / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    git(wt, "add", "--", "docs/squad/slack.yaml")
    git(wt, "commit", "-q", "-m", "seed the Slack file")
    git(wt, "remote", "add", "origin", str(origin))
    git(wt, "push", "-q", "origin", "dev")
    git(wt, "fetch", "-q", "origin")
    return wt, origin


def clone(tmp_path, origin, name="loop-checkout"):
    """A clone of the fixture origin: the loop checkout the hub hangs its own worktree from."""
    loop = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(origin), str(loop)], check=True, timeout=60)
    git(loop, "config", "user.name", "fixture")
    git(loop, "config", "user.email", "fixture@localhost")
    return loop


def saved(hub, tmp_path, **kw):
    c, loc = files(tmp_path, kw.get("committed"), kw.get("local"))
    body = {"committed": kw.get("new_committed", COMMITTED), "local": kw.get("new_local", LOCAL)}
    body["confirm"] = kw.get("confirm", False)
    return hub.slack_files_set(body, committed=c, local=loc), c, loc


def test_save_shows_the_diff_of_both_files_before_it_writes(hub, tmp_path, monkeypatch):
    """One line per changed value of each file, and no write at all: the owner sees both files before approving."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    new_c, new_l = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    new_c["project"]["language"] = "pt-BR"
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, new_local=new_l)
    assert set(out) >= {"diff", "files"}, out
    assert out["files"]["committed"].endswith("slack.yaml"), out["files"]
    assert out["files"]["local"] == str(loc), out["files"]
    assert any("pt-BR" in line for line in out["diff"]["committed"]), out["diff"]["committed"]
    assert any("client-b" in line for line in out["diff"]["local"]), out["diff"]["local"]
    assert any("en-US" in line for line in out["diff"]["committed"]), out["diff"]["committed"]
    assert "client-a" in yaml.safe_load(loc.read_text())["workspaces"][0]["label"]
    assert yaml.safe_load(c.read_text())["project"]["language"] == "en-US"


def test_a_member_alias_save_writes_the_local_file_and_leaves_slack_yaml_unchanged(hub, tmp_path, monkeypatch):
    """Criterion 9 (issue #2873): the editor adds an alias to a member, and the save writes the local file alone. The
    committed file keeps its bytes, so no alias reaches a repository. The alias is a neutral placeholder."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    c, loc = files(tmp_path)
    local = copy.deepcopy(LOCAL)
    local["members"] = [{"workspace": "T0TEAM0001", "channel": "C0PROJ0001", "role": "manager", "users": ["U0OWNER001"]}]
    loc.write_text(yaml.safe_dump(local, sort_keys=False))
    edited = hub.slack_docs_edit({"committed": copy.deepcopy(COMMITTED), "local": copy.deepcopy(local),
                                  "op": {"kind": "set", "doc": "local", "path": ["members", 0, "alias"],
                                         "value": "alias-a"}}, committed=c, local=loc)
    assert edited["err"] is None, edited
    before = c.read_bytes()
    out = hub.slack_files_set({"committed": edited["committed"], "local": edited["local"], "confirm": True},
                              committed=c, local=loc)
    assert out.get("err") is None, out
    assert yaml.safe_load(loc.read_text())["members"][0]["alias"] == "alias-a"
    assert c.read_bytes() == before
    assert "alias-a" not in c.read_text()


def test_a_local_save_is_atomic_and_applies_at_once(hub, tmp_path, monkeypatch):
    """The local file is written through a temp file and a rename, and the answer carries the file read back, so the
    change applies at once. The committed file is untouched when it has no change."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "checkout"))
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    seen = []
    real = hub.os.replace
    monkeypatch.setattr(hub.os, "replace", lambda a, b: (seen.append((str(a), str(b))), real(a, b))[1])
    out, c, loc = saved(hub, tmp_path, new_local=new_l, confirm=True)
    assert "err" not in out, out
    assert seen and pathlib.Path(seen[0][0]).name.startswith(".") and seen[0][1] == str(loc), seen
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["label"] == "client-b"
    assert out["documents"]["local"]["workspaces"][0]["label"] == "client-b"
    assert yaml.safe_load(c.read_text()) == COMMITTED


def test_the_committed_file_needs_the_hub_worktree_and_never_the_loop_checkout(hub, tmp_path, monkeypatch):
    """With no worktree the save refuses the committed file and writes nothing; a worktree that is the loop checkout is
    refused too. The message names the worktree the hub needs."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "checkout"))
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert out["err"] and "worktree" in out["err"], out
    assert yaml.safe_load(c.read_text())["project"]["language"] == "en-US"
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(tmp_path / "checkout"))
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert out["err"] and "loop checkout" in out["err"], out
    assert yaml.safe_load(c.read_text())["project"]["language"] == "en-US"


def test_an_approved_save_commits_the_file_and_lands_it_in_dev(hub, tmp_path, monkeypatch):
    """The committed file is written in the hub's own worktree, committed alone, and pushed to dev by fast-forward. The
    local file is written in the profile folder in the same save."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c, new_l = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    new_c["project"]["language"] = "pt-BR"
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, new_local=new_l, confirm=True)
    assert "err" not in out, out
    sha = out["sha"]
    assert git(origin, "rev-parse", "dev") == sha
    assert git(wt, "rev-parse", "HEAD") == sha
    assert yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())["project"]["language"] == "pt-BR"
    assert [line for line in git(wt, "show", "--name-only", "--format=", sha).splitlines() if line] == ["docs/squad/slack.yaml"]
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["label"] == "client-b"


def test_the_saved_sha_is_recorded_outside_every_repository(hub, tmp_path, monkeypatch):
    """The landed sha lands in one local registry, and that registry is no file of the worktree it landed from."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    registry = hub.slack_registry_path()
    assert not registry.resolve().is_relative_to(wt.resolve()), registry
    data = json.loads(registry.read_text())
    assert str(wt / "docs" / "squad" / "slack.yaml") in data, data
    assert data[str(wt / "docs" / "squad" / "slack.yaml")]["sha"] == out["sha"]


def test_an_invalid_file_is_refused_with_the_key_it_breaks(hub, tmp_path, monkeypatch):
    """A value that breaks its rule is refused before any write, and the message names the file and the key."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    bad = copy.deepcopy(COMMITTED)
    bad["project"]["language"] = "xx-YY"
    out, c, loc = saved(hub, tmp_path, new_committed=bad, confirm=True)
    assert "err" in out and "project.language" in out["err"], out
    assert "slack.yaml" in out["err"], out
    assert yaml.safe_load(c.read_text()) == COMMITTED


def test_a_moved_worktree_refuses_the_save_before_any_write(hub, tmp_path, monkeypatch):
    """A hub worktree that carries another commit is not the base a land needs. The save refuses it before either file
    is written, so the worktree keeps no uncommitted change and the local file keeps its value."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    (wt / "extra.txt").write_text("x\n")
    git(wt, "add", "--", "extra.txt")
    git(wt, "commit", "-q", "-m", "other work")
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c, new_l = copy.deepcopy(COMMITTED), copy.deepcopy(LOCAL)
    new_c["project"]["language"] = "pt-BR"
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, new_local=new_l, confirm=True)
    assert "err" in out and "origin/dev" in out["err"], out
    assert yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())["project"]["language"] == "en-US"
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["label"] == "client-a"
    assert git(wt, "status", "--porcelain") == ""


def test_a_save_with_no_variable_derives_the_hub_worktree(hub, tmp_path, monkeypatch):
    """A hub that names no worktree derives one per profile under its state directory, outside the loop checkout, and the
    committed save lands from it. The owner sets no variable by hand (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    assert hub.slack_worktree()[0] == derived, hub.slack_worktree()
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    sha = out["sha"]
    assert git(origin, "rev-parse", "dev") == sha
    assert yaml.safe_load((derived / "docs" / "squad" / "slack.yaml").read_text())["project"]["language"] == "pt-BR"
    assert git(loop, "status", "--porcelain") == ""


def test_a_save_after_a_later_landing_advances_the_worktree(hub, tmp_path, monkeypatch):
    """A landing in dev after the first save leaves the hub's worktree behind. The next save brings the clean worktree to
    origin/dev before its check, so it lands instead of refusing (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    later = clone(tmp_path, origin, "other-checkout")
    (later / "notes.txt").write_text("x\n")
    git(later, "add", "--", "notes.txt")
    git(later, "commit", "-q", "-m", "a later landing")
    git(later, "push", "-q", "origin", "HEAD:dev")
    landed = git(origin, "rev-parse", "dev")
    assert git(derived, "rev-parse", "HEAD") != landed
    newer = copy.deepcopy(new_c)
    newer["project"]["language"] = "es-ES"
    out, c, loc = saved(hub, tmp_path, new_committed=newer, confirm=True)
    assert "err" not in out, out
    assert git(origin, "rev-parse", "dev") == out["sha"]
    assert git(derived, "rev-parse", "HEAD") == out["sha"]
    assert subprocess.run(["git", "-C", str(origin), "merge-base", "--is-ancestor", landed, out["sha"]],
                          timeout=60).returncode == 0, "the later landing is lost"


def test_a_local_only_save_records_the_save_in_the_registry(hub, tmp_path, monkeypatch):
    """A save that changes only the local file still records its save: the registry names the worktree file the save
    would have used, the time, and that no commit landed, so a reader of the registry never sees an old save as the
    last one."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_local=new_l, confirm=True)
    assert "err" not in out, out
    data = json.loads(hub.slack_registry_path().read_text())
    entry = data[str(wt / "docs" / "squad" / "slack.yaml")]   # the worktree the save used
    assert entry["committed"] is False and entry["at"], data


def test_a_local_only_save_with_no_variable_keys_the_registry_by_the_derived_path(hub, tmp_path, monkeypatch):
    """A hub that names no worktree derives one per profile and keys the registry by that file, so a reader of the
    registry finds the save without a variable set by hand (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_local=new_l, confirm=True)
    assert "err" not in out, out
    entry = json.loads(hub.slack_registry_path().read_text())[str(derived / "docs" / "squad" / "slack.yaml")]
    assert entry["committed"] is False and entry["at"], entry


def test_a_save_reads_the_committed_diff_from_the_synced_worktree(hub, tmp_path, monkeypatch):
    """A landing in dev changes `slack.yaml` after the last save. The next save advances the clean derived worktree and
    reads the committed diff from the file that worktree now holds, so a document that matches the stale worktree but
    not the branch still lands its save instead of a silent local-only write (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    later = clone(tmp_path, origin, "other-checkout")
    landed_doc = copy.deepcopy(new_c)
    landed_doc["events"][0]["template"] = "Shipped to STG: {items}."
    (later / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(landed_doc, sort_keys=False))
    git(later, "add", "--", "docs/squad/slack.yaml")
    git(later, "commit", "-q", "-m", "a later landing that touches the Slack file")
    git(later, "push", "-q", "origin", "HEAD:dev")
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    assert out["sha"], out
    assert git(origin, "rev-parse", "dev") == out["sha"]
    assert yaml.safe_load((derived / "docs" / "squad" / "slack.yaml").read_text()) == new_c
    assert yaml.safe_load((later / "docs" / "squad" / "slack.yaml").read_text())["events"][0]["template"] \
        == "Shipped to STG: {items}."   # the landing stays in dev until the owner's save replaces it


def test_a_save_after_a_later_landing_lands_the_owners_committed_file(hub, tmp_path, monkeypatch):
    """A landing in dev changes `slack.yaml` after the last save, and the owner then saves the document the hub's
    worktree still holds. The save must land the owner's document: a diff read from the file as it stood before the
    sync would call the save unchanged and leave the later landing in place (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    mine = copy.deepcopy(COMMITTED)
    mine["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=mine, confirm=True)
    assert "err" not in out, out
    later = clone(tmp_path, origin, "other-checkout")
    theirs = copy.deepcopy(mine)
    theirs["events"][0]["template"] = "Shipped to STG: {items}."
    (later / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(theirs, sort_keys=False))
    git(later, "add", "--", "docs/squad/slack.yaml")
    git(later, "commit", "-q", "-m", "a later landing that changes the Slack file")
    git(later, "push", "-q", "origin", "HEAD:dev")
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=mine, new_local=new_l, confirm=True)
    assert "err" not in out, out
    assert out.get("sha"), out   # a diff read before the sync calls this save local-only and drops the owner's document
    assert yaml.safe_load((derived / "docs" / "squad" / "slack.yaml").read_text()) == mine
    entry = json.loads(hub.slack_registry_path().read_text())[str(derived / "docs" / "squad" / "slack.yaml")]
    assert entry["sha"] == out["sha"] and entry["committed"] is True, entry
    assert git(origin, "rev-parse", "dev") == out["sha"]


def test_a_dirty_derived_worktree_refuses_the_save_with_no_file_changed(hub, tmp_path, monkeypatch):
    """A derived worktree that carries uncommitted work cannot land a save. The save refuses before it writes either
    file, so a refused save never leaves the local file rewritten and the committed change unlanded (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = clone(tmp_path, origin)
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    derived = tmp_path / "state" / "slack-worktree"
    mine = copy.deepcopy(COMMITTED)
    mine["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=mine, confirm=True)
    assert "err" not in out, out
    edited = copy.deepcopy(mine)
    edited["events"][0]["template"] = "Hand edit: {items}."
    (derived / "docs" / "squad" / "slack.yaml").write_text(yaml.safe_dump(edited, sort_keys=False))
    assert git(derived, "status", "--porcelain") != ""
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=edited, new_local=new_l, confirm=True)
    assert "err" in out and "uncommitted work" in out["err"], out
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["label"] == "client-a"
    assert yaml.safe_load((derived / "docs" / "squad" / "slack.yaml").read_text()) == edited
    assert git(origin, "rev-parse", "dev") == git(derived, "rev-parse", "HEAD")   # no new land


def test_a_named_worktree_the_hub_did_not_create_is_never_advanced(hub, tmp_path, monkeypatch):
    """The hub advances only the worktree it derives for itself. A worktree the owner names by hand keeps its HEAD: the
    save refuses a worktree that stands behind origin/dev rather than resetting it (issue #2763)."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    head = git(wt, "rev-parse", "HEAD")
    later = clone(tmp_path, origin, "other-checkout")
    (later / "notes.txt").write_text("x\n")
    git(later, "add", "--", "notes.txt")
    git(later, "commit", "-q", "-m", "a later landing")
    git(later, "push", "-q", "origin", "HEAD:dev")
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" in out and "origin/dev" in out["err"], out
    assert git(wt, "rev-parse", "HEAD") == head, "the hub moved a worktree it did not create"
    assert yaml.safe_load(loc.read_text())["workspaces"][0]["label"] == "client-a"
    assert git(wt, "status", "--porcelain") == ""


def test_a_local_only_save_keeps_the_last_landed_sha(hub, tmp_path, monkeypatch):
    """A save after a land keeps the landed sha in its registry entry, so a reader still knows the commit the files
    come from, and the entry says the latest save landed no commit of its own."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    landed = out["sha"]
    landed_doc = yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())
    new_l = copy.deepcopy(LOCAL)
    new_l["workspaces"][0]["label"] = "client-b"
    out, c, loc = saved(hub, tmp_path, new_committed=landed_doc, new_local=new_l, confirm=True)
    assert "err" not in out, out
    entry = json.loads(hub.slack_registry_path().read_text())[str(wt / "docs" / "squad" / "slack.yaml")]
    assert entry["sha"] == landed, entry
    assert entry["committed"] is False, entry


def test_a_registry_write_that_fails_is_a_warning_and_the_save_stands(hub, tmp_path, monkeypatch):
    """The landed sha that the registry cannot keep is stated as a warning beside the save, never hidden: the file
    landed, so the save stands, and the owner still learns that the registry did not record it."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    monkeypatch.setattr(hub, "_slack_record", lambda *a, **k: "no space left on device")
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" not in out, out
    assert out["sha"] and "no space left on device" in out.get("warn", ""), out


def test_a_failed_push_reports_the_state_the_save_left(hub, tmp_path, monkeypatch):
    """A push the origin rejects comes back as an error that names the commit and the files the save already wrote, so
    a partial save is stated, never silent."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    wt, origin = worktree(tmp_path)
    loop = tmp_path / "loop-checkout"
    loop.mkdir()
    monkeypatch.setenv("SG_SLACK_WORKTREE", str(wt))
    monkeypatch.setenv("SG_CHECKOUT", str(loop))
    real = hub._slack_git

    def fake(cwd, *args):
        if args[:1] == ("push",):
            return subprocess.CompletedProcess(["git"], 1, "", "rejected: non-fast-forward")
        return real(cwd, *args)

    monkeypatch.setattr(hub, "_slack_git", fake)
    new_c = copy.deepcopy(COMMITTED)
    new_c["project"]["language"] = "pt-BR"
    out, c, loc = saved(hub, tmp_path, new_committed=new_c, confirm=True)
    assert "err" in out and "non-fast-forward" in out["err"], out
    assert "commit" in out["err"] and str(wt) in out["err"], out
    assert yaml.safe_load((wt / "docs" / "squad" / "slack.yaml").read_text())["project"]["language"] == "pt-BR"


def test_the_preview_carries_the_files_it_was_computed_from(hub, tmp_path, monkeypatch):
    """The preview states the size and time of every file it read, the sibling profile files included, so a page can
    see that a file changed after its preview and ask again before it confirms."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "checkout"))
    c, loc = files(tmp_path)
    sibling = loc.parent / "7.slack.local.yaml"
    sibling.write_text(yaml.safe_dump({"version": 1, "workspaces": []}, sort_keys=False))
    out = hub.slack_files_set({"committed": COMMITTED, "local": LOCAL}, committed=c, local=loc)
    assert "err" not in out, out
    base = out["base"]
    assert base[str(loc)]["size"] == loc.stat().st_size, base
    assert base[str(c)]["size"] == c.stat().st_size, base
    assert base[str(sibling)]["size"] == sibling.stat().st_size, base


def test_a_sibling_profile_file_is_validated_at_the_save(hub, tmp_path, monkeypatch):
    """The profile folder's other local files are read at the save, and a rule they break names the sibling file, not
    the file being saved."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "checkout"))
    c, loc = files(tmp_path)
    sibling = loc.parent / "7.slack.local.yaml"
    sibling.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    out = hub.slack_files_set({"committed": COMMITTED, "local": LOCAL, "confirm": True}, committed=c, local=loc)
    assert "err" in out and "7.slack.local.yaml" in out["err"], out
    assert "workspaces[0].id" in out["err"], out


def test_the_first_save_writes_the_local_file_of_a_profile_that_holds_none(hub, tmp_path, monkeypatch):
    """A profile folder that resolves and holds no local file is a profile in force (GitHub issue #2762): the preview
    answers the diff and names `<project>.slack.local.yaml`, and the confirm writes that file in the profile folder."""
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    monkeypatch.delenv("SG_SLACK_WORKTREE", raising=False)
    checkout = tmp_path / "checkout"
    c = checkout / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    c.write_text(yaml.safe_dump(COMMITTED, sort_keys=False))
    prof = tmp_path / "profiles"
    prof.mkdir()
    monkeypatch.setattr(hub, "CHECKOUT", str(checkout))
    monkeypatch.setenv("SG_CHECKOUT", str(checkout))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_PROJECT", "4")
    loc = prof / "4.slack.local.yaml"
    assert not loc.exists()
    preview = hub.slack_files_set({"committed": COMMITTED, "local": LOCAL, "confirm": False})
    assert "err" not in preview, preview
    assert preview["files"]["local"] == str(loc), preview["files"]
    assert preview["diff"]["local"], preview["diff"]
    out = hub.slack_files_set({"committed": COMMITTED, "local": LOCAL, "confirm": True})
    assert "err" not in out, out
    assert loc.exists(), out
    assert yaml.safe_load(loc.read_text()) == LOCAL


# ---------------------------------------------------------------- the save-dialog renderer

def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _save_slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    diff = re.search(r"^function diffByFile\(groups\).*$", page, re.M).group(0)
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, "const themeLine=(t,l)=>t+' · '+l;", sl, diff])


def test_the_save_dialog_names_both_files_and_their_changed_lines(hub):
    """The one preview groups its lines under each file the save would touch, so the owner approves both files."""
    preview = {"files": {"committed": "slack.yaml", "local": "4.slack.local.yaml"},
               "diff": {"committed": ["-  language: en-US", "+  language: pt-BR"], "local": ["-  label: client-a"]}}
    body = (_save_slices(hub.PAGE) + f"\nconst g=slGroups({json.dumps(preview)});"
            "console.log(JSON.stringify(g));console.log(diffByFile(g))")
    lines = _node(body).splitlines()
    groups = json.loads(lines[0])
    assert [g["file"] for g in groups] == ["slack.yaml", "4.slack.local.yaml"], groups
    text = "\n".join(lines[1:])
    assert "slack.yaml" in text and "4.slack.local.yaml" in text, text
    assert "Communication · +  language: pt-BR" in text, text
