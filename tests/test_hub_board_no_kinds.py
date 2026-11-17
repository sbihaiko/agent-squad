"""The kanban carries no Kinds view (owner, 2026-10-16).

The Kinds button grouped the items by kind and status and carried two filters, one kind and one outcome. The owner took
it out: the board draws its columns and the two controls that stay, labels and Specs, and nothing else. A work branch has
no lane and no other view of the board, so `board()` takes no filter argument and serves no kind group.

Fixture style follows tests/test_hub_board_cases.py.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
GONE = ("id=bk ", "id=bkf", "id=bof", "bo.kinds", "bo.kind", "bo.outcome", "kindGroups", "gapNote", "filterOpts")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "sbihaiko/MesenAI")
    os.environ.setdefault("SG_OWNER", "sbihaiko")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_no_kinds", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_board_header_carries_neither_specs_nor_kinds_control(hub):
    """The Specs and Kinds controls left the header at the owner's request (SPEC 1965); the Kinds control was already gone."""
    head = hub.PAGE[hub.PAGE.index("<div id=board>"):hub.PAGE.index("<div id=bcases>")]
    assert '<details id=fs' not in head and '<details id=fk' not in head, "the header still carries the filter bar"
    assert ">Kinds<" not in head and "id=bk " not in head, "the header still carries the Kinds control"
    assert "<select id=lmode" not in head, "the header still carries the lane-mode control"


def test_the_page_holds_no_trace_of_the_kinds_view(hub):
    for gone in GONE:
        assert gone not in hub.PAGE, f"the page still holds {gone}"


def test_the_board_takes_no_filter_and_serves_no_kind_group(hub, monkeypatch):
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": None, "errs": []})
    served = hub.board()
    for gone in ("kinds", "gaps", "filters"):
        assert gone not in served, f"the board still serves {gone}"
    with pytest.raises(TypeError):
        hub.board(kind="bug")   # the filter went with the view: a reader cannot ask for one
