#!/usr/bin/env python3
# modelreg: seam-clean
"""Which models of a chain may run now, why one may not, and when it is back (#431).

One reader of what review.sh already skips a model over: `codex-limited` (a usage limit; every model that is not claude:,
grok- or gemini-) and `agy-limited` (gemini-), `grok-limited` (grok-; fresh for SG_GROK_LIMIT_TTL, see grokmark.py), each an epoch under the shared dir, `quota-guard` with `anthropic_models`,
models.json's `off` and `hours`, and a provider key that is absent (provkey.py). The hub draws what `chain_state` returns and never computes the rule (ADR-0137);
branchreviewpass.sh prints `summary` into the issue's status file.

A v1 file keeps these rules: the registry landed beside this reader, and this reader did not move with it (ADR-0147).
The `divergence` field reports where a v1 file and the registry disagree about the same spelling, so a later move of
this caller to the resolver is read, not guessed. A v2 file (`chains`) has no earlier behavior and is read through the
registry.

usage: chainhealth.py <models.json> <shared dir> [now epoch] [--chain NAME] [--all]   prints the banner, else the
notice, else nothing.
"""
import argparse, json, pathlib, sys, time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path: sys.path.insert(0, str(_HERE))
import grokmark   # the grok-limited marker rule, shared with review.sh (#1417)
import provkey   # a model whose provider needs an absent key (#482)

_modelreg = None

CHAINS = ("coding", "review", "vision", "router")


def _resolver():
    """The resolver beside this file. Loaded on first use so a hub that never reads a divergence needs it not."""
    global _modelreg
    if _modelreg is None:
        try:
            import modelreg as mod
        except ImportError as exc:
            raise RuntimeError(f"chainhealth: modelreg.py is missing or unreadable beside chainhealth.py ({exc})") from exc
        _modelreg = mod
    return _modelreg


def _bare(m):
    """The spelling without its `claude:` route prefix: the name the status file shows (main's v1 rule, unchanged)."""
    return m[len("claude:"):] if isinstance(m, str) and m.partition(":")[0] == "claude" else m

def _display(e):
    """The name the hub and the status file show for a raw spelling: the route prefix is not part of it."""
    return e.raw.split(":", 1)[-1]

def _marker(shared, name, now):
    try: t = float((pathlib.Path(shared) / name).read_text().strip())
    except (OSError, ValueError): return None
    return t if t > now else None

def _is_grok(raw):
    return _bare(raw).startswith(("grok-", "claude-grok-"))


def _grok_why(raw, shared, now):
    """The grok-limited marker as one entry's state: (reason, info) or None with no marker (#1417).

    limited: the marker is fresh; eligible: expired and no probe holds the lock; probing: expired and a probe holds it.
    The reason names the next probe, never a balance reset."""
    st = grokmark.probe_state(shared, now)
    if st is None: return None
    written = grokmark.written(shared) or 0
    nxt = int(written + grokmark.ttl())
    info = {"state": st, "age": int(max(0, now - written)), "next_probe": nxt}
    if st == "limited":
        return f"limited: balance spent, marker {info['age'] // 60} min old, next probe {_clock(nxt)}", info
    if st == "probing":
        return "probing: one review is trying grok", info
    return None, info


def _grok_states(raws, shared, now):
    """Per grok entry with a marker, its state, marker age and next probe: the hub draws it as is (#1417)."""
    out = {}
    for raw in raws:
        g = _grok_why(raw, shared, now) if _is_grok(raw) else None
        if g: out[_bare(raw)] = g[1]
    return out


def _in_window(w, now, hour):
    """The resolver's one window grammar (ADR-0147), read at `now`; a named `hour` moves `now` to it on the same local day."""
    lt = time.localtime(now)
    return _resolver().in_window(w, now if hour is None else time.mktime((*lt[:3], hour, *lt[4:8], -1)))

def _clock(t): return time.strftime("%H:%M", time.localtime(t))


def _timeout_why(shared, raw, now):
    """The timeout streak that took this reviewer out of the chains, read from the file its writer owns (ADR-0161).

    The panel and the loop share one source of state: an omission is a state of the entry, so it shows on the entry
    and never removes it, and a streak whose window has ended is no reason at all."""
    try:
        d = json.loads((pathlib.Path(shared) / "review-timeouts.json").read_text())
    except (OSError, ValueError):
        return None, None
    e = d.get(raw)
    if e is None:   # the panel may carry the chain spelling and the writer the bare id: one model, one state
        bare = raw.split(":", 1)[-1]
        e = next((v for k, v in d.items() if k.split(":", 1)[-1] == bare), None)
    e = e or {}
    until = e.get("until")
    if until is None or now >= float(until):
        return None, None
    return f"{e.get('streak', 0)} consecutive timeouts", float(until)

def _v1_why(cfg, key, m, shared, now, hour):
    """The v1 rules, in the order main read them. Return (reason, until), or (None, None) when the model may run."""
    codex, agy = _marker(shared, "codex-limited", now), _marker(shared, "agy-limited", now)
    guard = (pathlib.Path(shared) / "quota-guard").exists()
    reg = _resolver()   # the two marker rules have one home, the resolver (ADR-0147 rule 10); their behavior is main's
    if reg.v1_codex(m) and codex: return "usage limit", codex
    if reg.v1_agy(m) and agy: return "usage limit", agy
    if _is_grok(m):
        g = _grok_why(m, shared, now)
        if g and g[0]: return g[0], None
    if provkey.closed(m): return provkey.reason(m), None
    if guard and m in (cfg.get("anthropic_models") or []): return "weekly quota guard", None
    if ((cfg.get("off") or {}).get(key) or {}).get(m): return "switched off", None
    w = ((cfg.get("hours") or {}).get(key) or {}).get(m)
    if w and not _in_window(w, now, hour): return f"outside its window {w}", None
    tw, tu = _timeout_why(shared, m, now)
    if tw: return tw, tu
    return None, None


def _registry_why(cfg, name, raw, shared, now, hour):
    """The registry's view of one spelling, with the rules the registry does not carry applied the same way.

    `quota-guard` and `anthropic_models` stay here (the registry has no field for them), so they never show as a
    divergence. Return None when the registry would let the spelling run.
    """
    regmod = _resolver()
    e = next((x for x in regmod.chain(cfg, name).entries if x.raw == raw), None)
    if e is None: return "not in the registry"
    g = _grok_why(raw, shared, now) if _is_grok(raw) else None
    if g and g[0]: return g[0]
    ev = regmod.eligible(e, shared=shared, now=now, hour=hour)
    if not ev.ok: return ev.reason
    if provkey.closed(raw): return provkey.reason(raw)
    if (pathlib.Path(shared) / "quota-guard").exists() and raw in (cfg.get("anthropic_models") or []):
        return "weekly quota guard"
    return None


def _divergence(cfg, key, name, shared, now, hour):
    """One row per spelling where the v1 rules and the registry disagree about eligibility."""
    rows = []
    for m in cfg.get(key) or []:
        v1, _ = _v1_why(cfg, key, m, shared, now, hour)
        reg = _registry_why(cfg, name, m, shared, now, hour)
        if (v1 is None) != (reg is None):
            rows.append({"model": _bare(m), "v1": v1 or "available", "registry": reg or "available"})
    return rows


def _registry_state(doc, name, shared, now, hour):
    """A v2 chain, read through the registry: switch, window, subscription marker and Claude reserve."""
    regmod = _resolver()
    entries = regmod.chain(doc, name).entries
    guard = (pathlib.Path(shared) / "quota-guard").exists()
    anthropic = doc.get("anthropic_models") or []
    out = []
    for e in entries:
        why, until = None, None
        ev = regmod.eligible(e, shared=shared, now=now, hour=hour)
        g = _grok_why(e.raw, shared, now) if _is_grok(e.raw) else None
        if g and g[0]: why = g[0]
        elif provkey.closed(e.raw) and (ev.ok or not str(ev.reason).startswith("usage limit")):
            why = provkey.reason(e.raw)
        elif guard and e.raw in anthropic: why = "weekly quota guard"
        elif not ev.ok: why, until = ev.reason, ev.until
        if not why:
            why, until = _timeout_why(shared, e.raw, now)
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
            "notice": f"out of the chain: {detail}" if out else None, "divergence": [], "divergence_notice": None,
            "grok": _grok_states([e.raw for e in entries], shared, now)}


def chain_state(cfg, key="review_models", shared=".", now=None, hour=None):
    """One chain, keyed as the caller names it (`review_models` or `review`). A v1 file keeps the v1 rules."""
    doc = cfg if isinstance(cfg, dict) else json.loads(pathlib.Path(cfg).read_text())
    name = key.removesuffix("_models")
    now = time.time() if now is None else now
    if "chains" in doc:
        return _registry_state(doc, name, shared, now, hour) | {"key": key}
    chain = list(doc.get(key) or [])
    out = []
    for m in chain:
        why, until = _v1_why(doc, key, m, shared, now, hour)
        if why: out.append({"model": _bare(m), "reason": why, "until": int(until) if until else None,
                            "until_text": _clock(until) if until else None})
    left = [_bare(m) for m in chain if _bare(m) not in {o["model"] for o in out}]
    n = len(chain)
    detail = "; ".join(f"{o['model']}: {o['reason']}" + (f", resets {o['until_text']}" if o["until_text"] else "") for o in out)
    banner = None
    if n > 1 and len(left) == 1: banner = f"review chain: only {left[0]} of {n} models available ({detail})"
    elif n > 1 and not left: banner = f"review chain: no model available of {n} ({detail})"
    rows = _divergence(doc, key, name, shared, now, hour)
    return {"key": key, "total": n, "eligible": len(left), "available": left, "out": out, "banner": banner,
            "notice": f"out of the chain: {detail}" if out else None,
            "divergence": rows, "grok": _grok_states(chain, shared, now),
            "divergence_notice": ("registry and v1 differ: " + "; ".join(
                f"{r['model']}: v1 {r['v1']}, registry {r['registry']}" for r in rows)) if rows else None}


def chains_state(cfg, shared=".", now=None, hour=None):
    """Every chain, from the same reader. A v1 file names its chains by the `*_models` key; a v2 file by `chains`."""
    doc = cfg if isinstance(cfg, dict) else json.loads(pathlib.Path(cfg).read_text())
    v2 = "chains" in doc
    return {name: chain_state(doc, name if v2 else f"{name}_models", shared, now, hour) for name in CHAINS}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="chainhealth", description="Which models of a chain may run now.")
    ap.add_argument("models"); ap.add_argument("shared"); ap.add_argument("now", nargs="?", type=float)
    ap.add_argument("--chain", default="review", choices=CHAINS)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    try:
        doc = json.loads(pathlib.Path(a.models).read_text())
        states = chains_state(doc, a.shared, a.now).values() if a.all else [chain_state(doc, a.chain + "_models", a.shared, a.now)]
        for s in states:
            print(s["banner"] or s["notice"] or "")
            if s.get("divergence_notice"): print(s["divergence_notice"])
    except (RuntimeError, OSError, ValueError) as exc:
        msg = str(exc)
        print(msg if msg.startswith("chainhealth: ") else f"chainhealth: {msg}", file=sys.stderr)
        raise SystemExit(2) from None
