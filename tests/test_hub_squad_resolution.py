"""A resolution answers only with a fully imported squad module, and an unreadable holder fact ends no board answer (#2358).

hubmux.py serves every profile from one interpreter, and each profile's resolution purges and re-imports `squad`. A thread
that asks during that window can receive a module whose body has not run, so a name the body defines is missing.
"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import threading
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


def _squad_keys():
    return [k for k in sys.modules if k == "squad" or k.startswith("squad.")]


@pytest.fixture(autouse=True)
def restore_squad_modules():
    before = {k: sys.modules[k] for k in _squad_keys()}
    yield
    for k in _squad_keys():
        del sys.modules[k]
    sys.modules.update(before)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(ROOT))
    monkeypatch.setenv("SG_HUB_DIR", str(tmp_path / "hub"))
    spec = importlib.util.spec_from_file_location("hub_squad_resolution", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bare_stub(name):
    """A module the package binds before its body runs: its file is set and no import spec records a finished import."""
    stub = types.ModuleType(name)
    stub.__file__ = str(ROOT / "squad" / (name.split(".")[-1] + ".py"))
    return stub


@pytest.fixture
def partial_reap(monkeypatch):
    pkg = types.ModuleType("squad")
    pkg.__path__ = [str(ROOT / "squad")]
    stub = _bare_stub("squad.reap")
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


def test_a_nested_name_is_imported_only_when_its_import_finished(hub, monkeypatch):
    pkg = types.ModuleType("squad")
    sub = types.ModuleType("squad.sub")
    child = _bare_stub("squad.sub.x")
    sub.x = child
    pkg.sub = sub
    monkeypatch.setitem(sys.modules, "squad", pkg)
    assert not hub._imported(("sub.x",), (child,))
    child.__spec__ = importlib.machinery.ModuleSpec("squad.sub.x", None)
    child.__spec__._initializing = True
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
    reap_src = tmp_path / "reap.py"
    reap_src.write_text("")
    monkeypatch.setattr(hub, "reaper", lambda: types.SimpleNamespace(NO_PID_IDLE_S=300, __file__=str(reap_src)))
    item = {"n": 2358, "h": {"run": run, "started": "2026-11-19T00:00:00Z"}}

    facts = [hub._holder_fact_now(item, run, time.time()) for _ in range(2)]

    assert facts == [(None, None), (None, None)]
    err = capsys.readouterr().err
    assert err.count(run) == 1
    assert f"squad.reap {reap_src} mtime 20" in err   # the line names the module that answered and when it was written
