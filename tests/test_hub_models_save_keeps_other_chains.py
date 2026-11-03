"""A Save in the hub's models panel rewrites the four chains the panel draws and keeps every other chain the file holds.

The panel draws coding, review, vision and router. The file also holds the editorial chain, and a Save dropped it:
the save wrote only the chains it was posted (2026-11-03).
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

HUB_PY = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts" / "hub.py"

DOC = {
    "schema_version": 2,
    "chains": {
        "coding": [{"id": "gpt-6-luna", "route": "gateway"}],
        "review": [{"id": "claude-opus-5-5", "raw": "claude:claude-opus-5-5", "route": "cli"}],
        "vision": [{"id": "gpt-5.6-luna", "route": "cli"}],
        "router": [{"id": "gpt-6-luna", "route": "gateway"}],
        "editorial": [{"id": "claude-haiku-5-5", "route": "gateway"}],
    },
    "review_timeout_seconds": 360,
}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_keep_chains", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = tmp_path / "models.json"
    path.write_text(json.dumps(DOC, indent=2) + "\n")
    monkeypatch.setattr(mod, "MODELS", path)
    return mod, path


def test_a_save_keeps_the_editorial_chain_the_panel_does_not_draw(hub):
    mod, path = hub
    body = mod.models_get()
    body["coding_models"] = ["gpt-6-luna", "claude-haiku-5-5"]
    out = mod._models_set(body)
    assert "err" not in out, out
    chains = json.loads(path.read_text())["chains"]
    assert [e["id"] for e in chains["coding"]] == ["gpt-6-luna", "claude-haiku-5-5"]
    assert chains["editorial"] == DOC["chains"]["editorial"]
