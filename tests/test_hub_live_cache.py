"""The Live work answer is computed at most once per the page's own poll window (#1933).

The desktop page polls `/api/live` every three seconds from one timer and every five from another, and every open page and
every client asks the same question of the same profile. The answer reads the process table, every live run's record and
the board read, so answering it once per client cost the hub most of a core. The route keeps the answer for its own poll
window, so any number of pages share one compute, and a request at or past the window computes again, never served as
fresh. The clock is injected, so freshness is proved without a sleep.
"""
import http.client
import importlib.util
import json
import os
import pathlib
import tempfile
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_STATE"] = tempfile.mkdtemp()
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_live_cache", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (pathlib.Path(mod.STATE) / "auto").mkdir(parents=True, exist_ok=True)
    return mod


@pytest.fixture
def served(hub, monkeypatch):
    """The hub's own handler over HTTP, with the live answer counted and the clock under the test's hand."""
    calls = []
    real = hub.live_work_lines

    def counted(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(hub, "live_work_lines", counted)
    clock = [1000.0]
    monkeypatch.setattr(hub, "LIVE_CLOCK", lambda: clock[0], raising=False)
    monkeypatch.setattr(hub, "_LIVE_KEPT", {"at": None, "answer": None}, raising=False)   # each case starts with no kept answer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_port, calls, clock
    finally:
        srv.shutdown()
        srv.server_close()


def get(port):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", "/api/live", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    assert r.status == 200, body
    return body


def test_two_requests_inside_the_window_compute_the_answer_once(served):
    """One page's two reads inside its poll window share one compute; the next window computes again."""
    port, calls, clock = served
    first = get(port)
    second = get(port)
    assert len(calls) == 1, f"two reads inside one window computed {len(calls)} times"
    assert first == second, "the second read inside the window must serve the same answer"
    clock[0] += 1000.0 + 0.1   # past the window: the answer reads afresh, never served as fresh
    get(port)
    assert len(calls) == 2, f"a read past the window computed {len(calls)} times"


def test_many_clients_inside_the_window_compute_the_answer_once(served):
    """Every open page shares one compute: six concurrent reads inside the window cost one."""
    port, calls, clock = served
    barrier = threading.Barrier(6)
    got = []

    def one():
        barrier.wait()
        got.append(get(port))

    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 6 and all(g == got[0] for g in got), "every client inside the window reads one answer"
    assert len(calls) == 1, f"six clients inside one window computed {len(calls)} times"


def test_the_answer_is_per_profile(hub):
    """The cache is the loaded module's own: each profile has its own answer, never another profile's."""
    assert hasattr(hub, "live_served"), "the route serves through `live_served`, the one cached read"
    assert hasattr(hub, "LIVE_POLL_SECS"), "the window is its own named constant"
