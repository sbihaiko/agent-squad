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
