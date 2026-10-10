"""The adaptation report: a read-only view for a person.

It joins each applied proposal to its target's previous and next entry and shows the deltas.
It has no label and no threshold. No engine code path imports this module, and it is never a fact.
"""
from squad import record

NOTE = "one observation, not a measurement: each entry is a fresh session, so the two entries may be different tasks"
INSTRUMENT = "code: outcome events of the run record; spend is the measured SDK result; elapsed is wall-clock"
FIELDS = ("outcome", "spend", "turns", "elapsed")


def _entry(e):
    return None if e is None else {"entry": e["entry"], "pos": e["pos"], "reasons": e["reasons"], **{k: e.get(k) for k in FIELDS}}


def _delta(prev, nxt):
    if prev is None or nxt is None:
        return {k: None for k in FIELDS}
    out = {"outcome": f"{prev['outcome']} -> {nxt['outcome']}"}
    for k in ("spend", "turns", "elapsed"):
        a, b = prev[k], nxt[k]
        out[k] = None if a is None or b is None else round(b - a, 6)
    return out


def build(events):
    """Return one row per applied proposal, in record order."""
    outs = [e for e in events if e["event"] == "outcome"]
    rows = []
    for a in (e for e in events if e["event"] == "applied"):
        mine = [o for o in outs if o["node"] == a["target"]]
        prev = next((o for o in reversed(mine) if o["pos"] < a["pos"]), None)
        nxt = next((o for o in mine if o["pos"] > a["pos"]), None)
        p, n = _entry(prev), _entry(nxt)
        rows.append({"proposal_id": a["proposal_id"], "kind": a["kind"], "target": a["target"], "old": a["old"],
                     "requested": a["requested"], "range": a["range"], "facts": a["facts"], "proposer": a["proposer"],
                     "previous": p, "next": n, "delta": _delta(p, n), "instrument": INSTRUMENT, "note": NOTE})
    return rows


def render(rows):
    """Plain text for a person. It states each delta and never calls an adaptation good or bad."""
    if not rows:
        return "no applied proposals in this run\n"
    lines = []
    for r in rows:
        lines.append(f"{r['proposal_id']}: {r['kind']} on {r['target']}: {r['old']} -> {r['requested']}")
        for label, e in (("previous", r["previous"]), ("next", r["next"])):
            lines.append(f"  {label} entry: " + ("none" if e is None else f"#{e['entry']} {e['outcome']}, spend {e['spend']}, turns {e['turns']}, elapsed {e['elapsed']}"
                                                  + (f", reasons {e['reasons']}" if e["reasons"] else "")))
        lines.append("  delta: " + ", ".join(f"{k} {v if v is not None else 'unavailable'}" for k, v in r["delta"].items()))
        lines.append(f"  instrument: {r['instrument']}")
        lines.append(f"  {r['note']}")
    return "\n".join(lines) + "\n"


def from_file(path):
    return build(record.read(path))
