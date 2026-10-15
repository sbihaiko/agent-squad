#!/usr/bin/env python3
"""Which models of a chain may run now, why one may not, and when it is back (#431).

One reader of what review.sh already skips a model over: `codex-limited` (a usage limit; every model that is not claude:,
grok- or gemini-) and `agy-limited` (gemini-), each an epoch under the shared dir, `quota-guard` with `anthropic_models`,
and models.json's `off` and `hours`. The hub draws what `chain_state` returns and never computes the rule (ADR-0137);
branchreviewpass.sh prints `summary` into the issue's status file.
usage: chainhealth.py <models.json> <shared dir> [now epoch]   prints the banner, else the notice, else nothing."""
import json, pathlib, sys, time

def _bare(m): return m[len("claude:"):] if m.startswith("claude:") else m

def _marker(shared, name, now):
    try: t = float((pathlib.Path(shared) / name).read_text().strip())
    except (OSError, ValueError): return None
    return t if t > now else None

def _in_window(w, h):
    try: a, b = int(w[:2]), int(w[3:5])
    except ValueError: return True
    return a <= h < b if a <= b else (h >= a or h < b)

def _clock(t): return time.strftime("%H:%M", time.localtime(t))

def chain_state(cfg, key="review_models", shared=".", now=None, hour=None):
    now = time.time() if now is None else now
    hour = time.localtime(now).tm_hour if hour is None else hour
    chain = list(cfg.get(key) or [])
    codex, agy = _marker(shared, "codex-limited", now), _marker(shared, "agy-limited", now)
    guard = (pathlib.Path(shared) / "quota-guard").exists()
    out = []
    for m in chain:
        why, until = None, None
        if not m.startswith(("claude:", "grok-", "gemini-")) and codex: why, until = "usage limit", codex
        elif m.startswith("gemini-") and agy: why, until = "usage limit", agy
        elif guard and m in (cfg.get("anthropic_models") or []): why = "weekly quota guard"
        elif ((cfg.get("off") or {}).get(key) or {}).get(m): why = "switched off"
        else:
            w = ((cfg.get("hours") or {}).get(key) or {}).get(m)
            if w and not _in_window(w, hour): why = f"outside its window {w}"
        if why: out.append({"model": _bare(m), "reason": why, "until": int(until) if until else None,
                            "until_text": _clock(until) if until else None})
    left = [_bare(m) for m in chain if _bare(m) not in {o["model"] for o in out}]
    n = len(chain)
    detail = "; ".join(f"{o['model']}: {o['reason']}" + (f", resets {o['until_text']}" if o["until_text"] else "") for o in out)
    banner = None
    if n > 1 and len(left) == 1: banner = f"review chain: only {left[0]} of {n} models available ({detail})"
    elif n > 1 and not left: banner = f"review chain: no model available of {n} ({detail})"
    return {"key": key, "total": n, "eligible": len(left), "available": left, "out": out, "banner": banner,
            "notice": f"out of the chain: {detail}" if out else None}

if __name__ == "__main__":
    cfg = json.loads(pathlib.Path(sys.argv[1]).read_text())
    s = chain_state(cfg, "review_models", sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(s["banner"] or s["notice"] or "")
