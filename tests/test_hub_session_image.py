"""The chat's thumbnails (#1073): the hub serves one screenshot the transcript names, and nothing else."""
import importlib.util
import os
import pathlib
import tempfile
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal" / "scripts"
SID = "1d9d701d-53d3-46f0-a377-9eae944acb4c"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_session_image", SCRIPTS / "hub.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.loop_caps = lambda: None
    return mod


def q(**kw):
    return urllib.parse.urlencode(kw)


@pytest.fixture
def shot(tmp_path):
    d = tmp_path / SID / "images"
    d.mkdir(parents=True)
    p = d / "123.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nabc")
    return p


def test_a_screenshot_the_transcript_names_is_served(hub, shot):
    body, ct = hub.image_payload(q(sid=SID, p=str(shot)))
    assert ct == "image/png" and body.startswith(b"\x89PNG")


def test_a_path_outside_an_images_directory_is_refused(hub, tmp_path, shot):
    other = tmp_path / SID / "notes.txt"
    other.write_text("secret")
    assert hub.image_payload(q(sid=SID, p=str(other))) is None
    assert hub.image_payload(q(sid=SID, p=str(tmp_path / SID / "images" / "missing.png"))) is None


def test_a_path_of_another_session_is_refused(hub, tmp_path, shot):
    assert hub.image_payload(q(sid="0" * 8 + "-0000-0000-0000-" + "0" * 12, p=str(shot))) is None
    assert hub.image_payload(q(sid="nope", p=str(shot))) is None
