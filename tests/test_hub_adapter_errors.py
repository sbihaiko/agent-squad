"""GitHub issue #698: the hub shows the adapter_error classes with their counts, and only reads.

The classifier is one pure module (`squad/adapter_classes.py`); the hub reads each run folder's record and
renders what it returns. The panel is a view: a GET route with no write route and no control.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
SRC = HUB_PY.read_text()
PAGE = SRC[SRC.index('PAGE = r"""'): SRC.index("PAGE = PAGE.replace")]

SESSION_LIMIT = ("ResultError: Claude Code returned an error result: You've hit your session limit · resets "
                 "7:27am (America/Sao_Paulo) (exit code: 1)")


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_adapter_errors", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _outcome(model, error):
    return {"event": "outcome", "node": "router", "entry": 1, "outcome": "fail", "reasons": ["adapter_error"],
            "artifact": None, "spend": 0.0, "turns": 0, "cap": 1.0, "elapsed": 1.0, "pos": 2,
            "model": model, "diagnostic": {"error": error}}


def _verdict():
    return {"event": "verdict", "verdict": "failed", "exit_code": 1, "reasons": ["adapter_error"], "remains": [],
            "folded": [2], "pos": 3}


def _run(root, name, events):
    d = root / name
    d.mkdir(parents=True)
    (d / "record.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return d


def test_the_hub_reports_the_classes_with_their_counts(hub, tmp_path, monkeypatch):
    _run(tmp_path, "run-a", [_outcome("claude-gpt-5.6-terra", SESSION_LIMIT), _verdict()])
    _run(tmp_path, "run-b", [_outcome("claude-gpt-5.6-terra", SESSION_LIMIT), _verdict()])
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path])
    d = hub.adapter_errors()
    assert d["runs"] == 2
    assert d["classes"] == [{"model": "gpt-5.6-terra", "backend": "gateway", "cause": "quota", "count": 2}]


def test_a_run_folder_that_cannot_be_classified_is_reported_and_never_raises(hub, tmp_path, monkeypatch):
    _run(tmp_path, "run-a", [_outcome("claude-gpt-5.6-terra", SESSION_LIMIT), _verdict()])
    monkeypatch.setattr(hub, "project_dirs", lambda: [tmp_path])

    def boom(*a, **kw):
        raise ModuleNotFoundError("no squad package")

    monkeypatch.setattr(hub, "load_squad", boom)
    d = hub.adapter_errors()
    assert d["classes"] == [] and d["err"]


def test_the_route_is_a_read_and_never_a_write():
    assert '"/api/adapter-errors"' in SRC[SRC.index("def do_GET"): SRC.index("def do_POST")]
    post = SRC[SRC.index("def do_POST"): SRC.index("SELF = pathlib.Path")]
    assert "/api/adapter-errors" not in post


def test_the_page_carries_no_adapter_errors_panel_and_never_reads_the_route():
    """The owner hid the panel from the accordion: the page holds no adapter-errors section and polls no route. The
    route stays a read for the tools that ask it, and the page makes no call of its own."""
    assert "<details id=ae class=sp>" not in PAGE
    assert 'fetch("/api/adapter-errors")' not in SRC
