"""Every model reads by one uniform name, and the hub panel row shows that name, not the raw id (#687).

The owner found mixed labels: "Opus 5.5" beside "Claude Haiku 5.5", "DeepSeek V4.1 Flash (tools)", and a raw id
truncated to "cl..." beside each label. The scheme: `<Family> <version> <tier>`, one of five families (Claude, GPT,
Gemini, DeepSeek, Grok), no parentheses, and at most one trailing lowercase variant word ("fast", "no-tools").
The label lives in the one registry (ADR-0147); the panel row renders it and keeps the id in its tooltip.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config/models.registry.json"
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

SCHEME = re.compile(
    r"^(?:Claude (?:Opus|Sonnet|Haiku|Fable) \d+(?:\.\d+)?"
    r"|GPT-\d+(?:\.\d+)?(?: [A-Z][a-z]+)?"
    r"|GPT-OSS \d+B"
    r"|Gemini \d+(?:\.\d+)? (?:Flash|Pro)"
    r"|DeepSeek V\d+(?:\.\d+)? (?:Flash|Pro)"
    r"|Grok \d+(?:\.\d+)?)"
    r"(?: [a-z][a-z-]*)?$")


def _models():
    return json.loads(REGISTRY.read_text())["models"]


def test_every_label_follows_the_scheme():
    bad = {m["id"]: m["label"] for m in _models() if not SCHEME.match(m.get("label") or "")}
    assert bad == {}


def test_two_live_entries_never_share_a_label():
    seen = {}
    for m in _models():
        if not m.get("retired"):
            seen.setdefault(m["label"], []).append(m["id"])
    assert {k: v for k, v in seen.items() if len(v) > 1} == {}


def test_the_deepseek_flash_pair_reads_by_its_variant():
    labels = {m["id"]: m["label"] for m in _models()}
    assert labels["deepseek-v4-flash"] == "DeepSeek V4.1 Flash"
    assert labels["deepseek-flash"] == "DeepSeek V4.1 Flash no-tools"


def _mdl_name(box, mid):
    """The panel's own mdlName, with its own esc, run by node."""
    assert shutil.which("node"), "node is not installed: the panel row guard cannot run, and it never skips"
    src = HUB_PY.read_text()
    esc = re.search(r"^const esc=.*$", src, re.M).group(0)
    fn = re.search(r"^function mdlName\(.*$", src, re.M).group(0)
    js = f"{esc}\n{fn}\nconsole.log(JSON.stringify(mdlName({json.dumps(box)},{json.dumps(mid)})))"
    p = subprocess.run(["node", "-"], input=js, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_a_registered_row_shows_the_label_and_keeps_the_id_out():
    assert _mdl_name({"llm": "Claude Opus 5.5"}, "claude-opus-5-5") == "Claude Opus 5.5"


def test_an_unregistered_row_shows_its_id():
    assert _mdl_name({"llm": None}, "gpt-9-nova") == "gpt-9-nova"


def test_the_row_tooltip_still_names_the_id():
    src = HUB_PY.read_text()
    assert src.count('<span class=mn title="${esc(m)} · ${esc(x.text)}">${mdlName(x,m)}</span>') == 2


# --- the add-a-model dropdowns (#687): the label is the primary text and the id the secondary one. An id no registry
# entry knows (a gateway-only id such as claude-gpt-5.2) is hidden from the dropdown: display never guesses a name,
# and the owner can still type a raw id. The `known` and `router_known` lists stay whole; `suggest` is the view.

import importlib.util  # noqa: E402
import os  # noqa: E402
import tempfile  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_label_scheme", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_suggestion_reads_by_its_label_and_hides_an_unregistered_id(hub):
    got = hub.model_suggestions(["claude-opus-5-5", "claude-gpt-5.2", "claude-gpt-5.2-fast", "grok-4.7"])
    assert got == [{"id": "claude-opus-5-5", "text": "Claude Opus 5.5"}, {"id": "grok-4.7", "text": "Grok 4.7"}]


def test_an_effort_spelling_suggests_its_label_and_its_effort_word(hub):
    got = hub.model_suggestions(["gemini-3.8-flash-high", "gemini-3.8-flash-low"])
    assert got == [{"id": "gemini-3.8-flash-high", "text": "Gemini 3.8 Flash high"},
                   {"id": "gemini-3.8-flash-low", "text": "Gemini 3.8 Flash low"}]


def test_two_spellings_of_one_label_differ_by_their_route(hub):
    got = {s["id"]: s["text"] for s in hub.model_suggestions(["claude-opus-5-5", "claude:claude-opus-5-5"])}
    assert got == {"claude-opus-5-5": "Claude Opus 5.5 · gateway", "claude:claude-opus-5-5": "Claude Opus 5.5 · claude CLI"}


def test_every_suggestion_text_is_unique_in_its_list(hub, monkeypatch):
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: ["claude-gpt-5.2", "claude-gpt-5.6-terra", "claude-grok-4.7"])
    for ids in (hub.known_models({}), hub.router_known()):
        texts = [s["text"] for s in hub.model_suggestions(ids)]
        assert len(texts) == len(set(texts))


def test_the_dropdowns_render_the_label_first_and_map_it_back_to_the_id():
    src = HUB_PY.read_text()
    assert '<datalist id=mk>${(mdl.suggest||{}).known' in src
    assert '<datalist id=mkr>${(mdl.suggest||{}).router_known' in src
    assert '<option value="${esc(s.text)}" label="${esc(s.id)}">' in src
    assert src.count("sugId(") >= 3   # one helper, used by both Add buttons
