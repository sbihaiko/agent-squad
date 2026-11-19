#!/bin/bash
# seconds: 8.84
# usage: test-hubstream.sh   The hub page's push stream (SPEC 1085): versioned events, the mux pass-through and the origin rule, over a real socket.
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
py=${SG_GATE_PY:-${SG_CHECKOUT:+$SG_CHECKOUT/.venv/bin/python}}; [ -x "$py" ] || py=python3
# The runner's temp HOME hides the user site that pytest's pygments comes from; the real user base is read, nothing written.
export PYTHONUSERBASE="${PYTHONUSERBASE:-$(HOME="$(eval echo "~$(id -un)")" python3 -c 'import site;print(site.USER_BASE)')}"
cd "$root" && exec "$py" -m pytest tests/test_hub_stream.py -q -p no:deepeval -p no:cacheprovider -p no:xdist
