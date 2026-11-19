"""A resolution answers only with a fully imported squad module, and an unreadable holder fact ends no board answer (#2358).

hubmux.py serves every profile from one interpreter, and each profile's resolution purges and re-imports `squad`. A thread
that asks during that window can receive a module whose body has not run, so a name the body defines is missing.
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import tempfile
import threading
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(ROOT))
    monkeypatch.setenv("SG_HUB_DIR", tempfile.mkdtemp())
    spec = importlib.util.spec_from_file_location("hub_squad_resolution", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _half_built(name):
    """A module whose import is in progress: its file is set and its body has not run."""
    stub = types.ModuleType(name)
    stub.__file__ = str(ROOT / "squad" / (name.split(".")[-1] + ".py"))
    stub.__spec__ = importlib.machinery.ModuleSpec(name, None)
    stub.__spec__._initializing = True
    return stub


@pytest.fixture
def partial_reap(monkeypatch):
    """The package binds `reap` before its body runs, the state a second thread sees during another profile's import."""
    pkg = types.ModuleType("squad")
    pkg.__path__ = [str(ROOT / "squad")]
    stub = _half_built("squad.reap")
    pkg.reap = stub
    monkeypatch.setitem(sys.modules, "squad", pkg)
    monkeypatch.setitem(sys.modules, "squad.reap", stub)
    return stub


def test_a_resolution_never_returns_a_module_still_importing(hub, partial_reap):
    (reap,) = hub.load_squad([str(ROOT)], ("reap",))
    assert reap is not partial_reap
    assert hasattr(reap, "run_live")


def test_two_threads_resolving_squad_at_once_each_get_a_fully_imported_module(hub, partial_reap):
    answers, failures = [], []
    start = threading.Barrier(2)

    def resolve():
        start.wait()
        try:
            (reap,) = hub.load_squad([str(ROOT)], ("reap",))
            answers.append(reap is not partial_reap and hasattr(reap, "run_live"))
        except Exception as e:  # noqa: BLE001 - the point is that every answer carries the name
            failures.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=resolve) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not failures, failures
    assert answers == [True, True]


def test_a_nested_name_is_imported_only_when_its_own_body_completed(hub, monkeypatch):
    pkg = types.ModuleType("squad")
    sub = types.ModuleType("squad.sub")
    child = _half_built("squad.sub.x")
    sub.x = child
    pkg.sub = sub
    monkeypatch.setitem(sys.modules, "squad", pkg)
    assert not hub._imported(("sub.x",), (child,))
    child.__spec__._initializing = False
    assert hub._imported(("sub.x",), (child,))


def test_an_unreadable_holder_fact_yields_no_holder_and_one_named_failure(hub, monkeypatch, tmp_path, capsys):
    run = "run-2358-unreadable-fact"
    folder = tmp_path / run
    folder.mkdir()
    (folder / "record.jsonl").write_text("{}\n")
    monkeypatch.setattr(hub, "run_folder", lambda name: folder)
    monkeypatch.setattr(hub, "active_run_names", lambda: {run})
    monkeypatch.setattr(hub, "reaper", lambda: types.SimpleNamespace(NO_PID_IDLE_S=300))
    item = {"n": 2358, "h": {"run": run, "started": "2026-11-19T00:00:00Z"}}

    facts = [hub._holder_fact_now(item, run, time.time()) for _ in range(2)]

    assert facts == [(None, None), (None, None)]
    assert capsys.readouterr().err.count(run) == 1
