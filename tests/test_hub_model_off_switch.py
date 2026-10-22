"""GitHub issue #1819: a per-row switch that takes a model out of its chain without removing it.

The hub's models panel could only stop a model with ✕, which deletes the entry and loses its effort and hrs. The
switch keeps the row: its place, its rank, its effort and its hrs all stay, and the model is simply out of the chain.
The state lives in models.json beside `efforts` and `hours`, and every consumer of a chain reads that one file, so
nothing caches it: launch.py (which enforces the coding model), mkreq.sh (which names it in the request), consult.sh
(the fallback that asks the coding model directly) and review.sh (the review chain) all skip a switched-off model
exactly like one outside its hours window.

The consumers are bash and one python script, so each test runs the predicate the script itself ships — sliced out of
the file and executed with a models.json of its own — rather than a copy of the rule written again in the test.
"""
import datetime as dt
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from squad import modelreg

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
LAUNCH = SCRIPTS / "launch.py"
MKREQ = SCRIPTS / "mkreq.sh"
CONSULT = SCRIPTS / "consult.sh"
REVIEW = SCRIPTS / "review.sh"

# A models.json shaped like the real one: three chains, one review id carrying the `claude:` prefix review.sh routes by,
# and the window the file that ships today carries (`claude-sonnet-5-5` coding 19-07, so noon is outside it).
MODELS_DOC = {
    "coding_models": ["claude-sonnet-5-5", "claude-deepseek-v4-flash", "gpt-5.6-terra"],
    "review_models": ["gemini-3.8-flash-high", "claude:claude-opus-5-5", "grok-4.5"],
    "vision_models": ["grok-4.5", "claude-sonnet-5-5"],
    "efforts": {"coding_models": {"gpt-5.6-terra": "low"}},
    "hours": {"coding_models": {"claude-sonnet-5-5": "19-07"}},
}
NOON, EIGHT_PM = dt.datetime(2026, 10, 11, 12, 0), dt.datetime(2026, 10, 11, 20, 0)


def _fn(text, opener, end=None):
    """One shell function's source, sliced out of the file that ships it: `opener` to its balanced closing brace.

    Parameter expansions are blanked to the same length first, so the `}` of a `${w%%-*}` inside the body is not read
    as the end of the function. `end` names the terminator instead, for a body that groups commands in braces of its
    own (`{ ... }` inside an if) — a brace count reads that group as the end of the function."""
    i = text.index(opener)
    if end is not None:
        return text[i:text.index(end, i) + len(end)]
    flat = re.sub(r"\$\{[^{}]*\}", lambda m: "X" + " " * (len(m.group(0)) - 1), text)
    i, j, depth = flat.index(opener), flat.index("{", flat.index(opener)), 0
    while j < len(flat):
        depth += (flat[j] == "{") - (flat[j] == "}")
        if depth == 0:
            return text[i:j + 1]
        j += 1
    raise AssertionError(f"{opener} never closes")


def _bash(script, args=()):
    p = subprocess.run(["bash", "-c", script + "\ntrue\n", "_", *args], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_off", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def models(hub, tmp_path, monkeypatch):
    """The panel's own file, in a folder of its own: a Save writes there and nowhere else."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps(MODELS_DOC, indent=2) + "\n")
    monkeypatch.setattr(hub, "MODELS", path)
    return path


def _post(hub, **changes):
    """What the panel posts back on Save: everything models_get() handed it, with `changes` applied."""
    doc = hub.models_get()
    doc.update(changes)
    return hub.models_set(doc)


def _saved(models, canon):
    """One chain as the Save wrote it: the v2 entries, where the switch is `on: False` (SPEC 750)."""
    return json.loads(models.read_text())["chains"][canon]


def _off(entries):
    return {e["id"] for e in entries if e.get("on") is False}


@pytest.mark.heavy
def test_a_rows_switch_round_trips_through_save_and_revert(hub, models):
    """The state survives Save (models.json carries it per chain) and Revert (models_get hands the same thing back)."""
    out = _post(hub, off={"coding_models": {"claude-sonnet-5-5": True}})
    assert out["off"]["coding_models"] == {"claude-sonnet-5-5": True}   # every chain present, like efforts and hours
    assert _off(_saved(models, "coding")) == {"claude-sonnet-5-5"}, "the switch is the entry's `on: false`"


def test_the_switch_is_written_per_chain_so_one_chain_can_hold_it_alone(hub, models):
    _post(hub, off={"review_models": {"grok-4.5": True}})
    assert _off(_saved(models, "review")) == {"grok-4.5"}


def test_switching_a_model_off_leaves_its_rank_effort_and_hours_alone(hub, models):
    """The reason the switch exists: ✕ loses the entry and its settings, the switch keeps both."""
    before = hub.models_get()
    out = _post(hub, off={"coding_models": {"gpt-5.6-terra": True}})
    assert out["coding_models"] == before["coding_models"]        # same order, same place, same rank
    assert out["efforts"]["coding_models"] == before["efforts"]["coding_models"]   # the effort is still the row's
    assert out["hours"]["coding_models"] == before["hours"]["coding_models"]       # and so is the window
    back = hub.models_set({**out, "off": {k: {} for k in ("coding_models", "review_models", "vision_models")}})
    assert back["coding_models"] == before["coding_models"]       # switched back on, same position
    assert back["off"]["coding_models"] == {}
    assert _off(_saved(models, "coding")) == set()                # an entry back on carries no switch at all


def test_switching_every_entry_of_a_chain_off_is_allowed(hub, models):
    """It is a legal state: the consumers then fail on the existing "no model may run now" path, never silently."""
    out = _post(hub, off={"review_models": {m: True for m in ("gemini-3.8-flash-high", "claude-opus-5-5", "grok-4.5")}})
    saved = _saved(models, "review")
    assert [e["id"] for e in saved] == ["gemini-3.8-flash", "claude-opus-5-5", "grok-4.5"]   # order untouched
    assert _off(saved) == {"gemini-3.8-flash", "claude-opus-5-5", "grok-4.5"}                # every row is off
    # the panel draws only the entries a reviewer can run, so the retired grok row is hidden from it, not deleted; the
    # `-high` row reads medium in the panel (this v1 file carries no effort for it), so its suffix goes with the Save
    assert out["review_models"] == ["gemini-3.8-flash", "claude-opus-5-5"]


def test_the_panel_never_sees_a_prefix_it_did_not_offer(hub, models):
    """`claude:` is review.sh's routing prefix; the panel works in bare ids, so the switch does too."""
    out = _post(hub, off={"review_models": {"claude-opus-5-5": True}})
    assert out["off"]["review_models"] == {"claude-opus-5-5": True}
    assert _off(_saved(models, "review")) == {"claude-opus-5-5"}
    assert [e["raw"] for e in _saved(models, "review") if e.get("on") is False] == ["claude:claude-opus-5-5"]


def test_a_model_switched_off_may_not_run(hub, models):
    d = json.loads(models.read_text()) | {"off": {"coding_models": {"gpt-5.6-terra": True}}}
    assert hub.may_run(d, "coding_models", "gpt-5.6-terra") is False
    assert hub.may_run(d, "coding_models", "claude-deepseek-v4-flash") is True


def test_switched_off_and_out_of_window_are_the_same_answer(hub, models):
    """One predicate, two reasons: every consumer asks `may_run`, and both come back False the same way."""
    d = json.loads(models.read_text())
    assert hub.may_run(d, "coding_models", "claude-sonnet-5-5", NOON) is False    # noon is outside its 19-07 window
    assert hub.may_run(d, "coding_models", "gpt-5.6-terra", NOON) is True         # no window: always
    off = {**d, "off": {"coding_models": {"gpt-5.6-terra": True}}}
    assert hub.may_run(off, "coding_models", "gpt-5.6-terra", NOON) is False      # switched off
    assert hub.may_run(off, "coding_models", "claude-sonnet-5-5", EIGHT_PM) is True


def test_the_panel_row_carries_a_switch_before_the_remove_button(hub):
    row = HUB_PY.read_text()
    row = row[row.index("function mdlRender()"):row.index("async function mdlLoad()")]
    assert 'data-sw="${k}|${esc(m)}"' in row
    assert row.index("data-sw=") < row.index("data-rm=")     # it sits in .mb, before ✕
    assert "aria-pressed=" in row and "${​of" not in row


def test_the_switch_reads_as_the_state_it_is_and_the_row_shows_the_model_is_out(hub):
    src = HUB_PY.read_text()
    row = src[src.index("function mdlRender()"):src.index("async function mdlLoad()")]
    assert 'class="mr${i?"":" p"}${of?" off":""}"' in row   # the row is de-emphasised, the order untouched
    assert "· switched off" in row                           # the rank badge says which model is out
    css = src[src.index("<style>"):src.index("</style>")]
    assert ".mr.off .rk" in css and ".mr .mb button.sw" in css


def test_the_switch_toggles_the_panel_state(hub):
    src = HUB_PY.read_text()
    assert "function mdlSetOff(k,m,v)" in src and "else if(t.dataset.sw)" in src


# --- the four consumers of a chain ---------------------------------------------------------------------------------

def _launch_predicate(doc):
    """launch.py's own gate, sliced out and run: `_may_run(chain, mid)` over the models.json this test hands it."""
    block = LAUNCH.read_text()
    block = block[block.index("def _chain("):block.index('cm = [e.raw for e in _chain("coding_models")]')]
    spec = importlib.util.spec_from_file_location("provkey", SCRIPTS / "provkey.py")
    provkey = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provkey)
    ns = {"mdoc": doc, "re": re, "time": __import__("time"), "os": os, "pathlib": pathlib, "provkey": provkey, "modelreg": modelreg}   # the key gate launch.py loads beside itself (#482)
    exec(compile(block, str(LAUNCH), "exec"), ns)      # noqa: S102 -- the shipped predicate, not a copy of it
    return ns["_may_run"]


def test_launch_refuses_to_run_a_model_switched_off():
    may_run = _launch_predicate(dict(MODELS_DOC, off={"coding_models": {"claude-sonnet-5-5": True}}))
    assert may_run("coding_models", "claude-sonnet-5-5") is False      # the chain's head, switched off
    assert may_run("coding_models", "claude-deepseek-v4-flash") is True


def test_launch_skips_it_and_names_the_next_in_window_one():
    """The consequence the issue asks for: the model that may run now is the first entry that is neither off nor out of window."""
    doc = dict(MODELS_DOC, off={"coding_models": {"claude-sonnet-5-5": True}})
    may_run = _launch_predicate(doc)
    assert next((m for m in doc["coding_models"] if may_run("coding_models", m)), None) == "claude-deepseek-v4-flash"


def test_every_coding_model_switched_off_leaves_launch_with_no_model_at_all():
    doc = dict(MODELS_DOC, off={"coding_models": {m: True for m in MODELS_DOC["coding_models"]}})
    may_run = _launch_predicate(doc)
    assert next((m for m in doc["coding_models"] if may_run("coding_models", m)), None) is None
    assert "switched off" in LAUNCH.read_text()      # the refusal names both reasons on the existing path


def _shipped(text, start, stop=None):
    """One assignment line of the shipped script, with `$(dirname "$0")` made the scripts' directory (no $0 under bash -c)."""
    line = next(ln.strip() for ln in text.splitlines() if ln.strip().startswith(start))
    return (line.split(stop)[0] if stop else line).replace('$(dirname "$0")', str(SCRIPTS))


def test_mkreq_skips_a_coding_model_switched_off(tmp_path):
    """mkreq.sh names the model in the request, so it skips it here too — launch.py would refuse the request."""
    models = tmp_path / "models.json"
    models.write_text(json.dumps(dict(MODELS_DOC, off={"coding_models": {"claude-sonnet-5-5": True}})))
    rows = _shipped(MKREQ.read_text(), "rows=$(python3")
    fn = _fn(MKREQ.read_text(), "mayrun() {")
    out = _bash(f'mjson={models}; hh=20; spent() {{ return 1; }}\n{rows}\n{fn}\n'
                'while read -r m w on; do if mayrun "$m" "$w" "$on"; then printf "%s\\n" "$m"; fi; done <<<"$rows" | head -1')
    assert out == "claude-deepseek-v4-flash"


def test_mkreq_still_leaves_an_in_window_model_alone(tmp_path):
    models = tmp_path / "models.json"
    models.write_text(json.dumps(MODELS_DOC))
    fn = _fn(MKREQ.read_text(), "mayrun() {")
    hh = int(subprocess.run(["date", "+%H"], capture_output=True, text=True).stdout)
    inwin, window = ("claude-sonnet-5-5", "19-07") if not 7 <= hh < 19 else ("claude-deepseek-v4-flash", "-")
    out = _bash(f'mjson={models}; hh={hh}; spent() {{ return 1; }}\n{fn}\nif mayrun "$1" "$2" 1; then echo yes; else echo no; fi', [inwin, window])
    assert out == "yes"


def _review_predicates(tmp_path, doc):
    models = tmp_path / "review-models.json"
    models.write_text(json.dumps(doc))
    rows = _shipped(REVIEW.read_text(), "rrows=$(python3", stop="; chain=")
    fns = [_fn(REVIEW.read_text(), "row_of() {"), *(_fn(REVIEW.read_text(), o, end="; }\n") for o in ("hours_ok() {", "off_ok() {", "may_run() {"))]
    return models, "\n".join([rows, *fns])


def test_a_reviewer_switched_off_is_skipped_like_one_out_of_window(tmp_path):
    models, fn = _review_predicates(tmp_path, dict(MODELS_DOC, off={"review_models": {"grok-4.5": True}}))
    ask = f'cfg={models}\n{fn}\nif may_run review_models "$1"; then echo yes; else echo no; fi'
    assert _bash(ask, ["grok-4.5"]) == "no"
    assert _bash(ask, ["gemini-3.8-flash-high"]) == "yes"


def test_a_reviewer_out_of_window_answers_the_same_way(tmp_path):
    """The behaviour the issue names as the reference: outside the window is already skipped, and off rides the same predicate."""
    hh = int(subprocess.run(["date", "+%H"], capture_output=True, text=True).stdout)
    window = f"{(hh + 1) % 24:02d}-{(hh + 2) % 24:02d}"      # a window that cannot contain this hour
    models, fn = _review_predicates(tmp_path, dict(MODELS_DOC, hours={"review_models": {"grok-4.5": window}}))
    ask = f'cfg={models}\n{fn}\nif may_run review_models "$1"; then echo yes; else echo no; fi'
    assert _bash(ask, ["grok-4.5"]) == "no"


def test_the_consult_never_wakes_a_model_switched_off(tmp_path):
    """consult.sh asks the coding model directly when the router hesitated: it must skip a model taken out of the chain."""
    models = tmp_path / "models.json"
    models.write_text(json.dumps(dict(MODELS_DOC, off={"coding_models": {"claude-sonnet-5-5": True}})))
    rows = _shipped(CONSULT.read_text(), "rows=$(python3")
    fn = _fn(CONSULT.read_text(), "pick() {")
    out = _bash(f'here={SCRIPTS}; mjson={models}; hh=20;\n{rows}\n{fn}\nmodel=$(pick); printf "%s\\n" "$model"')
    assert out == "claude-deepseek-v4-flash"


def test_the_consult_has_nothing_to_ask_when_every_coding_model_is_off(tmp_path):
    """No silent fallback: it names no model rather than waking one the owner switched off."""
    models = tmp_path / "models.json"
    models.write_text(json.dumps(dict(MODELS_DOC, off={"coding_models": {m: True for m in MODELS_DOC["coding_models"]}})))
    fn = _fn(CONSULT.read_text(), "pick() {")
    out = _bash(f'mjson={models}; hh=20;\n{fn}\nmodel=$(pick); printf "[%s]\\n" "$model"')
    assert out == "[]"
    assert "no coding model may run now" in CONSULT.read_text()


def test_a_row_removed_with_its_hours_effort_and_switch_saves_and_drops_them(hub, models):
    """✕ takes a row out of the chain, and the panel still posts the row's hrs, effort and switch back (#685): the Save
    writes, and the removed model's settings go with it instead of refusing every Save after a removal."""
    out = _post(hub, coding_models=["claude-deepseek-v4-flash", "gpt-5.6-terra"],
                hours={"coding_models": {"claude-sonnet-5-5": "19-07"}},
                efforts={"coding_models": {"claude-sonnet-5-5": "low", "gpt-5.6-terra": "low"}},
                off={"coding_models": {"claude-sonnet-5-5": True}})
    assert "err" not in out, out
    saved = _saved(models, "coding")
    assert [e.get("raw") or e["id"] for e in saved] == ["claude-deepseek-v4-flash", "gpt-5.6-terra"]
    assert "hrs" not in saved[0] and "on" not in saved[0]          # the removed row took its window and switch with it
    assert saved[1]["effort"] == "low"
