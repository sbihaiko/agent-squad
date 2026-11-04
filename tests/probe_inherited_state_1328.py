"""Child-process probe for tests/test_suite_state_isolation.py (#1328). Not collected: the name has no `test_` prefix.

The import-time read below is what a module-scoped fixture does before any function-scoped fixture runs: it must not reach
the inherited SG_STATE the parent passes in (PROBE_SENTINEL), and the test body must not see it either.
"""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts"
_SPEC = importlib.util.spec_from_file_location("issue_state_probe", SCRIPTS / "issue_state.py")
issue_state = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(issue_state)


def _stub(cmd, input=None, **kw):
    return subprocess.CompletedProcess(cmd, 0, json.dumps({"number": 10, "labels": []}), "")


def _milestones():
    return issue_state.board_state("o", "4", "x/y", kind="users", run=_stub).binding.api("repos/x/y/milestones")


IMPORT_READ = _milestones()   # at import, before any test


def test_the_test_body_read_is_not_the_inherited_state():
    assert _milestones()
    assert os.environ.get("SG_STATE") != os.environ.get("PROBE_SENTINEL"), "SG_STATE inside a test is the inherited dir"
