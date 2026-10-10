#!/usr/bin/env python3
"""Skill-choice probe on real requests. Reuses ask() and the pinned model of scripts/archive/probe_jev.py.

Run: python3 scripts/archive/jev_real/probe_skills.py
Input: scripts/archive/jev_data_real/requests.json (raw text, git ignores it). Output: scripts/archive/jev_data_real/results_skills.json.
Jev calls carry a 60 s total deadline incl. retries. A credit or auth error stops the run.
"""
import json
import subprocess
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import probe_jev as pj  # noqa: E402

DATA = pj.ROOT / "scripts" / "archive" / "jev_data_real"
BASE_MODEL = "claude-sonnet-5-5"
STOP = {"flag": None}


def base_call(prompt):
    out = subprocess.run(["claude", "-p", "--model", BASE_MODEL, "--effort", "medium", "--tools", "", "--no-session-persistence",
                          "--disable-slash-commands", "--system-prompt", "You are a classifier. Answer with the requested token only.", prompt],
                         capture_output=True, text=True, timeout=pj.DEADLINE_S * 2, cwd="/tmp")
    return out.stdout.strip()


def main():
    ros = pj.roster()
    reqs = json.loads((DATA / "requests.json").read_text())
    keep = [r for r in reqs if r["label"] in ros or r["label"] == "none"]
    dropped = len(reqs) - len(keep)
    criteria = dict(ros)
    criteria["none"] = "No skill applies: a plain question, a small edit, a short reply, or a routine command."
    roster_text = "\n".join(f"- {k}: {v}" for k, v in criteria.items())

    def jev(item):
        if STOP["flag"]:
            return {"error": STOP["flag"]}
        t = time.monotonic()
        try:
            a, u, m = pj.ask({"request": item["text"]}, {
                "skill": {"type": "choice", "instructions": "Which skill best fits the user's `request`?", "criteria": criteria},
                "wanted": {"type": "noul", "instructions": "Does the `request` ask for work that a specialized skill would do, rather than a plain answer, a tiny edit, or a routine command?"}})
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                STOP["flag"] = f"http {e.code}"
            return {"error": f"http {e.code}"}
        except Exception as e:  # noqa: BLE001
            return {"error": type(e).__name__}
        s = a["skill"]
        return {"pick": s["choice"], "conf": s["confidence"], "probs": s["probabilities"], "wanted": a["wanted"]["noul"],
                "ms": round((time.monotonic() - t) * 1000), "model": m}

    def base(item):
        t = time.monotonic()
        try:
            ans = base_call(f"Skills:\n{roster_text}\n\nUser request: {item['text']}\n\nWhich single skill fits? Answer with the skill name only, or none.")
        except Exception as e:  # noqa: BLE001
            return {"error": type(e).__name__}
        pick = ans.strip().strip("`*'\".").split()[0] if ans.strip() else ""
        return {"pick": pick, "ms": round((time.monotonic() - t) * 1000)}

    with ThreadPoolExecutor(8) as ex:
        j = list(ex.map(jev, keep))
    print("jev done", sum("error" not in x for x in j), "ok of", len(j), flush=True)
    with ThreadPoolExecutor(6) as ex:
        b = list(ex.map(base, keep))
    rows = [{"id": r["id"], "slice": r["slice"], "label": r["label"], "ts": r["ts"], "jev": x, "base": y} for r, x, y in zip(keep, j, b)]
    (DATA / "results_skills.json").write_text(json.dumps({"model": pj.MODEL, "base_model": BASE_MODEL, "roster": len(ros),
                                                          "dropped_not_in_roster": dropped, "rows": rows}, indent=1))
    print("rows", len(rows), "dropped", dropped, "jev calls", len(keep), "base calls", len(keep))


if __name__ == "__main__":
    main()
