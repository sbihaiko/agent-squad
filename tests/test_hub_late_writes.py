"""The Monitor shows late board writes and their lag by actor (#1022, ADR-0164). The hub computes the rows from /api/load;
the page draws them and computes nothing."""
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB_PY = SCRIPTS / "hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_SHARED"] = tempfile.mkdtemp()
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("hub_late_writes", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_load_payload_carries_the_late_writes_by_actor(hub, monkeypatch):
    import issue_ledger
    monkeypatch.setitem(hub._LATE, "v", None)
    issue_ledger.board(f"{hub.REPO}#11", "run-q", "Doing", "/req/q", instant=100.0)
    issue_ledger.boardw(f"{hub.REPO}#11", "run-q", "Doing", actor="autobug dispatch")
    late = hub.load_payload()["late"]
    assert late["late"] == 1 and late["approximate"] == 1
    assert late["rows"][0]["actor"] == "autobug dispatch"
    assert "late;" in late["rows"][0]["text"]


def test_the_page_draws_the_late_writes_row_from_the_payload(hub):
    assert re.search(r'id=ilate', hub.PAGE)
    assert re.search(r"function lateRows\(", hub.PAGE)
    assert re.search(r'instSet\("ilate",\s*lateRows\(\(d\|\|\{\}\)\.late\)\)', hub.PAGE)


def test_the_page_computes_no_lag_figure(hub):
    body = re.search(r"function lateRows\(.*?\n\}?", hub.PAGE, re.S)
    text = body.group(0) if body else ""
    assert text and not re.search(r"Math\.|Date\.|new Date|lag\s*[-/*]", text)
