#!/bin/bash
# usage: . skiplabels.sh   (then sg_skip_labels [<extra>,...])
# One skip set for every pass that reads work (#1593): the frontier pass (autofrontier.sh), the bug pass (autobug.sh), the
# health pass (health.sh) and the other readers of SG_FRONTIER_SKIP_LABELS (wave.sh, branchfixpass.sh) read this file, so a
# card no dispatcher will ever start is never reported startable. Both lists are a profile's own, read after the profile;
# their defaults live here and nowhere else.
# A spec is a document until the owner starts it, a freeze marker holds the card without changing its column, and a ticket
# that waits on the owner or on an answer is no work yet.
: "${SG_FRONTIER_SKIP_LABELS:=spec,needs-owner,needs-ratification,needs-info,frozen:owner,frozen:dependency,wontfix}"
: "${SG_BUG_SKIP_LABELS:=needs-owner,no-auto,wontfix,question}"
export SG_FRONTIER_SKIP_LABELS SG_BUG_SKIP_LABELS
# sg_skip_labels [<extra>,...]: the comma list of labels no dispatch pass takes as work. The freeze markers are a floor:
# frozen:owner and frozen:dependency hold a card whatever a profile's lists say. The optional argument adds a caller's
# structural labels -- the frontier leaves the bug, debt and run-parent units to their own lanes.
sg_skip_labels() {
  printf '%s,%s,frozen:owner,frozen:dependency%s\n' "$SG_FRONTIER_SKIP_LABELS" "$SG_BUG_SKIP_LABELS" "${1:+,$1}"
}
