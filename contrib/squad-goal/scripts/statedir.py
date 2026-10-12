"""The Python half of the state-directory seam (#20); env.sh and repoctx.sh are the shell half.

One state directory per repository: SG_STATE, else <base>/<SG_PROFILE>. What stays machine-wide (the provider quota
markers codex-limited, agy-limited and quota-guard, and the board cache keyed by (owner, project)) lives in one shared
directory: SG_SHARED, else <base>/_shared. <base> is SG_STATE_BASE, else the per-user cache directory. No other script
names that directory."""
import os, pathlib

def base(): return pathlib.Path(os.environ.get("SG_STATE_BASE") or pathlib.Path.home() / ".cache/squad-goal")
def state():
    if os.environ.get("SG_STATE"): return pathlib.Path(os.environ["SG_STATE"])
    return base() / os.environ["SG_PROFILE"] if os.environ.get("SG_PROFILE") else base()
def shared(): return pathlib.Path(os.environ.get("SG_SHARED") or base() / "_shared")
