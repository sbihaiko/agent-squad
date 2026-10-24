#!/bin/bash
# basecheckpass.sh (#790): one read of the base's own check per watch cycle, so a red head that no landing reads is still reported
# (the alerts.log line and the queued comments, as basecheck.sh writes them). It holds nothing and fails nothing: a red head is a
# report, and the landings wait on it through their own read (branchland.sh, wave.sh). The base is $SG_LAND_BRANCH, else $SG_BASE, else dev.
here=$(cd "$(dirname "$0")" && pwd)
. "$here/ghcall.sh"; . "$here/address.sh"; . "$here/basecheck.sh"
sg_base_red "${SG_LAND_BRANCH:-${SG_BASE:-dev}}" >/dev/null
exit 0
