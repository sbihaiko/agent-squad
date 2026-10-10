#!/bin/bash
# One janitor pass (see janitor.py). JANITOR_DRY=1 only prints. Off switch: touch $SG_STATE/auto/nojanitor.
. "$(dirname "$0")/env.sh"; [ -e "$SG_STATE/auto/nojanitor" ] && exit 0
SG_STATE="$SG_STATE" SG_CHECKOUT="$SG_CHECKOUT" SG_FORK_DIR="$SG_FORK_DIR" SG_FORK_BASE="$SG_FORK_BASE" SG_REPO="$SG_REPO" SG_BASE="$SG_BASE" python3 -I "$(dirname "$0")/janitor.py"
