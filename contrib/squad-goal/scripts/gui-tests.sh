#!/bin/bash
# usage: gui-tests.sh on|off|status   The Mesen GUI test kill switch.
# Why: the GUI suite drives the real emulator, so it opens windows on the owner's screen and serializes every fork gate behind one
# lock (serial.sh gui-suite). `off` writes the marker that makes serial.sh skip that lock's command and exit 0, so the fork gate and
# the e2e go on while no GUI suite starts; `on` removes it. State: $SG_STATE/auto/gui-tests-off (per the auto/off idiom). A single
# process tree can be silenced instead with SG_GUI_TESTS=no.
st=${SG_STATE:-$HOME/.cache/squad-goal}; f="$st/auto/gui-tests-off"; mkdir -p "$st/auto"
case "${1:-status}" in
  off) touch "$f"; echo "GUI tests OFF ($f)" ;;
  on)  rm -f "$f"; echo "GUI tests ON" ;;
  status) if [ -e "$f" ]; then echo "GUI tests OFF ($f, since $(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "$f"))"; else echo "GUI tests ON"; fi ;;
  *) echo "usage: gui-tests.sh on|off|status" >&2; exit 2 ;;
esac
