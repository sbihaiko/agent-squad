"""The phone page rendered at 390 x 844 (SPEC 1596, #1605): every frame the spec names sits inside the viewport, and the
controls the thumb must hit sit inside the thumb zone — the bottom 40 % of the box the thumb reaches.

The frames are the quick-answer chips above the keyboard, the recording hints, the not-sent mark, the attach sheet and
the two Backs. The page is rendered by ego-browser, the repository's browser door, on one private task space per call
and at a phone's own metrics; headless Chrome is never used and the owner's profile is never opened (the browser rule of
ADR-0137). Each frame is drawn the way the page draws it, captured as a screenshot and read as boxes in CSS pixels, so a
frame that overflows the viewport or falls out of the thumb's reach fails here.

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
KEYBOARD = 336                      # the keyboard the chips and the hints must stay above
THUMB = 0.6                         # the thumb zone is the bottom 40 % of the box (hub-mobile-gesture.js)
TAP = 44

BOX = """
  const box = el => { const r = el.getBoundingClientRect();
                      return { x: r.left, y: r.top, w: r.width, h: r.height, right: r.right, bottom: r.bottom }; };
"""

# The chat, with the keyboard up and the quick answers drawn: the chips ride above the composer and above the keyboard.
DRAW_CHIPS = """
(args) => {
  const [KEYBOARD] = args;
  /*BOX*/
  const term = document.getElementById("termview");
  term.hidden = false;
  term.style.top = "0px";
  term.style.height = (window.innerHeight - KEYBOARD) + "px";   // what fitKeyboard does while the keyboard is up
  document.getElementById("termview-rec").hidden = true;
  const chips = document.getElementById("termview-chips");
  chips.replaceChildren();
  for (const word of ["Yes", "Not now"]) {
    const b = document.createElement("button"); b.type = "button"; b.className = "qchip"; b.textContent = word;
    chips.append(b);
  }
  chips.hidden = false;
  return { vw: window.innerWidth, vh: window.innerHeight, box: box(term), chips: [...chips.querySelectorAll(".qchip")].map(box),
           compose: box(document.getElementById("termview-compose")) };
}
""".replace("/*BOX*/", BOX)

# The recording row the mic draws while it is held: the timer, the waveform, the cancel zone and the three hints.
DRAW_RECORDING = """
(args) => {
  const [KEYBOARD] = args;
  /*BOX*/
  const term = document.getElementById("termview");
  term.hidden = false;
  term.style.top = "0px";
  term.style.height = (window.innerHeight - KEYBOARD) + "px";   // the keyboard is up while the mic is held
  const rec = document.getElementById("termview-rec");
  rec.hidden = false;
  return { box: box(document.getElementById("termview")), rec: box(rec),
           hints: box(document.getElementById("voice-hints")),
           hintsCount: document.querySelectorAll("#voice-hints .voicehint").length };
}
""".replace("/*BOX*/", BOX)

# The chat at rest, with one send the hub never answered: it stays in the thread under its mark.
DRAW_NOT_SENT = """
() => {
  /*BOX*/
  const term = document.getElementById("termview");
  term.style.height = "";   // the keyboard is down: the chat fills the phone again
  document.getElementById("termview-rec").hidden = true;
  document.getElementById("termview-chips").hidden = true;
  const turns = document.getElementById("termview-turns");
  turns.replaceChildren();
  const failed = document.createElement("div");
  failed.className = "turn failed"; failed.tabIndex = 0;
  const said = document.createElement("div"); said.textContent = "What is the loop doing?";
  const mark = document.createElement("span"); mark.className = "notsent";
  mark.textContent = "Not sent: the hub refused the request.";
  failed.append(said, mark); turns.append(failed);
  const topBack = document.querySelector('[data-topback="term"]');   // the named Back at the top left
  topBack.hidden = false;
  topBack.querySelector(".ico-lbl").textContent = "Chat";
  return { box: box(term), thread: box(turns), notsent: box(mark), top: box(topBack),
           compose: box(document.getElementById("termview-compose")), back: box(document.getElementById("termview-back")) };
}
""".replace("/*BOX*/", BOX)

# The run detail, whose Back is the second of the two.
DRAW_RUN = """
() => {
  /*BOX*/
  document.getElementById("termview").hidden = true;
  const run = document.getElementById("runview");
  run.hidden = false;
  document.getElementById("runview-name").textContent = "Run 1";
  const topBack = document.querySelector('[data-topback="run"]');
  topBack.hidden = false;
  topBack.querySelector(".ico-lbl").textContent = "Runs";
  return { box: box(run), top: box(topBack), back: box(document.getElementById("runview-back")) };
}
""".replace("/*BOX*/", BOX)

# The attach sheet, risen from the bottom edge with three doors and Cancel.
DRAW_ATTACH = """
() => {
  /*BOX*/
  document.getElementById("runview").hidden = true;
  const sheet = document.getElementById("attach-sheet");
  sheet.replaceChildren();
  for (const word of ["Photo", "File", "Camera"]) {
    const b = document.createElement("button"); b.type = "button"; b.textContent = word; sheet.append(b);
  }
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.id = "attach-cancel";
  cancel.textContent = "Cancel"; sheet.append(cancel);
  sheet.hidden = false;
  return { box: box(sheet), buttons: [...sheet.querySelectorAll("button")].map(box) };
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
  const chips = await page.evaluate(DRAW_CHIPS, [KEYBOARD]);
  const chipsShot = size(await page.screenshot({ path: SHOT_DIR + "/chips.png" }));
  const recording = await page.evaluate(DRAW_RECORDING, [KEYBOARD]);
  const recordingShot = size(await page.screenshot({ path: SHOT_DIR + "/recording.png" }));
  const notSent = await page.evaluate(DRAW_NOT_SENT);
  const notSentShot = size(await page.screenshot({ path: SHOT_DIR + "/not-sent.png" }));
  const run = await page.evaluate(DRAW_RUN);
  const runShot = size(await page.screenshot({ path: SHOT_DIR + "/run.png" }));
  const attach = await page.evaluate(DRAW_ATTACH);
  const attachShot = size(await page.screenshot({ path: SHOT_DIR + "/attach.png" }));
  console.log("EGO_FRAMES:" + JSON.stringify({
    vw: chips.vw, vh: chips.vh,
    frames: {
      chips: { ...chips, shot: chipsShot },
      recording: { ...recording, shot: recordingShot },
      notsent: { ...notSent, shot: notSentShot },
      run: { ...run, shot: runShot },
      attach: { ...attach, shot: attachShot },
    },
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
            f"const WIDTH = {WIDTH}; const HEIGHT = {HEIGHT}; const KEYBOARD = {KEYBOARD};\n"
            f"const DRAW_CHIPS = {DRAW_CHIPS};\nconst DRAW_RECORDING = {DRAW_RECORDING};\n"
            f"const DRAW_NOT_SENT = {DRAW_NOT_SENT};\nconst DRAW_RUN = {DRAW_RUN};\nconst DRAW_ATTACH = {DRAW_ATTACH};\n")
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
def test_the_named_frames_sit_inside_the_viewport_and_the_thumb_zone(phone):
    page_path, shot_dir = phone
    seen = _frames(page_path, shot_dir)
    vw, vh = seen["vw"], seen["vh"]
    assert (vw, vh) == (WIDTH, HEIGHT), f"the phone page rendered at {vw} x {vh}, not {WIDTH} x {HEIGHT}"
    viewport = {"x": 0, "y": 0, "w": vw, "h": vh, "right": vw, "bottom": vh}
    chips, recording = seen["frames"]["chips"], seen["frames"]["recording"]
    notsent, run, attach = seen["frames"]["notsent"], seen["frames"]["run"], seen["frames"]["attach"]

    # The quick answers: one 44 px chip per option, above the composer and above the keyboard, inside the chat's box.
    keyboard_edge = vh - KEYBOARD
    assert abs(chips["box"]["h"] - keyboard_edge) < 1, f"the chat does not take the keyboard's height off: {chips['box']}"
    _inside(chips["box"], viewport, "the chat above the keyboard")
    assert len(chips["chips"]) == 2, f"the chat drew {len(chips['chips'])} chips"
    for i, chip in enumerate(chips["chips"]):
        _inside(chip, chips["box"], f"chip {i}")
        assert chip["h"] >= TAP, f"chip {i} is {chip['h']} px tall, under the {TAP} px tap floor"
        assert chip["bottom"] <= chips["compose"]["y"] + 0.5, f"chip {i} does not sit above the composer: {chip}"
        assert chip["bottom"] <= keyboard_edge, f"chip {i} sits under the keyboard: {chip}"

    # The recording hints: three of them, inside the chat's box and above the keyboard.
    assert recording["hintsCount"] == 3, f"the recording row names {recording['hintsCount']} hints, not three"
    _inside(recording["rec"], recording["box"], "the recording row")
    _inside(recording["hints"], recording["box"], "the recording hints")
    assert recording["hints"]["bottom"] <= keyboard_edge, f"the recording hints sit under the keyboard: {recording['hints']}"

    # The not-sent mark: in the thread, above the composer, so the reader meets it without scrolling.
    _inside(notsent["box"], viewport, "the chat")
    _inside(notsent["notsent"], notsent["thread"], "the not-sent mark")
    assert notsent["notsent"]["bottom"] <= notsent["compose"]["y"] + 0.5, \
        f"the not-sent mark is under the composer: {notsent['notsent']}"

    # The two Backs of a detail (rule 2): the named one at the top left, and the action row's, at the thumb.
    _inside(notsent["top"], notsent["box"], "the chat's top Back")
    assert notsent["top"]["bottom"] <= notsent["box"]["h"] * THUMB, \
        f"the chat's top Back left the top of the screen: {notsent['top']}"
    assert notsent["top"]["h"] >= TAP, f"the chat's top Back is {notsent['top']['h']} px tall"
    _inside(notsent["back"], notsent["box"], "the chat's action-row Back")
    assert notsent["back"]["y"] >= notsent["box"]["h"] * THUMB, \
        f"the chat's action-row Back sits above the thumb zone: {notsent['back']}"
    assert notsent["back"]["h"] >= TAP, f"the chat's action-row Back is {notsent['back']['h']} px tall"
    _inside(run["top"], run["box"], "the run's top Back")
    assert run["top"]["bottom"] <= run["box"]["h"] * THUMB, f"the run's top Back left the top of the screen: {run['top']}"
    _inside(run["back"], run["box"], "the run's action-row Back")
    assert run["back"]["y"] >= run["box"]["h"] * THUMB, f"the run's action-row Back sits above the thumb zone: {run['back']}"
    assert run["back"]["h"] >= TAP, f"the run's action-row Back is {run['back']['h']} px tall"

    # The attach sheet: risen from the bottom edge, every door inside the thumb's reach and a tap target of its own.
    _inside(attach["box"], viewport, "the attach sheet")
    assert attach["box"]["bottom"] >= vh - 0.5, f"the attach sheet does not sit on the bottom edge: {attach['box']}"
    assert len(attach["buttons"]) == 4, f"the attach sheet drew {len(attach['buttons'])} doors, not three and Cancel"
    for i, button in enumerate(attach["buttons"]):
        _inside(button, attach["box"], f"attach door {i}")
        assert button["y"] >= vh * THUMB, f"attach door {i} sits above the thumb zone: {button}"
        assert button["h"] >= TAP, f"attach door {i} is {button['h']} px tall"

    # Each captured frame was really rendered: the screenshots on disk carry bytes.
    for name in ("chips", "recording", "notsent", "run", "attach"):
        assert seen["frames"][name]["shot"] > 1000, f"the {name} frame rendered no screenshot"
