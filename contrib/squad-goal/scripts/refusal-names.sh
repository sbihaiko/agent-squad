#!/bin/bash
# usage: . refusal-names.sh   then: sg_refusal_names          (the set, one name per line)
#                                   sg_refusal_named <name>   (0 = a member of the set; 1 = refused)
#        bash refusal-names.sh            prints the closed set, one name per line
#        bash refusal-names.sh <name>     exits 0 for a member; exits 2, naming it, for a name outside the set
#
# THE refusal names: ONE closed set (#142, SPEC 117). Every refusal the loop can report is named on the one line below,
# in a place a reader finds without searching, so a new refusal is a deliberate act in one place and a name cannot drift
# into a dozen spellings (BOARD-WRITE-FAILED beside CARD-WRITE-FAILED beside BOARD-WRITE-REFUSED). The names are the
# shape: the helpers that own it -- the filer (autorefuse.sh), the issue's own comment (refusal.sh) and the write door of
# a pass (autobug.sh) -- each name their refusal through this file, and each refuses a name this set does not hold rather
# than filing, queueing or logging it under a guessed name. This file is one definition and one predicate: it sources
# nothing, reads nothing and writes nothing.
SG_REFUSAL_NAMES="BOARD-WRITE-FAILED BOARD-PREDICT-FAILED DISPATCH-FAILED REFUSAL-COMMENT-FAILED RETARGET-FAILED BOARD-READ-FAILED"
# sg_refusal_names: the whole set, one name per line.
sg_refusal_names() { printf '%s\n' $SG_REFUSAL_NAMES; }
# sg_refusal_named <name>: 0 when <name> is a member of the set, 1 when it is not. The one predicate of the closed set.
sg_refusal_named() { case " $SG_REFUSAL_NAMES " in *" ${1:-} "*) return 0;; esac; return 1; }

case "${BASH_SOURCE[0]}" in "$0")
  [ $# -eq 0 ] && { sg_refusal_names; exit 0; }
  sg_refusal_named "$1" && exit 0
  echo "refusal-names: '$1' is not a refusal name; the closed set is: $(sg_refusal_names | tr '\n' ' ')" >&2; exit 2;;
esac
