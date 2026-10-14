"""The hub's board seam binds the profile the hub was loaded for, never the environment at the first pass.

hubmux.py loads one hub per profile and pins each profile's SG_* variables only while it imports that hub. The
board seam is built lazily, in the first pass, after hubmux restored the environment, so a seam that read
SG_OWNER and SG_PROJECT then bound another profile's board (or none) and every pass failed with HTTP 404.
"""
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
PINNED = {"SG_REPO": "own/hubrepo", "SG_OWNER": "own", "SG_PROJECT": "4"}


def _load():
    before = {k: os.environ.get(k) for k in [*PINNED, "SG_HUB_DIR", "SG_STATE"]}
    os.environ.update(PINNED)
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    try:
        spec = importlib.util.spec_from_file_location("hub_seam_binds_its_profile", HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:   # what hubmux does after the import: the pinned profile is gone from the environment
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return mod


def test_the_seam_binds_the_profile_pinned_at_import(monkeypatch):
    hub = _load()
    monkeypatch.setenv("SG_OWNER", "other")
    monkeypatch.setenv("SG_PROJECT", "2")
    monkeypatch.setenv("SG_REPO", "other/repo")
    b = hub.board_seam().binding
    assert b.base.endswith("/own/projectsV2/4") or "/own/projectsV2/4" in b.base, \
        f"the seam bound the environment of the first pass, not the hub's own profile: {b.base}"
