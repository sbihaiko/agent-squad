# #926 Spend & limits: ego-browser check

The hub served one spare loopback port with temp directories, never the live hub and never the live profile:

```
SG_STATE=/tmp/r926-visual/state SG_HUB_DIR=/tmp/r926-visual/hub \
SG_PROFILES_DIR=/tmp/r926-visual/profiles SG_PROFILE=t SG_REPO=x/y SG_OWNER=x SG_PROJECT=4 \
python contrib/squad-goal/scripts/hub.py 7791
```

The profile under test names a 1500000 daily cap and a pause factor of 3. The theme was opened in ego-browser, one private
task space for this goal, and the page was read in the same process that set the device metrics, so the emulation held for
the screenshot.

- `desktop.png`: 1440 px. The sidebar carries "Spend & limits": guard state, daily token cap, pause factor, the hourly
  pause line, the last-hour and window burn, the dispatch state, the last change, and Save/Revert.
- `phone-390.png`: 390 x 844, mobile emulation. The same rows, one column, full width.

Measured in the page at 390 px:

```
innerWidth 390   documentElement.scrollWidth 390   #side 390px   #lbody scrollWidth - clientWidth 0
#win display:block
```

There is no sideways scroll at 390 px, and the theme's own panel does not overflow. The docked board (`body.dockb`) lays
the window out as a two-column grid; the phone rule unsays that grid, which is what a phone could not fit.

A screenshot check is evidence, never an ac_command.
