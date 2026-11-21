"""The served hub page draws the lead-time marks small (SPEC 1327, #1432): 3 px for a lone card, 5 px for a cluster,
and a 1.5 px outline, on a 1x display.

The check runs in ego-browser, the repository's browser door, against a real hub on a loopback port and a throwaway
profile: it opens the served page, asks it for the lead chart over a seeded history store, and reads the radius and
the computed stroke width of every mark the page drew. Headless Chrome is never used (ADR-0137's browser rule).
"""
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
HUB_PY = SCRIPTS / "hub.py"
HISTORY_PY = SCRIPTS / "board_history.py"
EGO = shutil.which("ego-browser")
REPO = "x/y"

EGO_SCRIPT = """
const task = await taskSpace(SPACE_NAME);
try {
  const page = task.page("p1");
  await page.cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await page.goto(URL);
  const seen = await page.evaluate(async () => {
    await flowOpen("lead");
    for (let i = 0; i < 50; i++) {
      const s = document.querySelector("#flowb svg[data-lt]");
      if (s && s.querySelectorAll("circle").length >= 2) break;
      await new Promise(r => setTimeout(r, 100));
    }
    const svg = document.querySelector("#flowb svg[data-lt]");
    const circles = svg ? [...svg.querySelectorAll("circle")] : [];
    const payload = await (await fetch("/api/chart/lead?ty=bug")).json();
    const control = payload.lead && payload.lead.control && payload.lead.control["2"];
    return {
      marks: circles.map(c => ({ r: c.r.baseVal.value, stroke: parseFloat(getComputedStyle(c).strokeWidth) })),
      mark: control ? control.mark : null,
      counts: control ? control.clusters.map(k => k.count) : [],
    };
  });
  console.log("EGO_MARKS:" + JSON.stringify(seen));
} finally {
  await task.finish({ keep: [] });
}
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _iso(moment):
    return moment.isoformat()


def _card(n, opened, closed):
    return {"n": n, "i": f"PVT_{n}", "t": f"card {n}", "s": "CLOSED", "c": closed, "st": "Closed", "k": None,
            "l": ["bug"], "si": {"total": None, "completed": None}, "o": opened}


def _seed(base):
    """A throwaway board cache and history store: one lone card and two whose closes merge into one cluster (#1331), all
    closed inside the default 12-hour view (#2029), placed from the clock the hub reads."""
    hub_dir = base / "hub"
    hub_dir.mkdir()
    now = time.time()
    clock = datetime.now(UTC)
    lone_close = clock - timedelta(hours=10)   # inside the 12-hour default (#2029)
    lone_open = lone_close - timedelta(hours=8)
    pair_closes = (clock - timedelta(hours=2), clock - timedelta(hours=1, minutes=58))   # two minutes apart: one cluster on the 12-hour axis
    cards = [(1, lone_open, lone_close)] + [(n, c - timedelta(hours=5), c) for n, c in ((2, pair_closes[0]), (3, pair_closes[1]))]
    items = [_card(n, _iso(o), _iso(c)) for n, o, c in cards]
    board = hub_dir / "hub-board.json"
    # `rv` is the items-read version the hub keeps; a cache that names another drops its rows and reads the board again.
    board.write_text(json.dumps({"t": now, "repo": REPO, "rv": 2, "items": items, "prs": [], "cases": [], "lc": {},
                                 "err": None, "lt": now}))

    spec = importlib.util.spec_from_file_location("board_history", HISTORY_PY)
    hist = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hist)
    path = board.with_name(f"hub-history.{REPO.replace('/', '_')}.jsonl")
    for n, opened, closed in cards:
        opened, closed = _iso(opened), _iso(closed)
        events = [{"t": opened, "type": "added"}, {"t": opened, "type": "status", "status": "Todo"},
                  {"t": closed, "type": "status", "status": "Closed"}, {"t": closed, "type": "closed"}]
        hist.record_issue(path, n, opened, opened, events, True, now)
    return board


def _start_hub(base, board, port):
    """A real hub on a loopback port and its own profile; a stub `gh` keeps the background board pass off the network."""
    stub = base / "stub"
    stub.mkdir()
    gh = stub / "gh"
    gh.write_text("#!/bin/sh\nexit 1\n")
    gh.chmod(0o755)
    env = {**os.environ, "HOME": str(base / "home"), "SG_STATE": str(base / "state"),
           "SG_HUB_DIR": str(base / "hub"), "SG_BOARD_CACHE": str(board), "SG_CHECKOUT": str(base / "checkout"),
           "SG_REPO": REPO, "SG_OWNER": "x", "SG_PROJECT": "4", "TZ": "UTC",
           "PATH": f"{stub}:{os.environ.get('PATH', '')}"}
    for name in ("home", "state", "checkout"):
        (base / name).mkdir()
    hub = subprocess.Popen([sys.executable, str(HUB_PY), str(port)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=2):
                return hub
        except OSError:   # a refused connection while the hub boots; HTTPError is a URLError, itself an OSError
            if hub.poll() is not None:
                break
            time.sleep(0.25)
    hub.terminate()
    raise AssertionError("the hub answered no /api/version on its loopback port")


@pytest.mark.heavy
@pytest.mark.skipif(EGO is None, reason="ego-browser is needed to drive the served page")
def test_the_served_hub_page_draws_the_lead_time_marks_small(tmp_path):
    board = _seed(tmp_path)
    port = _free_port()
    hub = _start_hub(tmp_path, board, port)
    try:
        head = (f"const SPACE_NAME = {json.dumps(f'agent-squad lead dots {uuid.uuid4().hex}')}; "
                f"const URL = {json.dumps(f'http://127.0.0.1:{port}/')};")
        run = subprocess.run([EGO, "nodejs"], input=head + EGO_SCRIPT, capture_output=True, text=True, timeout=120)
        out = run.stdout + run.stderr
        match = [line for line in out.splitlines() if line.startswith("EGO_MARKS:")]
        assert match, f"ego-browser read no marks from the served page: {out[-2000:]}"
        seen = json.loads(match[-1][len("EGO_MARKS:"):])
    finally:
        hub.terminate()
        hub.wait(timeout=10)

    # `mark` is `control.mark`: the page's own route answer for the 48h chart, the one the script renders from.
    assert seen["mark"] == {"dot_radius": 3, "cluster_radius": 5, "outline": 1.5}, \
        f"the served payload does not name the small sizes: {seen['mark']}"
    assert sorted(seen["counts"]) == [1, 2], f"the served page does not hold a lone card and a cluster: {seen['counts']}"
    radii = sorted(mark["r"] for mark in seen["marks"])
    assert radii == [3, 5], f"the served page does not draw a 3 px lone card and a 5 px cluster: {seen['marks']}"
    assert [mark["stroke"] for mark in seen["marks"]] == [seen["mark"]["outline"]] * len(seen["marks"]), \
        f"the served page does not apply the payload's outline {seen['mark']['outline']}: {seen['marks']}"
