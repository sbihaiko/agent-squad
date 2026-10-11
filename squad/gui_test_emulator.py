"""The emulator's gui-test/1 adapter (ADR-0272): it places the script's fixtures, starts the application with the
test hook, injects the input actions over the hook's local socket and captures the window.

Since #1779 the hook answers the release, the chord, the keyboard and the `nav.goal` read beside the single press,
so the adapter drives them too (#1780): a pad-only batch is no longer manual for the adapter not asking.

It never skips: a fixture it cannot place, or an application that does not come up, is a failure with the reason.
"""
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from squad.gui_test_checks import parse
from squad.gui_test_run import run

TICK_SECONDS = 0.05
SETTLE_SECONDS = 5
# A fresh install starts in Player on the Play door, with no play history (ADR-0256 Decision 8).
# SingleInstance is off: the app's guard is a machine-wide named mutex, so while any other Mesen runs (a sibling suite,
# the user's own copy) a launch hands off to it and exits 0 before its hook comes up.
PROFILES = {"fresh": {"Preferences": {"UiMode": "Player", "Workspace": "Play", "SingleInstance": False}}}
KNOWN_PROFILES = tuple(PROFILES)
NES_HEADER = 16
# #1780: the actions the adapter drives, and the hook answers them all since #1779 (hook version 2).
# `pad.press` was the whole of it while the hook drove a single press; `pad.release` and `pad.chord` are the
# two-button gestures a press cannot express, `key.press` is the application's own keyboard at the layer it
# reads, and `nav.goal` is not an input at all - it is the hook's read of the state a move is chosen from and
# the moves it will accept (ADR-0271 items 1-2), which the runner's Navigator chooses among (item 6 A).
ACTIONS = ["pad.press", "pad.release", "pad.chord", "key.press", "nav.goal"]
# The advertised actions that inject nothing: asking one is a read, so it is never a press that came too early. They
# are advertised under `reads` beside them, which is how the runner tells an adapter that answers the hook's read from
# one that performs the goal itself (`squad.gui_test_run.goal_route`, #1780).
READS = ("nav.goal",)


# #1282: the checks that read what a player sees (the hook's own state, ADR-0271) and the vocabularies
# they name - the action bar's closed id list, the Play surfaces, and the ports the lamps label.
# #1282: the checks that read what a player sees (the hook's own state, ADR-0271). The advertised four are
# the ones a gui-test/1 script may name - `ui.*` is the snapshot namespace. Issue #1282 also names
# `emu.paused`, `pad.lamps` and `pad.haptics`; ADR-0272 item 7 reserves `emu.*` (not part of v1) and makes
# `pad.*` the ACTION namespace, so the validator refuses them in a script: the adapter answers them (a
# direct `session.check`, used by the emulator-side adapter and by the MesenAI hook), and they are NOT
# advertised, because a name a script cannot write is not a capability of the format. The mismatch is
# flagged for the owner rather than resolved by amending the accepted ADR.
CHECKS = ["ui.screen", "ui.focused", "ui.visible", "ui.dialogs", "ui.ring", "ui.footer", "ui.surface", "ui.items"]
PLAYER_VISIBLE_CHECKS = {"ui.ring", "ui.footer", "ui.surface", "emu.paused", "pad.lamps", "ui.items", "pad.haptics"}
FOOTER_IDS = ("move", "confirm", "favorite", "search", "settings", "console-filter", "back")
SURFACES = ("play.quit-game", "play.select-rom", "play.shader", "play.tool", "play.bios",
            "play.controller-setup", "play.settings.system", "play.settings", "play.controller-sheet",
            "play.pack-dep", "play.pack-picker", "play.enhancements", "play.pack-detail", "play.cheats",
            "play.replays", "play.save-states", "play.pause", "play.library", "play.library-browse",
            "play.library-folders")
NONE_ID = "none"


def _port(value):
    """One of the four ports the lamps label ("P1"), 1-based; anything else is not a port."""
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4 else None


class AdapterError(Exception):
    """The hook refused a request or answered nothing."""


class FixtureError(AdapterError):
    """A fixture the script names could not be placed."""


class ProcessCrash(AdapterError):
    """The application exited while a step was running."""


def _sha1_no_intro(path):
    """The No-Intro SHA1 of a ROM (ADR-0003): the file minus its iNES header."""
    data = Path(path).read_bytes()
    if Path(path).suffix.lower() != ".nes":
        raise FixtureError(f"fixture rom: {Path(path).name} is not a console the adapter hashes (only .nes)")
    return hashlib.sha1(data[NES_HEADER:] if data[:4] == b"NES\x1a" else data).hexdigest()


class EmulatorAdapter:
    def __init__(self, out_dir, app_cmd, library=None, work_dir=None, env=None, startup_timeout=60, portable=False):
        """`portable`: the application finds its home next to its executable when a settings.json is there (and on
        macOS ignores $HOME), so each session runs from a clone of the application folder with its own settings.json.
        Without it the session only gets its own $HOME, which is enough for a stand-in application."""
        self.portable = portable
        self.out_dir, self.app_cmd = Path(out_dir), list(app_cmd)
        self.library = Path(library) if library else None
        self.work_dir = Path(work_dir) if work_dir else None
        self.extra_env, self.startup_timeout = dict(env or {}), startup_timeout
        self.session_dir = self.proc = self.sock = self.reader = None
        self.command, self.env, self.token, self._id, self._captures = [], {}, "", 0, 0
        self._nav_goal, self._nav = None, None

    def capabilities(self):
        return {"actions": list(ACTIONS), "reads": list(READS), "checks": list(CHECKS),
                "input_families": ["ticks"], "variants": {"window.mode": ["windowed"]}}

    # ---- fixtures, placed before the process starts ----

    def _place_fixtures(self, fixtures):
        profile = (fixtures.get("settings") or {}).get("profile", "fresh")
        if profile not in KNOWN_PROFILES:
            raise FixtureError(f"fixture settings.profiles.{profile}: the emulator adapter implements only {', '.join(KNOWN_PROFILES)}")
        home = self._clone_app() if self.portable else self.session_dir / "home" / "MesenAI"
        home.mkdir(parents=True, exist_ok=True)
        if self.portable:
            (home / "settings.json").write_text(json.dumps(PROFILES[profile]), encoding="utf-8")
        rom = fixtures.get("rom")
        placed = None
        if rom:
            placed = self._place_rom(rom)
        pack = fixtures.get("pack")
        if pack:
            src = Path(pack["path"]).expanduser()
            if not src.is_dir():
                raise FixtureError(f"fixture pack: {src} not found")
            if placed is None:
                raise FixtureError("fixture pack: a pack is installed under its ROM's file name, and the script names no rom")
            shutil.copytree(src, home / "HdPacks" / Path(placed).stem)
        for dest, source in (fixtures.get("files") or {}).items():
            src = Path(source).expanduser()
            if not src.is_file():
                raise FixtureError(f"fixture file {dest}: {src} not found")
            target = home / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        return placed

    def _clone_app(self):
        """A copy-on-write clone of the application folder (APFS), a plain copy elsewhere."""
        src = Path(self.app_cmd[0]).resolve().parent
        dest = self.session_dir / "app"
        if subprocess.run(["cp", "-Rc", str(src), str(dest)], capture_output=True).returncode != 0:
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, symlinks=True)
        self._exe = dest / Path(self.app_cmd[0]).name
        return dest

    def _place_rom(self, rom):
        sha1 = rom.get("sha1", "")
        if not re.fullmatch(r"[0-9a-f]{40}", sha1):
            raise FixtureError(f"fixture rom: sha1 {sha1!r} is a placeholder, not a No-Intro SHA1 (ADR-0003)")
        raw = rom["path"]
        if raw.startswith("<library>"):
            if self.library is None:
                raise FixtureError("fixture rom: the script names <library> and the adapter was given no library folder")
            raw = str(self.library) + raw[len("<library>"):]
        src = Path(raw).expanduser()
        if not src.is_file():
            raise FixtureError(f"fixture rom: {src} not found")
        got = _sha1_no_intro(src)
        if got != sha1:
            raise FixtureError(f"fixture rom: {src.name} hashes to {got}, the script wants {sha1}")
        (self.session_dir / "roms").mkdir()
        target = self.session_dir / "roms" / src.name
        shutil.copyfile(src, target)
        return str(target)

    # ---- the session ----

    def launch(self, fixtures):
        self.teardown()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        base = self.work_dir
        if base:
            base.mkdir(parents=True, exist_ok=True)
        # A Unix socket path is capped near 104 bytes, so the session lives under a short folder.
        self.session_dir = Path(tempfile.mkdtemp(prefix="mg-", dir=str(base) if base and len(str(base)) < 40 else "/tmp"))
        try:
            rom = self._place_fixtures(fixtures or {})
            home = str(self.session_dir / "home")
            self.env = {**os.environ, "HOME": home, "XDG_CONFIG_HOME": home, **self.extra_env}
            if self.portable:  # the real HOME stays, for the application's own libraries; its settings are the clone's
                self.env["HOME"] = os.environ["HOME"]
            self.token = secrets.token_hex(8)
            endpoint = str(self.session_dir / "hook.sock")
            self.command = ([str(self._exe)] + self.app_cmd[1:] if self.portable else self.app_cmd) + [f"--test-hook={endpoint}", f"--test-hook-token={self.token}"] + ([rom] if rom else [])
            self._log = open(self.session_dir / "app.log", "wb")
            self.proc = subprocess.Popen(self.command, env=self.env, stdout=self._log, stderr=subprocess.STDOUT, start_new_session=True)
            self._connect(endpoint)
        except BaseException:
            self.teardown()
            raise
        return self

    def _connect(self, endpoint):
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AdapterError(f"the application exited with code {self.proc.returncode} before the hook came up: {self._log_tail()}")
            if os.path.exists(endpoint):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(endpoint)
                except OSError:
                    s.close()
                else:
                    self.sock, self.reader = s, s.makefile("r", encoding="utf-8")
                    self._ask({"op": "hello"})
                    return
            time.sleep(0.1)
        raise AdapterError(f"the hook did not come up in {self.startup_timeout}s: {self._log_tail()}")

    def _log_tail(self):
        try:
            return (self.session_dir / "app.log").read_text(errors="replace")[-400:].strip() or "(no output)"
        except OSError:
            return "(no output)"

    def _ask(self, body):
        self._id += 1
        line = json.dumps({"id": self._id, "token": self.token, **body}) + "\n"
        try:
            self.sock.sendall(line.encode())
            answer = self.reader.readline()
        except OSError:
            answer = ""
        if not answer:
            self.proc.poll()
            raise ProcessCrash(f"process crash: the application closed the hook (exit {self.proc.returncode}): {self._log_tail()}")
        reply = json.loads(answer)
        if not reply.get("ok"):
            raise AdapterError(f"hook refused {body['op']}: {reply.get('error')}")
        return reply

    def inject(self, name, args):
        """A press right after launch can answer `unknown button Up on pad 1` until the app has built its key names
        (the same press a moment later is taken), so that one refusal is retried for up to 10 s.

        `nav.goal` is a read (the hook injects nothing for it), so it is asked once and its answer is kept: `moves`
        serves the moves it handed, which are the application's own and do not depend on the goal."""
        self._nav = None
        if name in READS:
            self._nav_goal = (args or {}).get("goal") or self._nav_goal
        deadline = time.monotonic() + 10
        while True:
            try:
                reply = self._ask({"op": "inject", "action": name, "args": args})
                if name in READS:
                    self._nav = reply
                return reply
            except AdapterError as e:
                if name in READS or "unknown button" not in str(e) or time.monotonic() > deadline:
                    raise
                time.sleep(0.25)

    def _state(self):
        return self._ask({"op": "state"})

    def ui_state(self):
        """The state a goal's move is chosen from: the hook's own snapshot, which is what ADR-0271 item 1 asks for -
        the UI state, never a screenshot."""
        return self._state()

    def moves(self, family):
        """The moves the hook handed for the goal it was asked (ADR-0271 item 2), in the family the step names.

        Only the moves the application itself answered are here: the hook hands the pad's own navigation presses in
        the duration family its clock allows, so a `key` family has none until it hands keyboard moves too. A goal
        nobody asked yet has no moves, which is why the runner asks before the Navigator chooses."""
        want = "key.press" if family == "key" else "pad.press"
        return [m for m in (self._nav or {}).get("moves", []) if m.get("name") == want]

    def advance(self):
        """One application tick. A Navigator settles by spending ticks, and here they are the application's own clock
        (the hook's `tick`), the same unit every wait counts in."""
        start = self._state()["tick"]
        deadline = time.monotonic() + TICK_SECONDS * 8 + 1
        while self._state()["tick"] == start and time.monotonic() < deadline:
            time.sleep(0.01)

    def _holds(self, state, cond):
        """`ui.screen` and `ui.focused` are read off the snapshot the poll already took; every other name the checks
        answer is read through `check`, so a wait and a step's own check read the same thing (#1780)."""
        key, _, want = cond.partition(" == ")
        if key in ("ui.screen", "ui.focused"):
            return {"ui.screen": state.get("screen"), "ui.focused": state.get("focus")}[key] == want
        return self.check(*parse(cond)) is True

    def wait(self, condition, timeout):
        """`timeout` counts the application's ticks. Without a condition the wait spends them all: a step that
        asserts nothing moved needs the ticks to have passed, not the first state it sees."""
        start = self._state()["tick"]
        deadline = time.monotonic() + timeout * TICK_SECONDS * 4 + 5
        while True:
            state = self._state()
            if condition is not None and self._holds(state, condition):
                return "met"
            if state["tick"] >= start + timeout:
                return "met" if condition is None else "timeout"
            if time.monotonic() > deadline:
                return "timeout"
            time.sleep(0.02)

    def check(self, name, args):
        state = self._state()
        # #1282: what a player judges by eye, read from the hook's own UI state - never a pixel. The
        # ids are the application's own; one the application does not have is "unknown id", never false.
        if name in PLAYER_VISIBLE_CHECKS:
            return self._check_player_visible(name, state, args)
        want = args["is"]
        controls = state.get("controls", [])
        known = {c["id"] for c in controls} | {state.get("screen"), state.get("focus")} | set(state.get("dialogs", []))
        if name == "ui.dialogs" and want == "none":
            return not state.get("dialogs")
        if want not in known:
            return "unknown id"
        if name == "ui.screen":
            return state.get("screen") == want
        if name == "ui.focused":
            return state.get("focus") == want
        if name == "ui.visible":
            # The first-run and populated Homes both carry play.home.open-rom; one is on screen at a time.
            return any(c["visible"] for c in controls if c["id"] == want)
        return want in state.get("dialogs", [])

    def _check_player_visible(self, name, state, args):
        """#1282: the ring, the action bar, the open surface, paused, the port lamps, the focused
        list's entries and the haptic ticks the menu asked for since the last step."""
        controls = {c["id"] for c in state.get("controls", [])} | {state.get("screen"), state.get("focus")} | set(state.get("dialogs", []))
        if name == "ui.ring":
            ring = state.get("ring") or {}
            if args["is"] != NONE_ID and args["is"] not in controls:
                return "unknown id"
            return (ring.get("target") if ring.get("visible") else NONE_ID) == args["is"]
        if name == "ui.surface":
            if args["is"] != NONE_ID and args["is"] not in SURFACES:
                return "unknown id"
            return state.get("surface", NONE_ID) == args["is"]
        if name == "ui.footer":
            bar = [str(action) for action in state.get("footer", [])]
            if "contains" in args:
                if args["contains"] not in FOOTER_IDS:
                    return "unknown id"
                return args["contains"] in bar
            if any(word and word not in FOOTER_IDS for word in str(args["is"]).split(",")):
                return "unknown id"
            return ",".join(bar) == args["is"]
        if name == "ui.items":
            items = [str(item) for item in state.get("items", [])]
            # #1282: an entry the application does not draw is "unknown id", never a list that happens to
            # differ - a renamed entry has to break the run loudly, the way a renamed control does. `is`
            # names the whole list (a bare id names the one entry, the way the shared contract suite asks
            # every advertised check); `contains` names the one entry that has to be drawn.
            if "is" in args:
                named = args["is"] if isinstance(args["is"], (list, tuple)) else [args["is"]]
                named = [str(entry) for entry in named]
                return items == named if all(entry in items for entry in named) else "unknown id"
            if str(args["contains"]) not in items:
                return "unknown id"
            return True
        if name == "emu.paused":
            return bool(state.get("paused")) == bool(args["is"])
        if name == "pad.lamps":
            port = _port(args["port"])
            if port is None:
                return "unknown id"
            lamps = state.get("lamps") or {}
            if not lamps.get("shown"):
                got = "hidden"
            else:
                got = next((("lit" if lamp["lit"] else "dim") for lamp in lamps.get("ports", [])
                            if lamp["port"] == port), "hidden")
            return got == args["is"]
        port = _port(args["pad"])
        if port is None:
            return "unknown id"
        count = next((entry["count"] for entry in state.get("haptics", []) if entry["pad"] == port), 0)
        return count >= args["at_least"] if "at_least" in args else count == args["is"]

    def _settled(self):
        """The window has drawn its screen: a named control other than the screen's root is on it. The hook names
        the screen as soon as the view exists, a beat before its content is laid out (a blank page)."""
        state = self._state()
        return state.get("screen") is None or any(c["visible"] and c["id"] != state["screen"] for c in state.get("controls", []))

    def capture(self):
        deadline = time.monotonic() + SETTLE_SECONDS
        while not self._settled() and time.monotonic() < deadline:
            time.sleep(0.05)
        self._captures += 1
        path = self.out_dir / f"capture-{self._captures}.png"
        reply = self._ask({"op": "capture", "path": str(path)})
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != reply["sha256"]:
            raise AdapterError(f"capture {path.name}: the file hashes to {digest}, the hook reported {reply['sha256']}")
        return {"path": str(path), "size": len(data), "sha256": digest}

    def teardown(self):
        """Quit the way the application's own Exit does, then clean up; report what could not be restored."""
        left = []
        if self.proc is not None:
            if self.proc.poll() is None:
                try:
                    self._ask({"op": "quit"})
                except AdapterError:
                    pass
                try:
                    self.proc.wait(10)
                except subprocess.TimeoutExpired:
                    left.append("the application did not exit after quit and was killed (settings may not have been written)")
                    self.proc.kill()
                    self.proc.wait()
            elif self.proc.returncode not in (0, None) and self.proc.returncode != -9:
                left.append(f"the application had already exited with code {self.proc.returncode}")
            self.proc = None
        for h in (self.reader, self.sock, getattr(self, "_log", None)):
            if h:
                h.close()
        self.reader = self.sock = self._log = None
        if self.session_dir:
            shutil.rmtree(self.session_dir, ignore_errors=True)
            self.session_dir = None
        return left


def _batch_fixtures(script, batch):
    """What a batch asks for: `fixture <path>` tokens in its steps' preconditions, resolved against the script."""
    wanted = {"settings": {"profile": "fresh"}}
    declared = script.get("fixtures") or {}
    text = " ".join(s.get("precondition") or "" for k in ("setup", "steps", "teardown") for s in batch[k])
    for ref in re.findall(r"fixture ([\w.\-]+)", text):
        parts = ref.split(".")
        if parts[:2] == ["settings", "profiles"] and len(parts) == 3:
            wanted["settings"] = {"profile": parts[2]}
        elif parts[0] in declared and parts[0] != "settings":
            wanted[parts[0]] = declared[parts[0]]
    return wanted


def run_launched(script, adapter, record=None, only=None, goal=None):
    """Run each batch in a fresh process launched with the fixtures it names (ADR-0272); `only` keeps those step ids.

    `goal` is the Navigator a `nav.goal` step is reached by (#1780); with none the step is `pending`, which is what
    ADR-0271 Decision 6 A asks of a run with no chooser. A fixture that cannot be placed, or a crash, fails the
    batch's steps with the reason; it never raises.
    """
    results, offset = [], 0

    def shifted(event):
        record({**event, "pos": event["pos"] + offset})

    for batch in script["batches"]:
        batch = {**batch, "steps": [s for s in batch["steps"] if only is None or s["id"] in only]}
        if not batch["steps"]:
            continue
        sub = {**script, "batches": [batch]}
        events = []
        try:
            adapter.launch(_batch_fixtures(script, batch))
            got = run(sub, adapter, record=events.append, goal=goal)
        except FixtureError as e:
            got = [{"id": s["id"], "verdict": f"failed (fixture: {e})", "capture": None} for s in batch["setup"] + batch["steps"] + batch["teardown"] if s["mode"] == "automated"]
        except ProcessCrash:
            done = {ev["node"] for ev in events if ev["event"] == "gui_verdict"}
            got = [{"id": ev["node"], "verdict": ev["verdict"], "capture": None} for ev in events if ev["event"] == "gui_verdict"]
            rest = [s for s in batch["setup"] + batch["steps"] + batch["teardown"] if s["mode"] == "automated" and s["id"] not in done]
            got += [{"id": s["id"], "verdict": "failed (process crash)", "capture": None} for s in rest]
            for s in rest[:1]:
                events.append({"event": "gui_verdict", "node": s["id"], "pos": len(events), "verdict": "failed (process crash)"})
        except AdapterError as e:
            got = [{"id": s["id"], "verdict": f"failed ({e})", "capture": None} for s in batch["setup"] + batch["steps"] + batch["teardown"] if s["mode"] == "automated"]
        finally:
            adapter.teardown()
        if record:
            for event in events:
                shifted(event)
            offset += len(events)
        results += got
    return results


def main(argv=None):
    """python -m squad.gui_test_emulator <script.json> --app <cmd> --out <dir> [--library <dir>] [--only <id,id>]

    Runs the script unattended and writes replay events (events.jsonl) and verdicts (results.json) to --out.

    With `--evidence-repo`, every failed and needs-review capture goes through the privacy gate and onto that repo's
    evidence branch, and the step records the URL (or `withheld`); a green run uploads nothing (#1244)."""
    import argparse
    ap = argparse.ArgumentParser(prog="squad.gui_test_emulator")
    ap.add_argument("script")
    ap.add_argument("--app", required=True, help="the emulator command, split on spaces")
    ap.add_argument("--out", required=True)
    ap.add_argument("--library")
    ap.add_argument("--portable", action="store_true", help="run from a clone of the app folder with its own settings.json")
    ap.add_argument("--only", help="comma-separated step ids to run")
    ap.add_argument("--evidence-repo", help="owner/name whose evidence branch holds this run's failed screenshots")
    ap.add_argument("--approvals", help="the privacy gate's approvals file (default: <out>/approvals.json)")
    a = ap.parse_args(argv)
    from squad.gui_test import validate
    script = json.loads(Path(a.script).read_text(encoding="utf-8"))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    adapter = EmulatorAdapter(out, a.app.split(), library=a.library, portable=a.portable)
    only = set(a.only.split(",")) if a.only else None
    if only:
        # Validate only the slice asked for: the rest may need actions the pad-only adapter does not advertise.
        script = {**script, "batches": [{**b, "steps": [s for s in b["steps"] if s["id"] in only]} for b in script["batches"]]}
        script["batches"] = [b for b in script["batches"] if b["steps"]]
        script = {**script, "requires": {"actions": adapter.capabilities()["actions"], "checks": adapter.capabilities()["checks"]},
                  "variants": {"window.mode": ["windowed"]}}
    validate(script, adapter.capabilities())
    events = []
    results = run_launched(script, adapter, record=events.append, only=only)
    if a.evidence_repo:
        from squad.gui_test_report import evidence_lock, evidence_store, upload_evidence
        results = upload_evidence(results, evidence_store(a.evidence_repo), a.approvals or out / "approvals.json",
                                  lock_path=evidence_lock(a.evidence_repo))
    (out / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for r in results:
        print(f"{r['id']}: {r['verdict']}")
    return 0 if all(r["verdict"] == "passed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
