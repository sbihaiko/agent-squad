"""GitHub issue #3011: the owner edits a persona in the Personas theme (SPEC 3009).

The theme posts the persona id, the changed fields and the target projects. The hub builds each project's document
through the persona rules module and calls that project's existing save path from the project's own worktree: a preview
writes no file, and a confirmed save writes, commits and lands each target project.

The seams under test are the hub's own read and edit, `personas_overview` and `personas_edit`, the rules module
`squad/personas.py`, and the served page's renderer run in node. No browser runs here. Every id and name is a
neutral placeholder.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile

import pytest
import yaml

from squad import personas as PS

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"

# A neutral project that declares two personas.
PROJECT = {
    "version": 1,
    "project": {"language": "en-US", "persona": "blue"},
    "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm."},
                 {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}],
    "roles": [{"id": "manager", "actions": ["status"]}],
}
LOCAL = {"version": 1}


@pytest.fixture(scope="module")
def hub():
    """The hub module under a pinned environment, so the policy module resolves from this tree."""
    mp = pytest.MonkeyPatch()
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4"), ("SG_SQUAD", str(ROOT))):
        mp.setenv(k, v)
    for k in ("SG_HUB_DIR", "SG_STATE", "SG_CHECKOUT", "SG_SHARED", "SG_PROFILES_DIR", "SG_SLACK_WORKTREE"):
        mp.setenv(k, tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_personas_edit", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mp.undo()


def seat(pid, committed=None, local=None, checkout="/co/alpha", cf="slack.yaml", lf="slack.local.yaml",
         read_defects=()):
    """One roster seat as the roster hands it in: its project id, its documents and their files, and its worktree."""
    return {"id": pid, "label": pid, "checkout": checkout, "committed": committed or {}, "local": local or {},
            "cf": cf, "lf": lf, "committed_path": f"/co/{pid}/docs/squad/slack.yaml",
            "local_path": f"/profiles/{pid}.slack.local.yaml", "read_defects": list(read_defects)}


# ---------------------------------------------------------------- the rules module: one persona's fields

def test_the_rules_set_only_the_named_fields_of_one_persona():
    """The changed fields alone move; the persona's other keys, the other personas and every other key keep their form."""
    doc = {"version": 1, "personas": [{"id": "blue", "name": "Blue", "voice": "Kore", "tone": "Calm.", "note": "x"},
                                      {"id": "amber", "name": "Amber", "voice": "Sulafat", "tone": "Warm."}]}
    got = PS.with_fields(doc, "blue", {"voice": "Puck", "tone": "  Direct.  "})
    assert got["personas"][0] == {"id": "blue", "name": "Blue", "voice": "Puck", "tone": "Direct.", "note": "x"}, got
    assert got["personas"][1] == doc["personas"][1], got
    assert doc["personas"][0]["voice"] == "Kore", "the input document changed"


def test_a_persona_the_committed_file_does_not_declare_answers_no_document():
    """A local-only persona is read-only in the theme: the rules answer no document for it."""
    assert PS.with_fields({"personas": [{"id": "amber", "name": "Amber"}]}, "blue", {"voice": "Puck"}) is None


def test_the_edit_names_each_refused_project_and_still_carries_the_other_writes():
    """A target outside the roster, a project with no worktree, a file whose read answered a defect and a project whose
    committed file does not declare the id are each refused by name; the other targets keep their write."""
    seats = [seat("alpha", PROJECT),
             seat("beta", PROJECT, checkout=""),
             seat("gamma", PROJECT, read_defects=["slack.yaml: not YAML"]),
             seat("delta", {"personas": [{"id": "amber", "name": "Amber"}]})]
    plan = PS.edit(seats, "blue", {"tone": "Direct."}, ["alpha", "beta", "gamma", "delta", "ghost"])
    rows = {r["project"]: r["result"] for r in plan["rows"]}
    assert rows["alpha"] == "writes Blue", rows
    assert rows["beta"] == "refused: the project has no worktree", rows
    assert rows["gamma"] == "refused: the file does not read: slack.yaml: not YAML", rows
    assert rows["delta"] == "refused: Blue is not declared in this project's committed file", rows
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert [w["project"] for w in plan["writes"]] == ["alpha"], plan["writes"]
    assert plan["writes"][0]["committed"]["personas"][0]["tone"] == "Direct.", plan["writes"]
