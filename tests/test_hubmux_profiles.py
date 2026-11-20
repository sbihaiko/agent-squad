"""The squad hub index reads the profile list on each request (#917).

A profile added while the hub runs shows on the next request; a profile that is gone drops from the index; the index
page polls its JSON every 10 s; the tailnet rule reads a changed allow list on the next request, with no restart.
Driven over HTTP through the mux, as the mobile hub test is (test_hub_mobile.py).
"""
import http.client
import importlib.util
import json
import pathlib
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
MUX_PY = SCRIPTS / "hubmux.py"
TAILNET = "hub.tail1234.ts.net"
ANA = "ana@example.com"


def request(port, path, *, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    body = r.read().decode()
    c.close()
    return r.status, body


def write_profile(prof, slug, co):
    (prof / f"{slug}.sh").write_text(f': "${{SG_REPO:=x/{slug}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={co}}}"\n')


def index_slugs(port):
    status, body = request(port, "/api/hub")
    assert status == 200
    return [r["slug"] for r in json.loads(body)["repos"]]


@pytest.fixture
def mux(tmp_path, monkeypatch):
    prof, base, co = tmp_path / "profiles", tmp_path / "state", tmp_path / "co-alpha"
    (co / "docs" / "squad").mkdir(parents=True)
    (co / "docs" / "squad" / "defaults.yaml").write_text(f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps([ANA])}\n")
    prof.mkdir()
    write_profile(prof, "alpha", co)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_profiles", MUX_PY)
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    for app in apps.values():
        app["mod"].runs = lambda: []
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield {"port": srv.server_address[1], "prof": prof, "co": co}
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.heavy
def test_a_profile_added_after_start_shows_on_the_next_request(mux):
    assert index_slugs(mux["port"]) == ["alpha"]
    write_profile(mux["prof"], "beta", mux["co"])
    assert index_slugs(mux["port"]) == ["alpha", "beta"]


@pytest.mark.heavy
def test_a_profile_that_is_gone_drops_from_the_index(mux):
    write_profile(mux["prof"], "beta", mux["co"])
    assert index_slugs(mux["port"]) == ["alpha", "beta"]
    (mux["prof"] / "alpha.sh").unlink()
    assert index_slugs(mux["port"]) == ["beta"]


@pytest.mark.heavy
def test_the_index_page_polls_its_json_every_ten_seconds(mux):
    status, body = request(mux["port"], "/")
    assert status == 200
    assert 'fetch("/api/hub"' in body
    assert "setInterval" in body and "10000" in body


@pytest.mark.heavy
def test_the_tailnet_rule_reads_a_changed_allow_list_on_the_next_request(mux):
    tn = {"Host": TAILNET, "Tailscale-User-Login": ANA}
    assert request(mux["port"], "/api/hub", headers=tn)[0] == 200
    (mux["co"] / "docs" / "squad" / "defaults.yaml").write_text(
        f"tailnet:\n  host: {TAILNET}\n  allow: {json.dumps(['other@example.com'])}\n")
    status, body = request(mux["port"], "/api/hub", headers=tn)
    assert status == 403 and "login not allowed" in body


# ---------------------------------------------------------------- a hand hold with no squad package (#2533)
# The live hub serves a profile whose checkout holds no `squad` package, and the hub.py beside the live copy sits in a
# tree that holds none either. The Live work answer read the hand holds through `squad.handhold` and dropped every row of
# such a profile, so the hub reads the profile's state directory directly.

def _live_tree(tmp_path):
    """A hub tree shaped as the live copy under the home is: a hub.py and a hubmux.py copy in a tree that holds no `squad`
    package, every other script linked beside them."""
    root = tmp_path / "live"
    scripts = root / "contrib" / "squad-goal" / "scripts"
    scripts.mkdir(parents=True)
    for f in SCRIPTS.iterdir():
        if f.name in ("hub.py", "hubmux.py"):
            continue
        if f.is_dir():
            (scripts / f.name).symlink_to(f, target_is_directory=True)
        else:
            (scripts / f.name).symlink_to(f)
    for name in ("hub.py", "hubmux.py"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    return root, scripts


def _hold_file(base, slug, n, carrier="manual"):
    d = base / slug / "auto"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"hold-{n}").write_text(f"by {carrier}\nsince 2026-11-20T08:00:00Z\nuntil 2030-01-01T00:00:00Z\n")


@pytest.mark.heavy
def test_two_profiles_serve_their_hand_holds_with_no_squad_package(tmp_path, monkeypatch):
    """#2533 (A): two profiles of one hubmux process, each whose checkout holds no `squad` package, and the Live work
    answer of each carries its own live hand hold row. The live hub dropped every hold row of such a profile: the read
    imported `squad.handhold` from roots that hold no package, and the answer named the failure in its log."""
    root, scripts = _live_tree(tmp_path)
    assert not (root / "squad" / "__init__.py").is_file(), "the premise: no squad package"
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug in ("alpha", "beta"):
        co = tmp_path / f"co-{slug}"
        co.mkdir()
        write_profile(prof, slug, co)
    _hold_file(base, "alpha", 4201, carrier="hal9k")
    _hold_file(base, "beta", 4202)
    for k in ("SG_SQUAD", "SG_PROJECTS", "SG_FORK_DIR"):   # the shell's own repository never answers for these profiles
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SG_CHECKOUT", str(tmp_path / "co-alpha"))
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_holds", scripts / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    assert all(a.get("mod") for a in apps.values()), [(s, a.get("err")) for s, a in apps.items()]
    srv = mux.server(0, apps)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        port = srv.server_address[1]
        assert index_slugs(port) == ["alpha", "beta"]
        held = {}
        for slug in ("alpha", "beta"):
            status, body = request(port, f"/{slug}/api/live")
            assert status == 200, (slug, body)
            held[slug] = [r["id"] for r in json.loads(body)["lines"] if r["kind"] == "hold"]
        assert held["alpha"] == ["hold-4201"], held
        assert held["beta"] == ["hold-4202"], held
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------- one scan a tick for every profile (#1746)
# hubmux loads one hub.py per profile into one interpreter, and each profile runs its own sampler thread. The process
# table is the machine's, not a profile's: a scan of it answers every profile's tick, so the whole hub costs one scan a
# tick instead of one per profile. The test loads three profiles through the mux's own loader, never three processes.

@pytest.fixture
def three_profiles(tmp_path, monkeypatch):
    """Three profiles' hubs, loaded as hubmux loads them: three module objects in this one interpreter, sharing no module."""
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    for slug in ("alpha", "beta", "gamma"):
        co = tmp_path / f"co-{slug}"
        (co / "docs" / "squad").mkdir(parents=True)
        write_profile(prof, slug, co)
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.delenv("SG_FORK_DIR", raising=False)
    monkeypatch.delenv("SG_PROJECTS", raising=False)
    spec = importlib.util.spec_from_file_location("hubmux_shared_scan", MUX_PY)
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    apps = mux.load_apps()
    mods = [apps[s].get("mod") for s in ("alpha", "beta", "gamma")]
    assert all(mods), [(s, apps[s].get("err")) for s in apps]
    assert len({id(m) for m in mods}) == 3 and len({id(m.LOAD_STATE) for m in mods}) == 3, "the profiles share one module"
    return mods


def test_three_profiles_scan_the_process_table_once_a_tick(three_profiles, monkeypatch):
    """One tick of the sampler, three profiles of one hub: the process table is read once, not once per profile."""
    monkeypatch.setitem(sys.modules, "psutil", None)   # `import psutil` raises: the hub's own table is the one read
    scans = []

    def scan():
        scans.append(1)
        return {}

    for m in three_profiles:
        monkeypatch.setattr(m, "process_table", scan)
    clock = {"t": time.time()}
    gap = three_profiles[0].LOAD_SAMPLE_SECS
    for m in three_profiles:
        monkeypatch.setattr(m, "time", SimpleNamespace(time=lambda: clock["t"]))
    sample = {"cpu": 95.0, "ram": 50.0, "gpu": None}   # at or above LOAD_WARN_PCT: two of them are the warned machine
    for _ in range(2):                                 # the two-sample warn rule: the second hot tick is the first scan
        for m in three_profiles:
            m.load_tick(sample)
        clock["t"] += gap
    assert scans, "the warned sampler never read the process table"

    scans.clear()
    clock["t"] += gap                                  # the next tick, one sample interval on
    for m in three_profiles:
        m.load_tick(sample)
    assert len(scans) == 1, f"one tick read the process table {len(scans)} times for three profiles"

    scans.clear()                                      # the reading is kept for its tick only
    clock["t"] += gap
    three_profiles[0].load_tick(sample)
    assert len(scans) == 1, "a later tick kept a scan it should have read again"


def test_three_profiles_profile_from_one_scan_a_tick(three_profiles, monkeypatch):
    """The Loop tab's sampler reads the table once a tick for every profile of the hub, not once per profile (#1790):
    three profile samplers at one reading a second each scanned the machine three times a second."""
    scans = []

    def scan():
        scans.append(1)
        return {}

    clock = {"t": time.time()}
    monkeypatch.setitem(sys.modules["procscan"]._STATE, "snap", None)   # no scan a test before this one left behind
    monkeypatch.setitem(sys.modules["procscan"]._STATE, "at", None)
    for m in three_profiles:
        monkeypatch.setattr(m, "process_table", scan)
        monkeypatch.setattr(m, "time", SimpleNamespace(time=lambda: clock["t"]))
    for m in three_profiles:
        m.profiling_table()
    assert len(scans) == 1, f"one profiler tick read the process table {len(scans)} times for three profiles"
    clock["t"] += three_profiles[0].PROFILER_SECS + 0.1
    three_profiles[0].profiling_table()
    assert len(scans) == 2, "a later tick kept a scan it should have read again"


def test_three_profiles_read_the_load_average_once_a_tick(three_profiles, monkeypatch):
    """The load average is the machine's, not a profile's (#1768): the minute record takes the value one tick read for
    the whole hub, so three profiles append their lines from one read, never three, and no process table is scanned."""
    reads = []

    def gauge():
        reads.append(1)
        return 1.5

    ps = three_profiles[0].procscan
    assert all(m.procscan is ps for m in three_profiles), "the profiles share one scan store"
    monkeypatch.setattr(ps, "_CELLS", {})   # a private store: no read a test before this one kept
    now = 5_000_000.0                       # a private monotonic base, never the wall clock
    for m in three_profiles:
        monkeypatch.setattr(m, "_loadavg", gauge)
    for m in three_profiles:
        assert m.load_average(now) == 1.5
    assert len(reads) == 1, f"one tick read the load average {len(reads)} times for three profiles"
    assert ps._CELLS is three_profiles[0].procscan._CELLS, "the three profiles read through one cache object"
