"""Hub board: a card names the run that holds it, read from the seam (#241).

The pass asks the seam for every holder in one read (`State.holders`), keeps it on the card's row, and
board() turns it into the text the card wears; the page paints what the row carries (ADR-0137). A card no
run holds draws nothing, and a holder read that fails keeps the holder the last good pass read.

Fixture style follows tests/test_hub_board_task_count.py.
"""
import importlib.util
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
HELD = {"run": "run-20261013-080000-abc123", "started": "2026-10-13T08:00:00Z"}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_holder", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_gh(items):
    def run(cmd, **kw):
        s = " ".join(cmd)
        if "projectsV2" in s and "/items" in s:
            out = "".join(json.dumps(i) + "\n" for i in items) + json.dumps({"_page": len(items)}) + "\n"
        elif "blockedBy" in s:
            out = json.dumps({"data": {"repository": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}})
        elif "pullRequests(first:100" in s:
            out = json.dumps({"data": {"repository": {"pullRequests": {"nodes": []}}}})
        elif "search/issues" in s:
            out = "0\n"
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def _pass(hub, monkeypatch, holders, cache=None):
    seam = hub.board_seam()
    monkeypatch.setattr(seam.binding, "status_field", lambda: {"id": "PVTSSF_status", "opt": {}})
    monkeypatch.setattr(seam, "holders", holders)
    monkeypatch.setattr(hub, "board_cache", cache or {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None})
    monkeypatch.setattr(hub.subprocess, "run", _fake_gh([{"n": 241, "t": "held", "s": "open", "st": "In Progress", "l": []},
                                                         {"n": 242, "t": "free", "s": "open", "st": "Todo", "l": []}]))
    hub.board_pass()


def _row(data, n):
    return next(x for g in data["groups"] for x in g["items"] if x["n"] == n)


def test_a_held_card_names_its_run_from_the_seam(hub, monkeypatch):
    _pass(hub, monkeypatch, lambda: {"241": dict(HELD)})
    data = hub.board()
    assert _row(data, 241)["ho"] == "held · run run-20261013-080000-abc123, no record", "no record of the run is no live process (#1661)"
    assert _row(data, 241)["hs"] == "ended"
    assert "ho" not in _row(data, 242), "a card no run holds names no holder"


def test_a_failed_holder_read_keeps_the_last_holder(hub, monkeypatch):
    _pass(hub, monkeypatch, lambda: {"241": dict(HELD)})
    last = hub.board_cache

    def refused():
        raise RuntimeError("HTTP 502")
    _pass(hub, monkeypatch, refused, cache=last)
    assert _row(hub.board(), 241)["ho"].startswith("held · run run-20261013-080000-abc123")


def test_the_card_paints_the_holder_the_row_carries(hub):
    line = re.search(r"^const cards=.*$", hub.PAGE, re.M).group(0)
    script = (Path(__file__).resolve().parent.parent.joinpath("contrib", "squad-goal", "scripts", "board-lanes.js").read_text() + "const SLOTS=specSlots([]);\n" +   # the lane rules the page inlines (#917)
              'const esc=s=>String(s);const isPR=n=>false;const colOf=()=>"#000000";const tint=()=>"";'
              'const lbc=()=>"";const gstrip=()=>"";const age=()=>"";const ageC=()=>"";const bo={labels:[]};const CN=["Todo"];\n'
              + line + '\nconst I=[{n:241,t:"held",l:[],ho:"held by run-x since then",hs:"running"}];console.log(cards({name:"Todo"},I,I));\n')
    out = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    # The chip names the holder (#1664): "Squad" now, "Squad · taken over <time>" once a takeover marker names it;
    # the full holder text is its tooltip.
    assert '<span class=hld title="held by run-x since then">Squad · running</span>' in out.stdout   # a live run says so (#1661)


def test_an_epoch_start_is_shown_as_a_utc_date(hub):
    h = {"h": {"run": "run-x", "started": "1791928601.68213"}}
    assert hub.holder_text(h) == "held by run-x since 2026-10-13 21:56 UTC"
