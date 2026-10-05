#!/usr/bin/env python3
"""Analyze the real-usage Jev probes. Writes aggregate results (no raw text) to scripts/archive/jev_results_real/.

Run: python3 scripts/archive/jev_real/analyze.py
FIT: earliest 70% by time sets a threshold. TEST: latest 30% only reports (ADR-0012).
"""
import json, math, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA = ROOT / "scripts" / "archive" / "jev_data_real"
OUT = ROOT / "scripts" / "archive" / "jev_results_real"
rng = random.Random(0)
THS = [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def boot(vals, fn, n=2000):
    if not vals:
        return (None, None)
    out = []
    for _ in range(n):
        s = [vals[rng.randrange(len(vals))] for _ in vals]
        out.append(fn(s))
    out.sort()
    return (round(out[int(0.025 * n)], 3), round(out[int(0.975 * n) - 1], 3))


def prec_cov(rows, t):
    cov = [r for r in rows if r["conf"] >= t]
    hit = sum(r["ok"] for r in cov)
    return {"t": t, "covered": len(cov), "coverage": round(len(cov) / len(rows), 3) if rows else None,
            "precision": round(hit / len(cov), 3) if cov else None, "precision_ci": wilson(hit, len(cov))}


def ece(rows, bins=5):
    if not rows:
        return None
    tot = 0.0
    tab = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        g = [r for r in rows if lo <= r["conf"] < hi or (b == bins - 1 and r["conf"] == 1.0)]
        if g:
            acc = sum(r["ok"] for r in g) / len(g)
            mc = sum(r["conf"] for r in g) / len(g)
            tot += len(g) / len(rows) * abs(acc - mc)
            tab.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(g), "mean_conf": round(mc, 3), "accuracy": round(acc, 3)})
    return round(tot, 3), tab


def smallest_t(fit, target, min_cov=10):
    for t in [x / 100 for x in range(0, 100)]:
        c = [r for r in fit if r["conf"] >= t]
        if len(c) >= min_cov and sum(r["ok"] for r in c) / len(c) >= target:
            return t
    return None


def skills(minlen=0):
    d = json.loads((DATA / "results_skills.json").read_text())
    lens = {q["id"]: len(q["text"]) for q in json.loads((DATA / "requests.json").read_text())}
    rows = []
    for r in d["rows"]:
        if "error" in r["jev"] or "error" in r["base"]:
            continue
        if lens.get(r["id"], 0) < minlen:
            continue
        rows.append({"id": r["id"], "slice": r["slice"], "label": r["label"], "ts": r["ts"], "conf": r["jev"]["conf"], "pick": r["jev"]["pick"],
                     "ok": r["jev"]["pick"] == r["label"], "base_ok": r["base"]["pick"] == r["label"], "base_pick": r["base"]["pick"]})
    res = {"n_rows": len(rows), "dropped_not_in_roster": d["dropped_not_in_roster"], "errors": len(d["rows"]) - len(rows)}
    labeled = sorted([r for r in rows if r["slice"] in ("S1", "S2")], key=lambda r: r["ts"])
    none = [r for r in rows if r["slice"] == "S0"]
    res["counts"] = {s: sum(r["slice"] == s for r in rows) for s in ("S0", "S1", "S2")}
    for name, pool in (("S1", [r for r in labeled if r["slice"] == "S1"]), ("S2", [r for r in labeled if r["slice"] == "S2"]), ("S1+S2", labeled)):
        n = len(pool)
        j, b = sum(r["ok"] for r in pool), sum(r["base_ok"] for r in pool)
        diffs = [int(r["ok"]) - int(r["base_ok"]) for r in pool]
        res[name] = {"n": n, "jev_top1": round(j / n, 3) if n else None, "jev_ci": wilson(j, n), "base_top1": round(b / n, 3) if n else None, "base_ci": wilson(b, n),
                     "paired_diff_jev_minus_base": round(sum(diffs) / n, 3) if n else None, "paired_diff_ci": boot(diffs, lambda s: sum(s) / len(s)),
                     "curve": [prec_cov(pool, t) for t in THS], "ece": ece(pool)}
    # fit / test on S1+S2 by time
    cut = int(len(labeled) * 0.7)
    fit, test = labeled[:cut], labeled[cut:]
    res["fit_test"] = {"fit_n": len(fit), "test_n": len(test), "fit_span": [fit[0]["ts"][:10], fit[-1]["ts"][:10]] if fit else None,
                       "test_span": [test[0]["ts"][:10], test[-1]["ts"][:10]] if test else None}
    for target in (0.95, 0.90):
        t = smallest_t(fit, target)
        entry = {"threshold_on_fit": t}
        if t is not None:
            entry["fit"] = prec_cov(fit, t)
            entry["test"] = prec_cov(test, t)
        res["fit_test"][f"target_{int(target*100)}"] = entry
    res["fit_test"]["test_top1"] = {"jev": round(sum(r["ok"] for r in test) / len(test), 3), "base": round(sum(r["base_ok"] for r in test) / len(test), 3)}
    # S0: requests where no skill ran. A non-none pick at high confidence is a false pick (weak label).
    res["S0_false_pick"] = [{"t": t, "picked_skill": sum(1 for r in none if r["pick"] != "none" and r["conf"] >= t), "n": len(none)} for t in THS[1:]]
    res["S0_base_non_none"] = sum(1 for r in none if r["base_pick"] not in ("none", ""))
    res["S0_jev_non_none_any_conf"] = sum(1 for r in none if r["pick"] != "none")
    # where Jev and baseline disagree with the label
    per = {}
    for r in labeled:
        g = per.setdefault(r["label"], {"n": 0, "jev": 0, "base": 0})
        g["n"] += 1; g["jev"] += r["ok"]; g["base"] += r["base_ok"]
    res["per_label"] = per
    return res


def screen():
    d = json.loads((DATA / "results_screen.json").read_text())
    rows = [r for r in d["rows"] if "error" not in r["jev"] and "error" not in r["base"]]
    out = {"n": len(rows), "errors": len(d["rows"]) - len(rows), "by_tool": {}}
    for tool in ("WebFetch", "WebSearch"):
        g = [r for r in rows if r["tool"] == tool]
        out["by_tool"][tool] = len(g)
    out["flag_rate"] = [{"t": t, "jev_flagged": sum(r["jev"]["p"] >= t for r in rows), "rate": round(sum(r["jev"]["p"] >= t for r in rows) / len(rows), 3)} for t in (0.1, 0.3, 0.5, 0.7, 0.9)]
    out["base_flagged"] = sum(r["base"]["yes"] for r in rows)
    out["agree_at_0.5"] = sum((r["jev"]["p"] >= 0.5) == r["base"]["yes"] for r in rows)
    out["jev_only_at_0.5"] = [r["id"] for r in rows if r["jev"]["p"] >= 0.5 and not r["base"]["yes"]]
    out["base_only_at_0.5"] = [r["id"] for r in rows if r["jev"]["p"] < 0.5 and r["base"]["yes"]]
    out["both_at_0.5"] = [r["id"] for r in rows if r["jev"]["p"] >= 0.5 and r["base"]["yes"]]
    out["jev_p_by_id_ge_0.3"] = {r["id"]: round(r["jev"]["p"], 3) for r in rows if r["jev"]["p"] >= 0.3}
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    sk = skills()
    (OUT / "skills.json").write_text(json.dumps(sk, indent=1))
    sk80 = skills(80)  # declared variant: informative requests only (80 characters or more)
    (OUT / "skills_min80.json").write_text(json.dumps(sk80, indent=1))
    for name, x in (("ALL", sk), ("MIN80", sk80)):
        print(name, x["counts"], {k: (x[k]["n"], x[k]["jev_top1"], x[k]["base_top1"], x[k]["paired_diff_ci"]) for k in ("S1", "S2", "S1+S2")})
        print("  fit/test", json.dumps(x["fit_test"]))
        print("  ece S1+S2", x["S1+S2"]["ece"][0], [ (c["t"], c["covered"], c["precision"]) for c in x["S1+S2"]["curve"]])
        print("  S0 false pick", [(c["t"], c["picked_skill"]) for c in x["S0_false_pick"]], "of", x["S0_false_pick"][0]["n"])
    if (DATA / "results_screen.json").exists():
        sc = screen()
        (OUT / "screen.json").write_text(json.dumps(sc, indent=1))
        print(json.dumps(sc, indent=1)[:3000])
