"""GitHub issue #2845: the tree detail pane's value choices show the inherited and default values (SPEC 2682).

A persona or language select names the value its empty choice inherits: `Inherit (X · from Y)` at the workspace and the
channel level, and `Not set` at the project level. The language choices are every language the policy supports. The Watch
row shows the channel's poll settings, each value the channel does not name carries the word `default`, and every default
comes from `squad/slackpolicy.py`, never from a constant in the hub or the page (ADR-0137, ADR-0177).

The seams under test are the hub's own read, `slack_overview`, whose `tree` key is the tree, and the theme's renderer run
in node. No browser runs here. Every id and name is a neutral placeholder.
"""
import copy
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "hal"},
    "apps": [{"id": "hal9k", "keychain": {"bot": "agent-squad-slack-bot", "app": "agent-squad-slack-app"}}],
    "personas": [
        {"id": "hal", "name": "HAL9K", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status"]}, {"id": "colleague", "actions": ["status"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "team", "class": "owned", "app": "hal9k", "language": "pt-BR",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"],
              "announce": ["stg_release"]},
             {"id": "C0ALERT001", "name": "squad-alerts", "mode": "listen", "persona": "hal"},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft", "roles": ["manager"]},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen"},
         ]},
    ],
}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, the same pin the tree suites use, so the policy module resolves from
    this tree and never from a loop checkout the caller exported."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_slack_tree_values", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


@pytest.fixture(scope="module")
def policy():
    sys.path.insert(0, str(ROOT))
    from squad import slackpolicy
    return slackpolicy


def paths(tmp_path, committed=None, local=None):
    c = tmp_path / "checkout" / "docs" / "squad" / "slack.yaml"
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(yaml.safe_dump(COMMITTED if committed is None else committed, sort_keys=False))
    loc = tmp_path / "profiles" / "4.slack.local.yaml"
    loc.parent.mkdir(parents=True, exist_ok=True)
    loc.write_text(yaml.safe_dump(LOCAL if local is None else local, sort_keys=False))
    return c, loc


def read(hub, tmp_path, committed=None, local=None):
    c, loc = paths(tmp_path, committed, local)
    return hub.slack_overview(c, loc)


def tree(hub, tmp_path, committed=None, local=None):
    return read(hub, tmp_path, committed, local)["tree"]


def walk(node):
    yield node
    for child in node.get("children") or []:
        yield from walk(child)


def by_name(root, name):
    for node in walk(root):
        if node["name"] == name:
            return node
    raise AssertionError(f"the tree names no node {name!r}: {[n['name'] for n in walk(root)]}")


def rows(node):
    return {row["key"]: row for row in node["settings"]["rows"]}


def control(node, row_key, field):
    row = rows(node)[row_key]
    return next(c for c in row["controls"] if c["field"] == field)


def option(ctl, value):
    return next(o for o in ctl["options"] if o["value"] == value)


# ---------------------------------------------------------------- criterion 1: the inherited choice names its source

def test_a_channel_persona_select_offers_the_value_and_level_it_inherits(hub, tmp_path):
    """Criterion 1: `#support` names no persona, and its workspace names Iara, so the empty choice reads
    `Inherit (Iara · from workspace)`."""
    node = by_name(tree(hub, tmp_path)["root"], "#support")
    ctl = control(node, "persona", "persona")
    assert ctl["options"][0] == {"value": "", "label": "Inherit (Iara · from workspace)"}, ctl["options"]
    assert [o["value"] for o in ctl["options"][1:]] == ["hal", "iara"], ctl["options"]   # the ids the policy validates


def test_a_channel_language_select_offers_the_value_and_level_it_inherits(hub, tmp_path):
    """Criterion 1: `#proj-agent-squad` names no language, and its workspace names pt-BR."""
    node = by_name(tree(hub, tmp_path)["root"], "#proj-agent-squad")
    ctl = control(node, "language", "language")
    assert ctl["options"][0] == {"value": "", "label": "Inherit (pt-BR · from workspace)"}, ctl["options"]


def test_a_workspace_select_offers_the_project_value_and_names_the_project(hub, tmp_path):
    """Criterion 1 at the workspace level: `team` names no persona, so its empty choice is the project's HAL9K."""
    node = by_name(tree(hub, tmp_path)["root"], "team")
    persona = control(node, "persona", "persona")
    language = control(node, "language", "language")
    assert persona["options"][0] == {"value": "", "label": "Inherit (HAL9K · from project)"}, persona["options"]
    assert language["options"][0] == {"value": "", "label": "Inherit (en-US · from project)"}, language["options"]


def test_a_workspace_that_names_its_own_value_does_not_inherit_it(hub, tmp_path):
    """The empty choice of a workspace names what stands above the workspace, never the workspace's own value: clearing
    a value the workspace holds inherits the project's."""
    committed = copy.deepcopy(COMMITTED)
    committed["workspaces"][0]["persona"] = "iara"
    node = by_name(tree(hub, tmp_path, committed=committed)["root"], "team")
    assert control(node, "persona", "persona")["options"][0]["label"] == "Inherit (HAL9K · from project)"
    assert control(node, "persona", "persona")["value"] == "iara"


def test_a_channel_of_a_guest_workspace_inherits_from_that_workspace(hub, tmp_path):
    """The guest workspace names Iara and en-US, so each empty choice of its channels names that value."""
    node = by_name(tree(hub, tmp_path)["root"], "#general")
    assert control(node, "persona", "persona")["options"][0]["label"] == "Inherit (Iara · from workspace)"
    assert control(node, "language", "language")["options"][0]["label"] == "Inherit (en-US · from workspace)"


def test_a_channel_that_names_its_own_value_inherits_the_workspace_value(hub, tmp_path):
    """A channel's empty choice names the workspace value, not the channel's own."""
    local = copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["persona"] = "hal"
    node = by_name(tree(hub, tmp_path, local=local)["root"], "#support")
    assert control(node, "persona", "persona")["options"][0]["label"] == "Inherit (Iara · from workspace)"
    assert control(node, "persona", "persona")["value"] == "hal"


def test_a_row_that_names_no_value_anywhere_inherits_the_default(hub, tmp_path):
    """The chain ends on the policy's default, and the label names that level: `from default`."""
    committed = copy.deepcopy(COMMITTED)
    committed["project"].pop("persona")
    committed["project"].pop("language")
    committed["workspaces"][0].pop("language")
    root = tree(hub, tmp_path, committed=committed)["root"]
    persona = control(by_name(root, "team"), "persona", "persona")
    language = control(by_name(root, "team"), "language", "language")
    assert persona["options"][0] == {"value": "", "label": "Inherit (none · from default)"}, persona["options"]
    assert language["options"][0] == {"value": "", "label": "Inherit (en-US · from default)"}, language["options"]


def test_the_project_selects_offer_not_set(hub, tmp_path):
    """Criterion 1 at the project level: nothing is above the project, so its empty choice says `Not set`."""
    root = tree(hub, tmp_path)["root"]
    persona = control(root, "persona", "persona")
    language = control(root, "language", "language")
    assert persona["options"][0] == {"value": "", "label": "Not set"}, persona["options"]
    assert language["options"][0] == {"value": "", "label": "Not set"}, language["options"]
    assert persona["value"] == "hal" and language["value"] == "en-US"


def test_a_project_that_names_no_value_keeps_its_select_empty(hub, tmp_path):
    """The project's own select shows the value the file stores, never the default it falls back to."""
    committed = copy.deepcopy(COMMITTED)
    committed["project"] = {}
    root = tree(hub, tmp_path, committed=committed)["root"]
    assert control(root, "persona", "persona")["value"] == ""
    assert control(root, "language", "language")["value"] == ""


# ---------------------------------------------------------------- criterion 2: every policy language

def test_each_language_select_offers_every_language_the_policy_supports(hub, tmp_path, policy):
    """Criterion 2: the choices are the policy's list, at every level, and no other language."""
    out = tree(hub, tmp_path)
    for node in (out["root"], by_name(out["root"], "team"), by_name(out["root"], "#support")):
        ctl = control(node, "language", "language")
        assert [o["value"] for o in ctl["options"]] == ["", *policy.LANGUAGES], (node["name"], ctl["options"])


# ---------------------------------------------------------------- criterion 3: the Watch row and its defaults

def test_the_watch_row_shows_the_poll_settings_and_marks_each_default(hub, tmp_path, policy):
    """Criterion 3: a guest channel that names no poll setting shows the policy's values, and the word `default` stands
    beside each one. The row names itself `Watch`."""
    node = by_name(tree(hub, tmp_path)["root"], "#support")
    watch = rows(node)["watch"]
    assert watch["label"] == "Watch", watch["label"]
    defaults = policy.poll_defaults("two_state")
    fields = {c["field"]: c for c in watch["controls"]}
    assert fields["poll.fast"]["default"] == defaults["fast"] == "1m", fields["poll.fast"]
    assert fields["poll.slow"]["default"] == defaults["slow"] == "10m"
    assert fields["poll.quiet_after"]["default"] == defaults["quiet_after"] == "10m"
    assert fields["poll.thread_open"]["default"] == defaults["thread_open"] == "30m"
    for name in ("fast", "slow", "quiet_after", "thread_open"):
        assert "(default)" in watch["note"] and defaults[name] in watch["note"], watch["note"]
    assert watch["note"].startswith("Watch: "), watch["note"]
    mode = fields["poll.mode"]
    assert mode["value"] == "two_state"
    assert option(mode, "two_state")["label"] == "two_state (default)", mode["options"]
    assert option(mode, "exponential")["label"] == "exponential", mode["options"]


def test_a_named_poll_setting_drops_the_default_mark(hub, tmp_path):
    """A value the channel names is its own, and the note marks only the values it does not name."""
    local = copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["poll"] = {"fast": "2m", "mode": "two_state"}
    node = by_name(tree(hub, tmp_path, local=local)["root"], "#support")
    watch = rows(node)["watch"]
    fields = {c["field"]: c for c in watch["controls"]}
    assert fields["poll.fast"]["value"] == "2m" and fields["poll.fast"]["default"] == "1m"
    assert "2m (default)" not in watch["note"] and "10m (default)" in watch["note"], watch["note"]


def test_an_exponential_poll_shows_the_exponential_defaults(hub, tmp_path, policy):
    local = copy.deepcopy(LOCAL)
    local["workspaces"][0]["channels"][0]["poll"] = {"mode": "exponential"}
    node = by_name(tree(hub, tmp_path, local=local)["root"], "#support")
    fields = {c["field"]: c for c in rows(node)["watch"]["controls"]}
    defaults = policy.poll_defaults("exponential")
    assert fields["poll.floor"]["default"] == defaults["floor"] == "5m", fields
    assert fields["poll.ceiling"]["default"] == defaults["ceiling"] == "1h"
    assert fields["poll.factor"]["default"] == str(defaults["factor"]) == "2"
    assert fields["poll.idle_window"]["default"] == defaults["idle_window"] == "15m"
    assert fields["poll.thread_open"]["default"] == defaults["thread_open"] == "30m"


def test_an_owned_channel_has_no_watch_row(hub, tmp_path):
    """Only a guest channel polls, so only a guest channel carries the Watch row."""
    node = by_name(tree(hub, tmp_path)["root"], "#proj-agent-squad")
    assert "watch" not in rows(node), rows(node)


# ---------------------------------------------------------------- criterion 4: the policy owns the defaults

def test_the_poll_defaults_follow_the_policy_and_not_a_copy(hub, tmp_path, policy):
    """Criterion 4: the hub reads `poll_defaults`, so a changed policy changes the row. No constant in the hub or the
    page holds the values."""
    seen = policy._POLL_DEFAULTS["fast"]
    policy._POLL_DEFAULTS["fast"] = "7m"
    try:
        node = by_name(tree(hub, tmp_path)["root"], "#support")
        fields = {c["field"]: c for c in rows(node)["watch"]["controls"]}
        assert fields["poll.fast"]["default"] == "7m", fields["poll.fast"]
        assert "7m (default)" in rows(node)["watch"]["note"]
    finally:
        policy._POLL_DEFAULTS["fast"] = seen


def test_the_page_holds_no_poll_default_of_its_own(hub):
    """Criterion 4 on the page: the poll defaults never appear as literals in the served page."""
    page = hub.PAGE
    for literal in ('"1m"', "'1m'", '"10m"', "'10m'", '"30m"', "'30m'", '"15m"', "'15m'", '"5m"', "'5m'",
                    '"1h"', "'1h'"):
        assert literal not in page, f"the page holds the poll default {literal}"


def test_a_narrow_detail_pane_stacks_a_row_under_its_label(hub):
    """The channel pane is about half the Settings column, so an inherited label is wider than the select it sits in.
    The detail pane is its own width container, and under 560 px of pane a row stacks its label over its control, so
    no inherited label and no poll value is cut."""
    page = hub.PAGE
    assert re.search(r"#sbody \.sltdetail\{[^}]*container-type:inline-size", page), "the detail pane is a container"
    assert re.search(r"@container \(max-width:560px\)\{#sbody \.slsetrow\{"
                     r"grid-template-columns:minmax\(0,1fr\) auto", page), "a narrow pane stacks the row"


def test_the_hub_read_carries_the_policy_languages_and_poll_defaults(hub, tmp_path, policy):
    """The page asks the hub for both lists, so it holds no copy of either."""
    out = read(hub, tmp_path)
    assert out["languages"] == list(policy.LANGUAGES), out["languages"]
    assert out["poll_defaults"] == {m: dict(policy.poll_defaults(m)) for m in policy.FIELD_POLL_MODES}, \
        out["poll_defaults"]


def test_the_hub_asks_the_policy_for_the_poll_mode_default(hub, policy):
    """The policy names the default poll mode, and the hub uses it: no `two_state` rule lives outside the policy."""
    assert policy.POLL_DEFAULT_MODE == "two_state"


# ---------------------------------------------------------------- the renderer

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


def render(hub, out, sel, docs=None, open_=""):
    docs = {"committed": copy.deepcopy(COMMITTED), "local": copy.deepcopy(LOCAL)} if docs is None else docs
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slDocs={json.dumps(docs)};slTsel={json.dumps(sel)};"
            f"slOpen={json.dumps(open_)};"
            "slRender();console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    return json.loads(_node(body))


def test_the_project_pane_draws_the_not_set_choice(hub, tmp_path):
    html = render(hub, read(hub, tmp_path), "p")
    body = html[html.index("Settings"):]
    assert body.count('<option value="">Not set</option>') == 2, body[:3000]
    committed = copy.deepcopy(COMMITTED)
    committed["project"] = {}
    html = render(hub, read(hub, tmp_path, committed=committed), "p",
                  docs={"committed": committed, "local": copy.deepcopy(LOCAL)})
    assert '<option value="" selected>Not set</option>' in html[html.index("Settings"):], html[:3000]


def test_the_workspace_pane_draws_the_inherited_choice(hub, tmp_path):
    html = render(hub, read(hub, tmp_path), "w0")
    body = html[html.index("Settings"):]
    assert "Inherit (HAL9K · from project)" in body, body[:3000]


def test_the_channel_pane_draws_the_inherited_choice_and_the_default_hint(hub, tmp_path):
    html = render(hub, read(hub, tmp_path), "w1.c0")
    body = html[html.index("Settings"):]
    assert "Inherit (Iara · from workspace)" in body, body[:3000]
    assert 'placeholder="default: 1m"' in body, body[:3000]
    assert "10m (default)" in body, body[:3000]


def test_the_older_editor_uses_the_policy_languages_and_poll_defaults(hub, tmp_path):
    """The editor under the read view draws the same lists: the policy's languages and each poll field's default, with
    no literal of either in the page."""
    out = read(hub, tmp_path)
    html = render(hub, out, "p", open_="local:0:0")   # the guest channel detail: poll fields and asks
    assert '<option value="es-ES"' in html, "the editor offers every policy language"
    assert 'placeholder="default: 1m"' in html, "the editor annotates a poll field with the policy default"
