#!/usr/bin/env python3
"""Print the epoch when the Codex (ChatGPT) quota frees up, read from the Squad hub's quota cache ($SG_SHARED/hub-quota.json, one per machine;
refreshed every 20 min from `codex app-server`). Prints nothing when no window is full, so a caller keeps its own fallback.
usage: codex-reset.py [--hit] [--sync <marker>]
  --hit: a usage-limit error was just seen, so a stale cache that shows no full window falls back to now+1h.
  --sync <marker>: keep <marker> in step with the cache -- write the reset into it, and DELETE it once the cache, which is
  refreshed every 20 min, has been read again and shows no full window. Without this a marker written once for a window that
  has since reset outlives the limit it records: every codex model stays skipped until its timestamp passes (#1525)."""
import json, os, pathlib, sys, time
hub = pathlib.Path(os.environ.get("SG_SHARED") or os.environ.get("SG_HUB_DIR") or pathlib.Path(os.environ.get("SG_CHECKOUT") or os.getcwd()) / "runs" / ".hub")   # the shared quota cache, one per machine
try: q = json.loads((hub / "hub-quota.json").read_text()).get("gpt") or {}
except Exception: q = {}
now = time.time()
full = [w["resets"] for w in q.get("windows", []) if (w.get("pct") or 0) >= 100 and (w.get("resets") or 0) > now]
def cache_mtime():
    try: return (hub / "hub-quota.json").stat().st_mtime
    except Exception: return None

if "--sync" in sys.argv:
    marker = pathlib.Path(sys.argv[sys.argv.index("--sync") + 1])
    if full:
        marker.write_text(str(int(max(full))))
    else:
        # the cache is the source of truth and is repolled every 20 min: a reading taken after the marker was written, with
        # no full window, means the limit it recorded has reset. A marker younger than the reading is left alone, so the
        # --hit fallback still holds a window that the cache has not caught up with.
        m = cache_mtime()
        if marker.exists() and m is not None and marker.stat().st_mtime < m: marker.unlink()
    sys.exit(0)
if full: print(int(max(full)))
elif "--hit" in sys.argv: print(int(now + 3600))
