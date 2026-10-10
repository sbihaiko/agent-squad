"""GUI test squad e2e suite (local-only): a 3-step script against a fake adapter,
the renderer, and the reverse-drift case against MesenAI's committed files."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

from tests.gui_test_helpers import script, step
from squad.gui_test import validate
from squad.gui_test_render import render
from squad.gui_test_run import run

FORK = Path(__file__).resolve().parent.parent


class FakeAdapter:
    """Home screen with one card; Right is a no-op, A opens the library."""

    def __init__(self):
        self.screen, self.focus, self.log = "play.home", "play.home.open-rom", []

    def inject(self, name, args):
        self.log.append((name, args))
        if name == "pad.press" and args.get("button") == "A":
            self.screen = "play.library"

    def wait(self, condition, timeout):
        return "met" if condition is None or self._holds(condition) else "timeout"

    def _holds(self, cond):
        key, _, want = cond.partition(" == ")
        return {"ui.screen": self.screen, "ui.focused": self.focus}[key] == want

    def check(self, name, args):
        if args["is"].endswith("ghost"):
            return "unknown id"
        return name == "ui.focused" and self.focus == args["is"] or \
            name == "ui.screen" and self.screen == args["is"]


def three_steps():
    return script(
        step("b1.right-holds"),
        step("b1.open-library",
             action={"name": "pad.press", "args": {"button": "A", "ticks": 4}},
             wait={"check": "ui.screen == play.library", "timeout_ticks": 10},
             check={"name": "ui.screen", "args": {"is": "play.library"}}),
        step("b1.ghost", check={"name": "ui.focused", "args": {"is": "play.ghost"}},
             wait={"timeout_ticks": 2}),
    )


def test_three_step_script_runs_against_a_fake_adapter():
    d = three_steps()
    validate(d)
    results = run(d, FakeAdapter())
    assert [(r["id"], r["verdict"]) for r in results] == [
        ("b1.right-holds", "passed"),
        ("b1.open-library", "passed"),
        ("b1.ghost", "failed (unknown id)"),
    ]


def test_wait_timeout_is_a_failed_step_naming_the_wait():
    d = script(step("b1.t", wait={"check": "ui.screen == nowhere", "timeout_ticks": 3}))
    [r] = run(d, FakeAdapter())
    assert r["verdict"] == "failed (timeout waiting for ui.screen == nowhere)"


def test_rendering_is_deterministic_and_lists_every_step():
    d = three_steps()
    md = render(d)
    assert md == render(d)
    for sid in ("b1.right-holds", "b1.open-library", "b1.ghost"):
        assert f"`{sid}`" in md


def _mesenai():
    root = os.environ.get("MESENAI_REPO")
    assert root, "set MESENAI_REPO to the MesenAI checkout holding the pilot script"
    return Path(root)


def test_reverse_drift_committed_markdown_equals_live_render():
    docs = _mesenai() / "docs" / "validation" / "process"
    pilots = sorted(docs.glob("*.gui-test.json"))
    assert pilots, "no committed pilot script"
    for p in pilots:
        d = json.loads(p.read_text(encoding="utf-8"))
        validate(d)
        assert render(d) == p.with_suffix(".md").read_text(encoding="utf-8"), p.name


def test_reverse_drift_vendored_renderer_equals_fork_file_at_pinned_commit():
    vendored = _mesenai() / "scripts" / "vendor" / "agent_squad" / "gui_test_render.py"
    text = vendored.read_text(encoding="utf-8")
    header = {k: v for k, v in (l[2:].split(": ", 1) for l in text.splitlines()
                                if l.startswith("# ") and ": " in l[2:])}
    body = text.split("# ---- end vendored header ----\n", 1)[1].encode()
    pinned = subprocess.run(["git", "-C", str(FORK), "show", f"{header['source-commit']}:squad/gui_test_render.py"],
                            capture_output=True, check=True).stdout
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(pinned).hexdigest()
    assert header["sha256"] == hashlib.sha256(pinned).hexdigest()
