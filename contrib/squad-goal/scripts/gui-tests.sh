#!/bin/bash
# usage: gui-tests.sh on|off|status   The Mesen GUI test kill switch.
# Why: the GUI suite drives the real emulator, so it opens windows on the owner's screen and serializes every fork gate behind one
# lock (serial.sh gui-suite). `off` writes the marker that makes serial.sh skip that lock's command and exit 0, so the fork gate and
# the e2e go on while no GUI suite starts; `on` removes it. State: $SG_STATE/auto/gui-tests-off (per the auto/off idiom). A single
# process tree can be silenced instead with SG_GUI_TESTS=no.
[ -n "${SG_STATE:-}" ] || . "$(dirname "$0")/env.sh"; st=$SG_STATE; f="$st/auto/gui-tests-off"; mkdir -p "$st/auto"
# A second copy OUTSIDE $SG_STATE. A test or suite that inherits the machine's SG_STATE can clean that directory (observed
# 2026-10-11: the fork-gate fixture drove the real state dir and the marker vanished twice in minutes), so the durable switch
# cannot live only there. $HOME/.squad-gui-tests-off is written and read by nothing else.
case "${1:-status}" in
  off) touch "$f" "$HOME/.squad-gui-tests-off"; echo "GUI tests OFF ($f)" ;;
  on)  rm -f "$f" "$HOME/.squad-gui-tests-off"; echo "GUI tests ON" ;;
  status) if [ -e "$f" ] || [ -e "$HOME/.squad-gui-tests-off" ]; then echo "GUI tests OFF (state marker: $( [ -e "$f" ] && echo yes || echo MISSING); $HOME/.squad-gui-tests-off: $( [ -e "$HOME/.squad-gui-tests-off" ] && echo yes || echo MISSING))"; else echo "GUI tests ON"; fi ;;
  *) echo "usage: gui-tests.sh on|off|status" >&2; exit 2 ;;
esac
