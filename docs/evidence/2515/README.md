# #2515 Settings without the Loop theme: ego-browser check

A throwaway hub served a spare loopback port from this branch, with temporary home and state directories. Its
`loop-audit.jsonl` held two change lines, so the Loop tab drew a change list with entries.

```
SG_STATE=$PWD/.scratch/vis/state SG_HUB_DIR=$PWD/.scratch/vis/hub \
SG_PROFILES_DIR=$PWD/.scratch/vis/profiles SG_PROFILE=t SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 SG_CHECKOUT=$PWD \
python contrib/squad-goal/scripts/hub.py 7841
```

The renders came from one ego-browser task space, one private space for this goal, never the default profile and never
headless Chrome. Each render set the device metrics in the same process that took the screenshot, and each `goto` carried
a unique query so a hash change forced a fresh document. The change list renders with its disclosure opened through
`document.querySelector("#lopaud details.aud").open = true`.

Run this from the worktree root: `OUT` derives from `$PWD`, so the six renders land beside this README. It writes every
render below in one pass: two widths, both tabs, and the change list closed and open.

```js
const OUT = process.cwd() + "/docs/evidence/2515";
const task = await taskSpace("issue 2515 settings without the loop theme");
const page = task.page("p1");
for (const width of [1440, 1000]) {
  await page.cdp("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: false });
  // Settings: the theme list and the phone select, with no Loop theme.
  await page.goto(`http://127.0.0.1:7841/?w=${width}#settings`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1600);
  await page.screenshot({ path: `${OUT}/settings-${width}.png`, fullPage: true });
  // The Loop tab: the Now tiles and the change list closed.
  await page.goto(`http://127.0.0.1:7841/?w=${width}#loop`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1600);
  await page.screenshot({ path: `${OUT}/loop-${width}.png`, fullPage: true });
  // The Loop tab: the change list open.
  await page.evaluate(() => { document.querySelector("#lopaud details.aud").open = true; });
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/loop-open-${width}.png`, fullPage: true });
}
```

The `#loop` hash is the Loop tab (`HASH={prof:"#loop"}`); `#settings` is the Settings tab. An old `#set-lop` link opens
the first theme left, and Settings never lists a Loop theme.

- `settings-1440.png`: 1440 px. The theme list reads Spend & limits, Subscriptions, Models & chains, Communication and
  Access. It holds no Loop theme, and the phone select carries the same five themes.
- `settings-1000.png`: 1000 px. The theme list keeps one line per theme, with its state line under the name. The page
  has no sideways scroll.
- `loop-1440.png`: 1440 px. The Loop tab keeps the Now tiles and the change list under the profiler. Three tiles share
  one row, and the note and the Changes disclosure align on the tiles' left edge.
- `loop-1000.png`: 1000 px. The three tiles keep equal widths, the note and the Changes disclosure stay aligned on one
  left edge, and the Last change value wraps inside its tile.
- `loop-open-1440.png`: 1440 px, the Changes disclosure open. Two change lines, one line each.
- `loop-open-1000.png`: 1000 px, the Changes disclosure open. Each change line stays on one line.

Measured in the page, from the rendered boxes:

```
1440 px  innerWidth 1440  scrollWidth 1440  tiles 372 px each at x 296/674/1052  disclosure 1128 px  lines fit
1000 px  innerWidth 1000  scrollWidth 1000  tiles 225 px each at x 296/527/759   disclosure  688 px  lines fit
```

At both widths there is no sideways scroll, no line whose content is wider than its box, and the loop theme list and the
phone select name the same five themes.

A screenshot check is evidence, never an ac_command.
