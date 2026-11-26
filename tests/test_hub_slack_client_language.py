"""GitHub issue #3035: a client default language writes each following project's language (SPEC 3031).

A client has a default language beside its default persona. The client map stores that language and, for each project,
a `follows_language` flag that is separate from the persona `follows` flag. A change of the client language writes
`project.language` into each project that follows it, through the project's own save path. A project whose Slack file
does not read is named before any write and is not written, and a project that does not validate is named as refused
while the others are written. The change shows no proposed-writes table, and no client name enters a committed file.

The seams under test are the hub's reads `slack_tree` and `slack_clients_preview`, its writes `slack_clients_save`,
`slack_clients_post` and `slack_files_set`, and the theme's renderer run in node. No browser runs here. Every id and
name is a neutral placeholder.
"""
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

FOLLOWER = {
    "version": 1,
    "project": {"language": "en-US", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned",
         "channels": [{"id": "C0PROJ0001", "name": "project", "mode": "listen+speak", "roles": ["manager"]}]},
    ],
}
OWN = {
    "version": 1,
    "project": {"language": "pt-BR", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."}],
    "workspaces": [{"id": "T0TEAM0002", "label": "own", "class": "owned", "channels": []}],
}
EMPTY = {}
LOCAL = {"version": 1}


@pytest.fixture(scope="module")
def hub():
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_client_language", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def seat(tmp_path, name, committed):
    co = tmp_path / "checkouts" / name
    c = co / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True)
    if committed is not None:
        c.write_text(yaml.safe_dump(committed, sort_keys=False))
    return co, c


def proto(tmp_path, slug, project, checkout):
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    (prof / f"{slug}.sh").write_text(
        f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:={project}}}"; '
        f': "${{SG_CHECKOUT:={checkout}}}"; : "${{SG_SQUAD:={ROOT}}}"\n')
    loc = prof / f"{project}.slack.local.yaml"
    loc.write_text(yaml.safe_dump(LOCAL, sort_keys=False))
    return prof, loc


def client_map(prof, clients):
    (prof / "clients.local.yaml").write_text(yaml.safe_dump({"version": 1, "clients": clients}, sort_keys=False))


def read_map(prof):
    return yaml.safe_load((prof / "clients.local.yaml").read_text())


def wire(tmp_path, monkeypatch, hub, clients, seats):
    prof = tmp_path / "profiles"
    prof.mkdir(exist_ok=True)
    made = {}
    for slug, committed in seats.items():
        co, c = seat(tmp_path, slug, committed)
        proto(tmp_path, slug, slug, co)
        made[slug] = (co, c, prof / f"{slug}.slack.local.yaml")
    client_map(prof, clients)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "ambient-checkout"))
    monkeypatch.setattr(hub, "STATE", tmp_path / "state")
    return prof, made


def stub_write(hub, monkeypatch):
    """A stub of the project save path: it writes the committed document to its own file and answers a sha."""
    calls = []

    def fake(body, committed=None, local=None, loop=None, name=None):
        calls.append({"committed": committed, "doc": body.get("committed")})
        pathlib.Path(committed).write_text(yaml.safe_dump(body["committed"], sort_keys=False))
        return {"sha": "stub"}

    monkeypatch.setattr(hub, "slack_files_set", fake)
    return calls


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_name(out, name):
    tree = out.get("tree") or out
    for client in tree["clients"]:
        for node in walk(client):
            if node["name"] == name:
                return node
    raise AssertionError(f"the tree names no node {name!r}")


# ---------------------------------------------------------------- the client node and the page

def test_the_client_node_offers_a_language_select_and_keeps_a_default_language(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": "blue", "default_language": "pt-BR",
                     "projects": ["alpha"]}], {"alpha": FOLLOWER})
    client = (hub.slack_tree()["clients"])[0]
    assert client["default_language"] == "pt-BR", client
    P = hub.slack_policy()
    assert [o["value"] for o in client["language_options"]] == list(P.LANGUAGES), client["language_options"]


def test_the_project_node_carries_its_language_follow_flag(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_language": "pt-BR",
                     "projects": [{"id": "alpha", "follows_language": True},
                                  {"id": "beta", "follows_language": False}]}],
                   {"alpha": FOLLOWER, "beta": OWN})
    assert by_name(hub.slack_tree(), "alpha")["follows_language"] is True
    assert by_name(hub.slack_tree(), "beta")["follows_language"] is False


def _node(body):
    p = subprocess.run(["node", "-"], input=body, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _slices(page):
    esc = re.search(r"^const esc=.*$", page, re.M).group(0)
    sett = page[page.index("// SETT{"):page.index("// }SETT")]
    sl = page[page.index("let slS=null"):page.index("async function slLoad()")]
    stub = 'const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:""});'
    return "\n".join([stub, esc, sett, sl])


def render(hub, out, sel=None):
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};" + (f"slTsel={json.dumps(sel)};" if sel else "") +
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_client_detail_draws_the_language_select_and_a_follow_toggle_for_each_project(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_language": "pt-BR",
                     "projects": [{"id": "alpha", "follows_language": True},
                                  {"id": "beta", "follows_language": False}]}],
                   {"alpha": FOLLOWER, "beta": OWN})
    out = hub.slack_overview()
    html = render(hub, out, sel="c0")
    assert re.search(r'aria-label="Default language"', html), html[:800]
    assert re.search(r'<option value="pt-BR" selected>', html), html[:1400]
    assert re.search(r'data-slfollang="[^"]+"', html), "the page draws no language follow toggle"
    assert re.search(r'checked[^>]*data-slfollang="[^"]+"|data-slfollang="[^"]+"[^>]*checked', html), \
        "the following project's toggle is not drawn on"


def test_a_language_save_draws_no_proposed_writes_table(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_language": "en-US", "projects": ["alpha"]}],
                   {"alpha": FOLLOWER})
    out = hub.slack_overview()
    out["tree"]["clients"][0]["proposal"] = hub.slack_clients_preview({"client": "one", "language": "pt-BR"})["rows"]
    html = render(hub, out, sel="c0")
    assert "Proposed writes" not in html, html[:600]
    assert "writes pt-BR" not in html, html[:600]


# ---------------------------------------------------------------- the writes

def test_a_change_writes_the_language_of_each_following_project(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True},
                                     {"id": "owners", "follows_language": False}]}],
                      {"alpha": FOLLOWER, "owners": OWN})
    calls = stub_write(hub, monkeypatch)
    before = made["owners"][1].read_text()
    out = hub.slack_clients_save({"client": "one", "language": "pt-BR", "confirm": True})
    assert out.get("err") is None, out
    assert [s["project"] for s in out["saved"]] == ["alpha"], out
    rows = {r["project"]: r["result"] for r in out["rows"]}
    assert rows["owners"] == "kept: the project follows its own language", rows
    assert yaml.safe_load(made["alpha"][1].read_text())["project"]["language"] == "pt-BR"
    assert made["owners"][1].read_text() == before, "the unfollowing project's file changed"
    assert read_map(prof)["clients"][0]["default_language"] == "pt-BR", read_map(prof)
    assert [c["committed"] for c in calls] == [str(made["alpha"][1])], calls


def test_the_answer_names_an_unknown_project_a_project_with_no_slack_and_an_unreadable_file_apart(
        hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True},
                                     {"id": "empty", "follows_language": True},
                                     {"id": "broken", "follows_language": True},
                                     {"id": "ghost", "follows_language": True}]}],
                      {"alpha": FOLLOWER, "empty": EMPTY, "broken": OWN})
    made["broken"][1].chmod(0o000)
    try:
        rows = {r["project"]: r["result"] for r in
                hub.slack_clients_preview({"client": "one", "language": "pt-BR"})["rows"]}
    finally:
        made["broken"][1].chmod(0o644)
    assert rows["ghost"].startswith("unknown project:"), rows
    assert rows["empty"].startswith("no Slack file:"), rows
    assert rows["broken"].startswith("refused:") and "slack.yaml" in rows["broken"], rows
    assert len({rows["ghost"].split(":")[0], rows["empty"].split(":")[0], "refused"}) == 3, rows


def test_an_unreadable_file_is_named_before_any_write_and_is_not_written(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True},
                                     {"id": "broken", "follows_language": True}]}],
                      {"alpha": FOLLOWER, "broken": OWN})
    calls = stub_write(hub, monkeypatch)
    made["broken"][1].chmod(0o000)
    try:
        out = hub.slack_clients_save({"client": "one", "language": "pt-BR", "confirm": True})
    finally:
        made["broken"][1].chmod(0o644)
    assert out.get("err") is None, out
    assert [s["project"] for s in out["saved"]] == ["alpha"], out
    refused = {r["project"]: r["result"] for r in out["refused"]}
    assert "slack.yaml" in refused["broken"], refused
    assert [c["committed"] for c in calls] == [str(made["alpha"][1])], "the unreadable project was written"


def test_a_project_that_does_not_validate_is_refused_and_the_others_are_written(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True},
                                     {"id": "beta", "follows_language": True}]}],
                      {"alpha": FOLLOWER, "beta": OWN})
    guests = {"version": 1, "workspaces": [
        {"id": "T0GUEST001", "label": "g1", "class": "guest", "channels": []},
        {"id": "T0GUEST002", "label": "g2", "class": "guest", "channels": []}]}
    made["beta"][2].write_text(yaml.safe_dump(guests, sort_keys=False))
    beta_doc = yaml.safe_load(made["beta"][1].read_text())
    beta_doc["project"]["language"] = "en-US"
    made["beta"][1].write_text(yaml.safe_dump(beta_doc, sort_keys=False))
    before = made["beta"][1].read_text()
    stub_write(hub, monkeypatch)
    out = hub.slack_clients_save({"client": "one", "language": "pt-BR", "confirm": True})
    assert out.get("err") is None, out
    assert [s["project"] for s in out["saved"]] == ["alpha"], out
    refused = {r["project"]: r["result"] for r in out["refused"]}
    assert refused["beta"].startswith("refused:") and "second guest" in refused["beta"], refused
    assert made["beta"][1].read_text() == before, "the refused project's language changed"
    assert read_map(prof)["clients"][0]["default_language"] == "pt-BR", "a partial failure dropped the default"


def test_a_follow_off_makes_an_exception_that_a_later_change_keeps(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True},
                                     {"id": "beta", "follows_language": True}]}],
                      {"alpha": FOLLOWER, "beta": OWN})
    stub_write(hub, monkeypatch)
    off = hub.slack_clients_post({"op": "follow_language", "client": "one", "project": "beta",
                                  "follows": False, "confirm": True})
    assert off.get("err") is None, off
    assert read_map(prof)["clients"][0]["projects"][1]["follows_language"] is False, read_map(prof)
    before = made["beta"][1].read_text()
    hub.slack_clients_save({"client": "one", "language": "es-ES", "confirm": True})
    assert made["beta"][1].read_text() == before, "an exception kept its own language"
    assert yaml.safe_load(made["alpha"][1].read_text())["project"]["language"] == "es-ES"


def test_a_follow_on_writes_the_client_default_language_to_that_project(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "es-ES",
                        "projects": [{"id": "beta", "follows_language": False}]}], {"beta": OWN})
    stub_write(hub, monkeypatch)
    on = hub.slack_clients_post({"op": "follow_language", "client": "one", "project": "beta",
                                 "follows": True, "confirm": True})
    assert on.get("err") is None, on
    assert yaml.safe_load(made["beta"][1].read_text())["project"]["language"] == "es-ES"
    assert read_map(prof)["clients"][0]["projects"][0]["follows_language"] is True, read_map(prof)


def test_a_direct_edit_of_a_following_project_language_turns_its_follow_flag_off(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "pt-BR",
                        "projects": [{"id": "alpha", "follows": False, "follows_language": True}]}],
                      {"alpha": FOLLOWER})
    monkeypatch.setattr(hub, "_slack_provision", lambda wt, loop: ("", False))
    monkeypatch.setattr(hub, "_slack_land_check", lambda wt: (None, None))
    monkeypatch.setattr(hub, "_slack_land", lambda wt, rel, msg, base=None: ("sha", None))
    monkeypatch.setattr(hub, "_slack_record", lambda path, sha, committed=True: None)
    new = yaml.safe_load(yaml.safe_dump(FOLLOWER))
    new["project"]["language"] = "es-ES"
    out = hub.slack_files_set({"committed": new, "local": LOCAL, "confirm": True},
                              committed=str(made["alpha"][1]), local=str(made["alpha"][2]))
    assert out.get("err") is None, out
    client = read_map(prof)["clients"][0]
    assert client["projects"][0]["follows_language"] is False, client
    assert client["projects"][0]["follows"] is False, "the persona follow flag changed"


def test_no_client_name_or_id_enters_a_committed_file(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "secret-client", "name": "Secret Client", "default_language": "en-US",
                        "projects": [{"id": "alpha", "follows_language": True}]}], {"alpha": FOLLOWER})
    stub_write(hub, monkeypatch)
    hub.slack_clients_save({"client": "secret-client", "language": "pt-BR", "confirm": True})
    text = made["alpha"][1].read_text()
    assert "secret-client" not in text and "Secret Client" not in text, text
    assert yaml.safe_load(text)["project"]["language"] == "pt-BR"


def test_a_language_the_policy_refuses_is_refused_before_any_write(hub, tmp_path, monkeypatch):
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_language": "en-US", "projects": ["alpha"]}],
                      {"alpha": FOLLOWER})
    calls = stub_write(hub, monkeypatch)
    before = made["alpha"][1].read_text()
    out = hub.slack_clients_save({"client": "one", "language": "xx-XX", "confirm": True})
    assert out.get("err") and "language" in out["err"].lower(), out
    assert calls == [] and made["alpha"][1].read_text() == before
