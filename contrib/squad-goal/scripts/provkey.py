#!/usr/bin/env python3
"""A model whose provider needs a key is closed while the key is absent (#482).

Qwen 3.8 Flash runs through the bridge's OpenCode Go backend, which answers `authentication_error` with no key. With no key
the model is never offered, never routed and never chosen: launch.py and mkreq.sh close it like a spent subscription, the
hub drops it from the add boxes, and chainhealth names the reason. Presence only: the value is never read into an output.
The key lives in `opencode.apiKey` of the bridge config (SG_CCP_CONFIG overrides the path) or in CCP_OPENCODE_API_KEY.
usage: provkey.py <model id>   exit 0 when the model is closed for a missing key, 1 when it may run. Prints nothing."""
import json, os, pathlib, sys

KEYED = (("qwen", "opencode"),)   # bare-id prefix -> provider whose key the bridge needs
REASON = {"opencode": "no OpenCode Go key"}
ENV = {"opencode": "CCP_OPENCODE_API_KEY"}


def _config():
    p = os.environ.get("SG_CCP_CONFIG") or str(pathlib.Path.home() / ".config/claude-code-proxy/config.json")
    try: d = json.loads(pathlib.Path(p).read_text())
    except (OSError, ValueError): return {}
    return d if isinstance(d, dict) else {}


def provider(mid):
    """The provider whose key the model needs, or None. `claude:` and `claude-` spell the same model."""
    b = mid.removeprefix("claude:").removeprefix("claude-").lower()
    return next((p for pre, p in KEYED if b.startswith(pre)), None)


def has_key(prov):
    if os.environ.get(ENV[prov], "").strip(): return True
    row = _config().get(prov)
    return isinstance(row, dict) and isinstance(row.get("apiKey"), str) and bool(row["apiKey"].strip())


def closed(mid):
    """True when the model needs a key that is absent."""
    p = provider(mid)
    return bool(p) and not has_key(p)


def reason(mid):
    """Why the model is closed, or None when it is not."""
    return REASON[provider(mid)] if closed(mid) else None


if __name__ == "__main__":
    sys.exit(0 if len(sys.argv) > 1 and closed(sys.argv[1]) else 1)
