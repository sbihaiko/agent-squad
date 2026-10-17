#!/usr/bin/env python3
# modelreg: seam-clean
"""Which models of a chain may run now, why one may not, and when it is back (#431, SPEC 505).

A thin wrapper over `modelreg.chain` and `modelreg.eligible` for coding, review, vision and router: the resolver owns
the switch, the window, a subscription marker (`codex-limited`, `agy-limited`, `claude-limited`, `grok-limited`) and
the Claude reserve. The hub draws what `chain_state` returns and never computes the rule (ADR-0137);
branchreviewpass.sh prints `summary` into the issue's status file.
usage: chainhealth.py <models.json> <shared dir> [now epoch]   prints the banner, else the notice, else nothing.
A caller that cannot load the resolver beside this file is a named failure, never a silent skip."""
import json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import provkey   # a model whose provider needs an absent key (#482)

_modelreg = None

def _resolver():
    """The resolver beside this file. Loaded on first use so a hub that never reads a chain needs it not."""
    global _modelreg
    if _modelreg is None:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        try:
            import modelreg as mod
        except ImportError as exc:
            raise RuntimeError(f"chainhealth: modelreg.py is missing or unreadable beside chainhealth.py ({exc})") from exc
        _modelreg = mod
    return _modelreg

def _bare(m):
    """The name the hub and the status file show for a raw spelling: the route prefix is not part of it."""
    scheme, sep, rest = m.partition(":")
    return rest if sep and scheme == "claude" else m

def _clock(t): return time.strftime("%H:%M", time.localtime(t))

def chain_state(cfg, key="review_models", shared=".", now=None, hour=None):
    regmod = _resolver()
    name = key[:-len("_models")] if key.endswith("_models") else key
    entries = regmod.chain(cfg, name).entries
    out = []
    for e in entries:
        el = regmod.eligible(e, shared=shared, now=now, hour=hour)
        if provkey.closed(e.raw) and (el.ok or not str(el.reason).startswith("usage limit")):   # an absent provider key (#482)
            out.append({"model": _bare(e.raw), "reason": provkey.reason(e.raw), "until": None, "until_text": None})
        elif not el.ok:
            reason = "switched off" if el.reason == "off" else el.reason
            out.append({"model": _bare(e.raw), "reason": reason, "until": el.until,
                        "until_text": _clock(el.until) if el.until else None})
    left = [_bare(e.raw) for e in entries if _bare(e.raw) not in {o["model"] for o in out}]
    n = len(entries)
    detail = "; ".join(f"{o['model']}: {o['reason']}" + (f", resets {o['until_text']}" if o["until_text"] else "") for o in out)
    banner = None
    if n == 0: banner = f"{name} chain: no model available of 0"
    elif n > 1 and len(left) == 1: banner = f"{name} chain: only {left[0]} of {n} models available ({detail})"
    elif n > 1 and not left: banner = f"{name} chain: no model available of {n} ({detail})"
    return {"key": key, "total": n, "eligible": len(left), "available": left, "out": out, "banner": banner,
            "notice": f"out of the chain: {detail}" if out else None}

if __name__ == "__main__":
    try:
        cfg = json.loads(pathlib.Path(sys.argv[1]).read_text())
        s = chain_state(cfg, "review_models", sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
    except (RuntimeError, OSError, ValueError, IndexError) as exc:
        msg = str(exc)
        print(msg if msg.startswith("chainhealth: ") else f"chainhealth: {msg}", file=sys.stderr)
        raise SystemExit(2) from None
    print(s["banner"] or s["notice"] or "")
