#!/usr/bin/env python3
"""Render the Communication tree states of the hub Settings page (SPEC 2682, #2842).

The script boots a throwaway hub from this tree, as `test-hub-e2e.py` does: the skill copy sits in a temporary folder,
`SG_STATE` and `SG_HUB_DIR` are temporary, and one neutral profile names a fixture checkout. The page is drawn by
ego-browser, the repository's browser door, in one private task space; headless Chrome is never used and the owner's
profile is never opened (ADR-0137).

States: the channel note, the workspace detail, the empty project and the two-guest conflict. Widths: 1440, 975 and
390 px. The PNGs land in `.scratch/renders/` of the tree, which git never tracks.

Usage: python3 contrib/squad-goal/scripts/render-hub-slack-tree.py [outdir]
Exit 3 when ego-browser is not installed, so the caller tells a missing browser from a render that failed.
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[3]
SKILL = ROOT / "contrib" / "squad-goal"
EGO = shutil.which("ego-browser")
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".scratch" / "renders"
WIDTHS = ((1440, 1000), (975, 1000), (390, 844))

# The committed file and the local file of the neutral fixture: placeholder ids, one owned workspace with two
# channels, and one guest workspace whose channel accepts no role and subscribes to one event (#2842's channel state).
COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "nova"},
    "apps": [{"id": "app", "keychain": {"bot": "probe-bot", "app": "probe-app"}}],
    "personas": [
        {"id": "nova", "name": "Nova", "voice": "Kore", "tone": "Calm and direct."},
        {"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."},
    ],
    "events": [{"id": "stg_release", "template": "Deployed to STG: {items}."}],
    "roles": [{"id": "manager", "actions": ["status", "logs"]}],
    "workspaces": [
        {"id": "T0TEAM0001", "label": "internal-ws", "class": "owned", "app": "app", "language": "pt-BR",
         "channels": [
             {"id": "C0PROJ0001", "name": "proj-agent-squad", "mode": "listen+speak", "roles": ["manager"]},
             {"id": "C0GUES0001", "name": "hal-guest-sandbox", "mode": "listen"},
         ]},
    ],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "language": "en-US", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "product-group", "mode": "listen+speak", "speak": "draft",
              "roles": ["manager"], "announce": ["stg_release"]},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen"},
         ]},
    ],
    "members": [
        {"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "manager", "users": ["U0CLIENT01"],
         "alias": "Sam"},
    ],
}


def two_guests():
    """The local file with a second guest workspace, so the machine serves one and refuses the other."""
    local = json.loads(json.dumps(LOCAL))
    local["workspaces"].append({"id": "T0OTHER001", "label": "client-b", "class": "guest",
                                "channels": [{"id": "C0CLIB0001", "name": "b-general", "mode": "listen+speak"}]})
    return local


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_yaml(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    path.write_text(yaml.safe_dump(doc))


class Hub:
    """One throwaway hub: a copy of the skill, a temporary state and hub dir, and one neutral profile."""

    def __init__(self, base, name, committed, local):
        self.port = free_port()
        self.base = base / name
        self.profiles = self.base / "profiles"
        # The profile's local file is named for the checkout folder (#2899), so this folder is `probe` and the fixture
        # keeps its `probe.slack.local.yaml` name.
        self.checkout = self.base / "probe"
        self.state, self.hubdir = self.base / "state", self.base / "hubdir"
        for d in (self.profiles, self.state, self.hubdir):
            d.mkdir(parents=True)
        if committed is not None:
            write_yaml(self.checkout / "docs" / "squad" / "slack.yaml", committed)
        if local is not None:
            write_yaml(self.profiles / "probe.slack.local.yaml", local)
        # A profile is one shell file (address.sh sources it); `example` is refused by name, so this one is `probe`.
        (self.profiles / "probe.sh").write_text(
            f'SG_REPO="probe/probe"\nSG_OWNER="probe"\nSG_PROJECT="probe"\nSG_CHECKOUT="{self.checkout}"\n')
        self.skill = self.base / "skill"
        shutil.copytree(SKILL, self.skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.proc = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def start(self):
        env = dict({k: v for k, v in os.environ.items() if not k.startswith("SG_")},
                   SG_HUB_DIR=str(self.hubdir), SG_STATE=str(self.state), SG_PROFILES_DIR=str(self.profiles),
                   SG_REPO="probe/probe", SG_OWNER="probe", SG_PROJECT="probe",
                   SG_CHECKOUT=str(self.checkout), SG_SQUAD=str(ROOT))     # the policy resolves from this tree
        self.proc = subprocess.Popen([sys.executable, str(self.skill / "scripts" / "hub.py"), str(self.port)],
                                     stdout=open(self.base / "hub.log", "w"), stderr=subprocess.STDOUT,
                                     start_new_session=True, env=env)
        for _ in range(80):
            try:
                with urllib.request.urlopen(self.url + "api/version", timeout=2):
                    return True
            except Exception:
                time.sleep(0.25)
        return False

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


EGO_SCRIPT = """
const task = await taskSpace(SPACE_NAME);
try {
  const page = task.page("p1");
  /* The page boots once at the default width: the phone redirect at the first line of the page is a boot decision, so
     every frame below changes the metrics on a page already loaded, and the CSS answers the new width on its own. */
  await page.goto(URL);
  await page.evaluate(async () => {
    for (let i = 0; i < 150; i++) {
      if (typeof setTab === "function") break;
      await new Promise(r => setTimeout(r, 100));
    }
    setTab("set"); setTheme("slk");
    /* the tree is the page's own render of /api/slack/files: open Settings, open the Communication theme, pick a node */
    for (let i = 0; i < 150; i++) {
      if (slR && slR.tree && slR.tree.root) break;
      await new Promise(r => setTimeout(r, 100));
    }
  });
  for (const shot of SHOTS) {
    await page.cdp("Emulation.setDeviceMetricsOverride",
                   { width: shot.w, height: shot.h, deviceScaleFactor: 2, mobile: shot.w <= 500 });
    const ok = await page.evaluate(async (opts) => {
      const sel = opts.sel, NARROW = opts.narrow;
      const find = (n, name) => {
        if (!n) return null;
        if (n.name === name) return n;
        for (const c of n.children || []) { const hit = find(c, name); if (hit) return hit; }
        return null;
      };
      const node = sel ? find(slR.tree.root, sel) : null;
      slTsel = node ? node.id : (slR.tree.root || {}).id;
      /* A phone folds the tree on a pick, so a state that lives in the tree pane keeps the tree in view (#2842). */
      slTshow = NARROW && !opts.tree;
      slRender();
      for (let i = 0; i < 50; i++) {
        if (document.querySelector("#sltdetail .slth")) break;
        await new Promise(r => setTimeout(r, 100));
      }
      /* a phone frame starts at the Communication panel, so the frame is not spent on the cards above it */
      const panel = document.getElementById("slk");
      if (NARROW && panel && panel.scrollIntoView) panel.scrollIntoView({ block: "start" });
      else window.scrollTo(0, 0);
      return { tab: typeof setTab, tree: !!(slR && slR.tree && slR.tree.root), node: !!(sel ? node : slR.tree.root) };
    }, { sel: shot.sel, narrow: shot.w <= 600, tree: !!shot.tree });
    if (!ok || !ok.node) { console.log("EGO_MISSING:" + shot.name + ":" + JSON.stringify(ok)); continue; }
    await page.evaluate(() => new Promise(r => setTimeout(r, 300)));
    const bytes = require_size(await page.screenshot({ path: shot.path }));
    console.log("EGO_SHOT:" + JSON.stringify({ name: shot.name, bytes }));
  }
} finally {
  await task.finish({ keep: [] });
}
"""

# `require_size` is not a browser global: the node side reads the file after the shot lands.
EGO_PRELUDE = """
import fs from "node:fs";
const require_size = p => { try { return fs.statSync(p).size; } catch (e) { return 0; } };
"""


def render(hub, shots):
    head = (f"const SPACE_NAME = {json.dumps('agent-squad slack tree ' + uuid.uuid4().hex)};\n"
            f"const URL = {json.dumps(hub.url)};\n"
            f"const SHOTS = {json.dumps(shots)};\n")
    run = subprocess.run([EGO, "nodejs"], input=head + EGO_PRELUDE + EGO_SCRIPT,
                         capture_output=True, text=True, timeout=300)
    out = run.stdout + run.stderr
    return out, [json.loads(line[len("EGO_SHOT:"):]) for line in out.splitlines()
                 if line.startswith("EGO_SHOT:")]


def main():
    if EGO is None:
        print("ego-browser is not installed: no render was taken")
        return 3
    OUT.mkdir(parents=True, exist_ok=True)
    base = ROOT / ".scratch" / f"render-run-{uuid.uuid4().hex[:8]}"     # scratch stays inside the tree
    base.mkdir(parents=True)
    plans = [
        # name, fixture, states (one per width)
        ("channel", dict(committed=COMMITTED, local=LOCAL), "#product-group"),
        ("workspace", dict(committed=COMMITTED, local=LOCAL), "client-a"),
        ("empty", dict(committed=None, local=None), "probe"),       # the project node, which names no Slack file
        ("conflict", dict(committed=COMMITTED, local=two_guests()), "probe"),   # the project detail carries the alert
    ]
    total = 0
    try:
        for i, (name, fixture, sel) in enumerate(plans):
            shots = [{"name": f"{name}-{w}", "w": w, "h": h, "sel": sel, "tree": name == "conflict" and w <= 600,
                      "path": str(OUT / f"{name}-{w}.png")} for w, h in WIDTHS]
            # The hub's threaded server raced two first reads on the import of the policy modules once; a fresh hub
            # answers on the retry, so one plan costs at most two boots.
            for attempt in (1, 2):
                hub = Hub(base, f"{i}-{name}-{attempt}", **fixture)
                if not hub.start():
                    print(f"the hub for {name} did not answer; see {hub.base / 'hub.log'}")
                    return 1
                try:
                    out, seen = render(hub, shots)
                finally:
                    hub.stop()
                if len(seen) == len(shots):
                    break
                print(f"the render of {name} took {len(seen)} of {len(shots)} shots; retrying")
            for line in out.splitlines():
                if line.startswith("EGO_MISSING:"):
                    print(f"no node for {line}")
            for shot in seen:
                print(f"{shot['name']}.png  {shot['bytes']} bytes")
                total += 1 if shot["bytes"] > 1000 else 0
            if len(seen) != len(shots):
                print(out[-2000:])
                return 1
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print(f"{total} renders in {OUT}")
    return 0 if total == len(plans) * len(WIDTHS) else 1


if __name__ == "__main__":
    sys.exit(main())
