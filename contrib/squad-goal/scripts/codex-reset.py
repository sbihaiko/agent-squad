#!/usr/bin/env python3
"""Print the epoch when the Codex (ChatGPT) quota frees up, read from the Squad hub's quota cache (runs/.hub/hub-quota.json,
refreshed every 20 min from `codex app-server`). Prints nothing when no window is full, so a caller keeps its own fallback.
usage: codex-reset.py [--hit]   --hit: a usage-limit error was just seen, so a stale cache that shows no full window falls back to now+1h."""
import json, os, pathlib, sys, time
hub = pathlib.Path(os.environ.get("SG_HUB_DIR") or pathlib.Path(os.environ.get("SG_CHECKOUT") or os.getcwd()) / "runs" / ".hub")
try: q = json.loads((hub / "hub-quota.json").read_text()).get("gpt") or {}
except Exception: q = {}
now = time.time()
full = [w["resets"] for w in q.get("windows", []) if (w.get("pct") or 0) >= 100 and (w.get("resets") or 0) > now]
if full: print(int(max(full)))
elif "--hit" in sys.argv: print(int(now + 3600))
