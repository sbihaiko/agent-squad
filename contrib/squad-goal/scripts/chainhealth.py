#!/usr/bin/env python3
# modelreg: seam-clean
"""Which models of a chain may run now, why one may not, and when it is back (#431, SPEC 505).

One reader over every chain (coding, review, vision, router). The chains and the eligibility rule come from
modelreg: its subscription marker (`codex-limited`, `agy-limited`, `claude-limited`, `grok-limited`), models.json's
`off` and `hours`, and the Claude reserve. `quota-guard` with `anthropic_models` is the one rule the registry does
not carry yet, so it stays here. The hub draws what `chain_state` returns and never computes the rule (ADR-0137);
branchreviewpass.sh prints the same summary into the issue's status file.
usage: chainhealth.py <models.json> <shared dir> [now epoch] [--chain NAME] [--all]   prints the banner, else the
notice, else nothing. A caller that cannot load the resolver beside this file is a named failure, never a silent skip."""
import argparse, json, pathlib, sys, time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path: sys.path.insert(0, str(_HERE))
import provkey   # a model whose provider needs an absent key (#482)
_modelreg = None


def _resolver():
    """The resolver beside this file. Loaded on first use so a hub that never reads a chain needs it not."""
    global _modelreg
    if _modelreg is None:
        if str(_HERE) not in sys.path: sys.path.insert(0, str(_HERE))
        try:
            import modelreg as mod
        except ImportError as exc:
            raise RuntimeError(f"chainhealth: modelreg.py is missing or unreadable beside chainhealth.py ({exc})") from exc
        _modelreg = mod
    return _modelreg


CHAINS = ("coding", "review", "vision", "router")
_REASON = {"off": "switched off"}


def _display(e):
    """The name the hub and the status file show for a raw spelling: the route prefix is not part of it."""
    return e.raw.split(":", 1)[-1]

def _clock(t): return time.strftime("%H:%M", time.localtime(t))

def _state(doc, name, shared=".", now=None, hour=None):
    regmod = _resolver()
    now = time.time() if now is None else now
    hour = time.localtime(now).tm_hour if hour is None else hour
    entries = regmod.chain(doc, name).entries
    guard = (pathlib.Path(shared) / "quota-guard").exists()
    anthropic = doc.get("anthropic_models") or []
    out = []
    for e in entries:
        why, until = None, None
        ev = regmod.eligible(e, shared=shared, now=now, hour=hour)
        if provkey.closed(e.raw) and (ev.ok or not str(ev.reason).startswith("usage limit")):   # an absent provider key (#482)
            why = provkey.reason(e.raw)
        elif guard and e.raw in anthropic: why = "weekly quota guard"
        elif not ev.ok: why, until = _REASON.get(ev.reason, ev.reason), ev.until
        if why: out.append({"model": _display(e), "reason": why, "until": int(until) if until else None,
                            "until_text": _clock(until) if until else None})
    left = [_display(e) for e in entries if _display(e) not in {o["model"] for o in out}]
    n = len(entries)
    detail = "; ".join(f"{o['model']}: {o['reason']}" + (f", resets {o['until_text']}" if o["until_text"] else "") for o in out)
    banner = None
    if n == 0: banner = f"{name} chain: no model configured"
    elif n > 1 and len(left) == 1: banner = f"{name} chain: only {left[0]} of {n} models available ({detail})"
    elif n > 1 and not left: banner = f"{name} chain: no model available of {n} ({detail})"
    return {"key": name, "total": n, "eligible": len(left), "available": left, "out": out, "banner": banner,
            "notice": f"out of the chain: {detail}" if out else None}

def chains_state(cfg, shared=".", now=None, hour=None):
    """Every chain, from the same reader. A v1 file names its chains by the `*_models` key; a v2 file by `chains`."""
    doc = cfg if isinstance(cfg, dict) else json.loads(pathlib.Path(cfg).read_text())
    return {name: _state(doc, name, shared, now, hour) for name in CHAINS}

def chain_state(cfg, key="review_models", shared=".", now=None, hour=None):
    """One chain, keyed as the caller names it (`review_models` or `review`)."""
    doc = cfg if isinstance(cfg, dict) else json.loads(pathlib.Path(cfg).read_text())
    s = _state(doc, key.removesuffix("_models"), shared, now, hour)
    return s | {"key": key}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="chainhealth", description="Which models of a chain may run now.")
    ap.add_argument("models"); ap.add_argument("shared"); ap.add_argument("now", nargs="?", type=float)
    ap.add_argument("--chain", default="review", choices=CHAINS)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    try:
        doc = json.loads(pathlib.Path(a.models).read_text())
        if a.all:
            for s in chains_state(doc, a.shared, a.now).values():
                print(s["banner"] or s["notice"] or "")
        else:
            s = chain_state(doc, a.chain + "_models", a.shared, a.now)
            print(s["banner"] or s["notice"] or "")
    except (RuntimeError, OSError, ValueError) as exc:
        msg = str(exc)
        print(msg if msg.startswith("chainhealth: ") else f"chainhealth: {msg}", file=sys.stderr)
        raise SystemExit(2) from None
