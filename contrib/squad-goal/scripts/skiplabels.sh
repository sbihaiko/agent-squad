#!/bin/bash
# usage: . skiplabels.sh   (then sg_skip_labels <frontier|bug> [<extra>,...])
# One skip set for every pass that reads work (#1593): the frontier pass (autofrontier.sh), the bug pass (autobug.sh), the
# health pass (health.sh) and the other readers of SG_FRONTIER_SKIP_LABELS (wave.sh, branchfixpass.sh) read this file, so a
# card no dispatcher will ever start is never reported startable. Each lane keeps its own list; a lane's list is a profile's
# own, read after the profile, and its default lives here and nowhere else.
# A spec is a document until the owner starts it, a freeze marker holds the card without changing its column, and a ticket
# that waits on the owner or on an answer is no work yet.
: "${SG_FRONTIER_SKIP_LABELS:=spec,needs-owner,needs-ratification,needs-info,frozen:owner,frozen:dependency,wontfix}"
: "${SG_BUG_SKIP_LABELS:=needs-owner,no-auto,wontfix,question}"
# No export here: env.sh exports both after the profile (#757), and a sourced library that exports them defeats a
# profile's := default in any suite that sources this file before it runs env.sh in a child.
# sg_skip_labels <frontier|bug> [<extra>,...]: the comma list of labels one lane takes no work from. The freeze markers are a
# floor: frozen:owner and frozen:dependency hold a card whatever a profile's lists say. The frontier's optional argument adds
# its structural labels -- it leaves the bug, debt and run-parent units to their own lanes. The bug lane adds needs-info, a
# ticket that waits on an answer (#729). A label one lane skips is no hold for the other: no-auto and question belong to the
# bug lane alone, and the frontier starts a card that carries one (health.sh reads the frontier lane for its idle alert).
# The floor is spelled out in each arm, never a shared variable: a profile is sourced after this file and must not lift it (#1673).
sg_skip_labels() {
  case "${1:-}" in
    frontier) printf '%s,frozen:owner,frozen:dependency,ready-for-human,ready-for-HAL9K,needs-triage%s\n' "$SG_FRONTIER_SKIP_LABELS" "${2:+,$2}" | tr ',' '\n' | awk 'NF && !s[$0]++' | paste -sd, - ;;
    bug) printf '%s,frozen:owner,frozen:dependency,ready-for-human,ready-for-HAL9K,needs-triage,needs-info\n' "$SG_BUG_SKIP_LABELS" | tr ',' '\n' | awk 'NF && !s[$0]++' | paste -sd, - ;;
    *) echo "sg_skip_labels: name a lane, frontier or bug, not '${1:-}'" >&2; return 2 ;;
  esac
}
# sg_frontier_skip: the frontier's skip set, the one list the frontier pass and the janitor's stale-card rule both read (#2352).
sg_frontier_skip() { sg_skip_labels frontier "${SG_BUG_LABEL:-bug},debt,run-parent"; }
