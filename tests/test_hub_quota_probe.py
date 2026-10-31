"""A quota probe is not a login: the hub's `agy -p /quota` refresh must never open a browser.

A hub started from a checkout whose HOME carries no credential made agy fall back to its interactive OAuth flow, and
every dashboard refresh opened another Google account page. The probe now runs with a PATH whose `open` does nothing,
and a CLI that answers `not authenticated` reads as no quota at all.
"""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_quota_probe", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_probe_path_names_a_no_op_open(hub):
    d = hub.no_browser_dir()
    opener = Path(d) / "open"
    assert opener.exists()
    assert opener.stat().st_mode & stat.S_IXUSR
    out = subprocess.run(["sh", "-c", f"PATH={d}:$PATH command -v open"], capture_output=True, text=True)
    assert out.stdout.strip() == str(opener)


def test_the_no_op_open_opens_nothing(hub):
    d = hub.no_browser_dir()
    marker = Path(tempfile.mkdtemp()) / "opened"
    r = subprocess.run(["sh", "-c", f"PATH={d}:$PATH open https://accounts.google.com && touch {marker}"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and marker.exists()   # the stub answered, and it printed no page


def test_an_unauthenticated_agy_reads_as_no_quota(hub, monkeypatch):
    """The CLI's own words for the case that opened the pages: the probe answers nothing, and it raises no window."""
    d = Path(tempfile.mkdtemp())
    agy = d / "agy"
    agy.write_text('#!/bin/sh\necho "Print mode: not authenticated, trying silent auth"\n'
                   'echo "Print mode: silent auth failed" >&2\nexit 1\n')
    agy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{d}:{os.environ['PATH']}")
    assert hub.q_agy() is None
