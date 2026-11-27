"""GitHub issue #3034: another profile's workspace and channel nodes are edited from any hub page (SPEC 3031).

The tree draws the workspaces and the channels of every project the hub knows. The owner edits the Persona and the
Language of a workspace node and of a channel node that belong to another profile's project, through the same
project-bound save the project node uses. Each edit writes the file that holds the row: the committed file or the
local file, as on the project's own page. Every other setting of those nodes stays read-only.

The seams are the hub's tree, the edit route `slack_docs_edit`, the save `slack_files_set`, and the page's own
functions run in node over stubbed timers and a stubbed fetch. Every id and name is a neutral placeholder, and every
repository is a temporary fixture.
"""
import json
import pathlib
import subprocess

import pytest
import yaml

from tests import test_hub_slack_clients as C
from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name
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
    "personas": [{"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Short."},
                 {"id": "atlas", "name": "Atlas", "voice": "Kore", "tone": "Plain."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
    "workspaces": [{"id": "T0BETA0001", "label": "beta-team", "class": "owned", "persona": "nova",
                    "channels": [{"id": "C0BETA0001", "name": "beta-room", "mode": "listen"}]}],
}
LOCAL = {"version": 1}


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def repo(tmp_path, name, doc, local=None):
    """A project checkout that is a git repository at origin/dev: `doc` is its committed Slack file, and `local` is the
    profile folder's local file. The bare origin answers the commits the save lands."""
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
    c.write_text(yaml.safe_dump(doc, sort_keys=False))
    git(co, "add", "--", "docs/squad/slack.yaml")
    git(co, "commit", "-q", "-m", "seed the Slack file")
    git(co, "remote", "add", "origin", str(origin))
    git(co, "push", "-q", "origin", "dev")
    git(co, "fetch", "-q", "origin")
    return co, origin


def wire(tmp_path, monkeypatch, hub, projects, locals=None):
    """The hub's own seam: one git checkout and one profile file per project, the client map, and its state directory.
    `projects` maps a slug to its committed document; `locals` maps a slug to its local document."""
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, doc in projects.items():
        local_doc = (locals or {}).get(slug, LOCAL)
        co, origin = repo(tmp_path, slug, doc, local_doc)
        (prof / f"{slug}.sh").write_text(
            f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={slug}}}"; '
            f': "${{SG_CHECKOUT:={co}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
        loc = prof / f"{slug}.slack.local.yaml"
        loc.write_text(yaml.safe_dump(local_doc, sort_keys=False))
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


# ---------------------------------------------------------------- the tree edits a workspace and a channel of another project

def _seat(tmp_path, name, committed, local):
    root = tmp_path / name
    root.mkdir()
    c, loc = root / "slack.yaml", root / f"{name}.slack.local.yaml"
    c.write_text(yaml.safe_dump(committed))
    loc.write_text(yaml.safe_dump(local))
    return {"id": name, "label": name, "checkout": str(root), "committed": committed, "local": local,
            "committed_path": str(c), "local_path": str(loc), "read_defects": [],
            "cf": "slack.yaml", "lf": loc.name}


MAP = [{"id": "acme", "name": "Acme", "default_persona": None,
        "projects": [{"id": "alpha", "follows": True}, {"id": "beta", "follows": True}]}]


@pytest.fixture
def client_map(hub, monkeypatch):
    """The client map of the fixture tree: a project no client names is drawn nowhere (#2922)."""
    monkeypatch.setattr(hub, "_clients_map_read", lambda: (json.loads(json.dumps(MAP)), None))


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def _tree(hub, tmp_path, other=None, other_local=None):
    here = _seat(tmp_path, "alpha", ALPHA, LOCAL)
    beta = _seat(tmp_path, "beta", other or BETA, other_local or LOCAL)
    P = hub.slack_policy()
    return hub._slack_clients_tree_for(P, [here, beta], here["committed_path"]), here, beta


def _project(tree, name):
    return next(n for c in tree["clients"] for n in walk(c) if n["kind"] == "project" and n["name"] == name)


def _controls(node):
    return [c for r in (node.get("settings") or {}).get("rows") or [] for c in r.get("controls") or []]


def test_a_workspace_node_of_another_project_offers_the_persona_and_language_controls(hub, tmp_path, client_map):
    """#3034 acceptance 1: the workspace node of another profile's project shows the Persona and Language selects, and
    each carries the row's own value from that project's file."""
    tree, _, _ = _tree(hub, tmp_path)
    ws = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "workspace")
    controls = {c["field"]: c for c in _controls(ws)}
    assert controls["persona"]["kind"] == "select", controls
    assert controls["persona"]["value"] == "nova", controls
    assert controls["language"]["kind"] == "select" and controls["language"]["value"] == "", controls
    assert {c["scope"] for c in controls.values()} == {"ws:T0BETA0001"}, controls


def test_a_channel_node_of_another_project_offers_the_persona_and_language_controls(hub, tmp_path, client_map):
    """#3034 acceptance 2: the channel node of another profile's project shows the Persona and Language selects, and each
    carries the row's own value."""
    tree, _, _ = _tree(hub, tmp_path)
    channel = next(n for n in walk(_project(tree, "beta")) if n["kind"] == "channel")
    controls = {c["field"]: c for c in _controls(channel)}
    assert controls["persona"]["kind"] == "select" and controls["persona"]["value"] == "", controls
    assert controls["language"]["kind"] == "select" and controls["language"]["value"] == "", controls


def test_every_other_setting_of_another_projects_workspace_and_channel_stays_read_only(hub, tmp_path, client_map):
    """#3034 acceptance 8: the other rows of a channel, and the add and remove operations of both nodes, stay read-only,
    so an operation of another profile's project never reaches this page."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    for node in walk(beta):
        if node is beta:
            continue
        assert not node.get("channel_add") and not node.get("channel_remove"), node["name"]
        for row in (node.get("settings") or {}).get("rows") or []:
            for c in row.get("controls") or []:
                if row.get("key") in ("persona", "language"):
                    assert c["kind"] == "select", (node["name"], row["key"], c)
                else:
                    assert c["kind"] == "read", (node["name"], row["key"], c)


def test_the_workspace_node_names_no_edited_elsewhere_sentence_and_a_channel_names_only_other_settings(
        hub, tmp_path, client_map):
    """#3034 acceptance 7: neither node claims that its Persona and Language are edited on the project's page. The
    workspace shows no sentence at all, and the channel's sentence stays for the settings that page still edits."""
    tree, _, _ = _tree(hub, tmp_path)
    beta = _project(tree, "beta")
    ws = next(n for n in walk(beta) if n["kind"] == "workspace")
    channel = next(n for n in walk(beta) if n["kind"] == "channel")
    assert "elsewhere" not in ws, ws.get("elsewhere")
    for node, sentence in ((ws, False), (channel, True)):
        body = (_slices(hub.PAGE) + f"\nslS={{}};slR={{}};slT={json.dumps(tree)};slTsel={json.dumps(node['id'])};"
                + "globalThis.fetch=()=>new Promise(()=>{});slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
        html = json.loads(_node(body))
        detail = html[html.index("id=sltdetail"):]
        assert "Edited on " not in detail, detail
        assert ("other settings are edited on" in detail) is sentence, detail
