"""The phone page rendered at 390 x 844 (SPEC 1596, #1605): the run detail sits inside the viewport, and the controls
the thumb must hit sit inside the thumb zone — the bottom 40 % of the box the thumb reaches.

The chat, its quick answers, its recording row and its attach sheet left the page with the chat (#2490), so the frames
here are the run detail: its box, its named Back at the top left and the two Backs of a detail. The page is rendered by
ego-browser, the repository's browser door, on one private task space per call and at a phone's own metrics; headless
Chrome is never used and the owner's profile is never opened (the browser rule of ADR-0137). Each frame is drawn the way
the page draws it, captured as a screenshot and read as boxes in CSS pixels, so a frame that overflows the viewport or
falls out of the thumb's reach fails here.

When ego-browser is not installed the test skips and names that reason; it never reports a frame it did not see.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
EGO = shutil.which("ego-browser")
WIDTH, HEIGHT = 390, 844            # the phone frame the spec names
THUMB = 0.6                         # the thumb zone is the bottom 40 % of the box (hub-mobile-gesture.js)
TAP = 44

BOX = """
  const box = el => { const r = el.getBoundingClientRect();
                      return { x: r.left, y: r.top, w: r.width, h: r.height, right: r.right, bottom: r.bottom }; };
"""

# The run detail: its box, its named Back at the top left and its action row's Back at the thumb.
DRAW_RUN = """
() => {
  /*BOX*/
  const run = document.getElementById("runview");
  run.hidden = false;
  document.getElementById("runview-name").textContent = "Run 1";
  const topBack = document.querySelector('[data-topback="run"]');
  topBack.hidden = false;
  topBack.querySelector(".ico-lbl").textContent = "Runs";
  return { box: box(run), top: box(topBack), back: box(document.getElementById("runview-back")) };
}
""".replace("/*BOX*/", BOX)

EGO_SCRIPT = """
import fs from "node:fs";
const task = await taskSpace(SPACE_NAME);
try {
  const page = task.page("p1");
  await page.cdp("Emulation.setDeviceMetricsOverride",
                 { width: WIDTH, height: HEIGHT, deviceScaleFactor: 2, mobile: true });
  await page.goto(URL);
  const size = p => { try { return fs.statSync(p).size; } catch (e) { return 0; } };
  const run = await page.evaluate(DRAW_RUN);
  const runShot = size(await page.screenshot({ path: SHOT_DIR + "/run.png" }));
  const vp = await page.evaluate(() => ({ vw: window.innerWidth, vh: window.innerHeight }));
  console.log("EGO_FRAMES:" + JSON.stringify({
    vw: vp.vw, vh: vp.vh,
    frames: { run: { ...run, shot: runShot } },
  }));
} finally {
  await task.finish({ keep: [] });
}
"""


@pytest.fixture(scope="module")
def phone(tmp_path_factory):
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_visual_floor", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = tmp_path_factory.mktemp("phone")
    (base / "shots").mkdir()
    page = base / "hub-mobile.html"
    page.write_text(mod.MOBILE_PAGE)
    return page, base / "shots"


def _frames(page_path, shot_dir):
    head = (f"const SPACE_NAME = {json.dumps(f'agent-squad phone floor {uuid.uuid4().hex}')};\n"
            f"const URL = {json.dumps(page_path.as_uri())};\n"
            f"const SHOT_DIR = {json.dumps(str(shot_dir))};\n"
            f"const WIDTH = {WIDTH}; const HEIGHT = {HEIGHT};\n"
            f"const DRAW_RUN = {DRAW_RUN};\n")
    run = subprocess.run([EGO, "nodejs"], input=head + EGO_SCRIPT, capture_output=True, text=True, timeout=180)
    out = run.stdout + run.stderr
    seen = [line for line in out.splitlines() if line.startswith("EGO_FRAMES:")]
    assert seen, f"ego-browser rendered no frame: {out[-2000:]}"
    return json.loads(seen[-1][len("EGO_FRAMES:"):])


def _inside(rect, box, name):
    assert rect["x"] >= box["x"] - 0.5 and rect["right"] <= box["right"] + 0.5 and rect["y"] >= box["y"] - 0.5 \
        and rect["bottom"] <= box["bottom"] + 0.5, f"{name} leaves its box: {rect} not in {box}"
    assert rect["w"] > 0 and rect["h"] > 0, f"{name} draws nothing: {rect}"


@pytest.mark.heavy
@pytest.mark.skipif(EGO is None, reason="ego-browser is needed to render the phone page")
def test_the_run_detail_sits_inside_the_viewport_and_the_thumb_zone(phone):
    page_path, shot_dir = phone
    seen = _frames(page_path, shot_dir)
    vw, vh = seen["vw"], seen["vh"]
    assert (vw, vh) == (WIDTH, HEIGHT), f"the phone page rendered at {vw} x {vh}, not {WIDTH} x {HEIGHT}"
    viewport = {"x": 0, "y": 0, "w": vw, "h": vh, "right": vw, "bottom": vh}
    run = seen["frames"]["run"]

    # The run detail fills the phone, and its two Backs stand where the thumb reaches: the named one at the top left,
    # the action row's at the bottom.
    _inside(run["box"], viewport, "the run detail")
    _inside(run["top"], run["box"], "the run's top Back")
    assert run["top"]["bottom"] <= run["box"]["h"] * THUMB, f"the run's top Back left the top of the screen: {run['top']}"
    assert run["top"]["h"] >= TAP, f"the run's top Back is {run['top']['h']} px tall"
    _inside(run["back"], run["box"], "the run's action-row Back")
    assert run["back"]["y"] >= run["box"]["h"] * THUMB, f"the run's action-row Back sits above the thumb zone: {run['back']}"
    assert run["back"]["h"] >= TAP, f"the run's action-row Back is {run['back']['h']} px tall"

    # The captured frame was really rendered: the screenshot on disk carries bytes.
    assert run["shot"] > 1000, "the run frame rendered no screenshot"
