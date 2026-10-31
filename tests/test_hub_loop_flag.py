"""The loop state ends the hub's line (#554): `Loop:ON` while the supervisor's pid file names a live process, `Loop:OFF`
when the file is absent or its pid is dead. The hub reads the file and signals the pid; it runs no `ps`."""
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = 1_000_000.0


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_CHECKOUT"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_loop_flag", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _items(hub, loop):
    return hub.foot_items({"quota": {"codex": None, "agy": None}, "loop": loop}, NOW)


def test_a_live_supervisor_reads_loop_on_as_the_last_fact(hub):
    items = _items(hub, True)
    assert items[-1]["key"] == "loop" and items[-1]["text"] == "Loop:ON" and not items[-1]["bad"]


def test_a_stopped_supervisor_reads_loop_off_in_the_error_tone(hub):
    items = _items(hub, False)
    assert items[-1]["text"] == "Loop:OFF" and items[-1]["bad"]


def test_the_pid_file_decides(hub, tmp_path):
    pid = tmp_path / "watchd.pid"
    assert hub._loop_on(pid) is False   # no file
    pid.write_text(f"{os.getpid()}\n")
    assert hub._loop_on(pid) is True   # a live pid
    pid.write_text("999999999\n")
    assert hub._loop_on(pid) is False   # a dead pid
    pid.write_text("not a pid\n")
    assert hub._loop_on(pid) is False


def test_an_auto_off_flag_reads_loop_off_even_with_a_live_supervisor(hub, tmp_path, monkeypatch):
    """#788: `auto/off` pauses dispatch, so the footer reads Loop:OFF while the supervisor pid is still live."""
    monkeypatch.setattr(hub, "STATE", tmp_path)
    (tmp_path / "watchd.pid").write_text(f"{os.getpid()}\n")
    assert hub._loop_on(tmp_path / "watchd.pid") is True   # live pid, no flag
    (tmp_path / "auto").mkdir()
    (tmp_path / "auto" / "off").touch()
    assert hub._loop_on(tmp_path / "watchd.pid") is False   # the flag pauses the loop
    assert hub.foot_items({"quota": {"codex": None, "agy": None}, "loop": hub._loop_on(tmp_path / "watchd.pid")}, NOW)[-1]["text"] == "Loop:OFF"


def test_a_loop_stop_marker_reads_loop_off_even_with_a_live_supervisor(hub, tmp_path, monkeypatch):
    """#956: an open `loop-bug` holds dispatch, so the footer reads Loop:OFF instead of a green Loop:ON beside a stopped frontier."""
    monkeypatch.setattr(hub, "STATE", tmp_path)
    (tmp_path / "watchd.pid").write_text(f"{os.getpid()}\n")
    (tmp_path / "auto").mkdir()
    assert hub._loop_on(tmp_path / "watchd.pid") is True
    (tmp_path / "auto" / "loop-stop-956").touch()
    assert hub._loop_on(tmp_path / "watchd.pid") is False
    assert hub.foot_items({"quota": {"codex": None, "agy": None}, "loop": hub._loop_on(tmp_path / "watchd.pid")}, NOW)[-1]["text"] == "Loop:OFF"
    (tmp_path / "auto" / "loop-stop-956").unlink()
    assert hub._loop_on(tmp_path / "watchd.pid") is True
