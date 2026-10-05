# /// script
# requires-python = ">=3.10"
# dependencies = ["claude-agent-sdk==0.2.161", "anyio", "pyyaml", "jsonschema"]
# ///
"""Run the router-dynamic graph on subject issues with the real SDK adapter (tickets #87, #88, #90).

Run: uv run scripts/run_router.py 17            one issue
     uv run scripts/run_router.py 19,20,21,22   a set of issues in waves: one clone, one run
A router agent reads the task and asks for children inside the dispatch envelope (ADR-0113). In a set, the router runs once per
wave and reads only the ledger; code lands each passing child into the clone, in request order (ADR-0113 amendment, 2026-10-01).
The graph that runs is written to graph.json in the run folder, so the dashboard loads the graph the record names. A harness, not a gate.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from squad import defaults, graph, subject        # noqa: E402
from squad.engine import Engine                   # noqa: E402
from squad.sdk_adapter import SdkAdapter          # noqa: E402

# Probe knobs (#100): a low turn or spend cap forces a child to stop at its cap, so a real run exercises the cap path. Unset, the caps are the standing ones.
CHILD_SPEND, CHILD_TURNS = os.environ.get("ROUTER_CHILD_SPEND", "2"), os.environ.get("ROUTER_CHILD_TURNS", "60")

# Regression probe (#100): the set router also asks one child, after that issue lands, to break the issue's held-out test, so the landing check meets a real regression.
PROBE_BREAK = os.environ.get("ROUTER_PROBE_BREAK")


def probe_break_text(prep):
    n = int(PROBE_BREAK)
    return (f"PROBE: this is a test of the landing check, on a throwaway clone. Once issue {n} is landed with held_out not \"fail\", request exactly one extra child, with task \"probe\", the tdd skill and the tools Read, Edit, Write, Bash, Glob, Grep. "
            f"Its prompt tells it to change the production code under billing/ so that the behavior issue {n} added no longer holds (the tests of issue {n} then fail), to touch no test file, and to end with the contract below, using [\"true\"] as ac_commands. "
            "That child is expected to land and then be undone by the landing check. The set is done when every issue has landed, the probe child has been asked once, and its outcome is in the ledger. ")


CONTRACT = ('When done, reply with one JSON object in a ```json fence: {"status": "pass", "diff": {"files": [{"path": "<changed file>", "generated": false}]}, '
            '"ac_commands": %s}. Copy ac_commands exactly. Do not run git commit: the harness lands your work.')


def single_instructions(prep, cfg):
    return (
        f"The task is the issue in {prep.issue_file}. Read it and CLAUDE.md. Do not change any file. Decide what work the task needs. "
        "Request one to three children. Each child has a skill from this list: tdd, code-review, research, grill-with-docs. "
        f"Set each child's `task` to the issue number it works ({prep.issue}), so the loop ceiling counts attempts of one task. "
        "Give each child a prompt that names the issue file, and ends with: '" + CONTRACT % json.dumps(prep.ac_commands) + "' "
        "Order the work with `after`: a reviewing child lists the index of the implementing request it reviews, for example \"after\": [0], and the implementing request comes first in the list. "
        f"Give an implementing child the tools Read, Edit, Write, Bash, Glob, Grep, and a spend of {CHILD_SPEND} and a turn cap of {CHILD_TURNS}. Give a reviewing child only Read, Grep, Glob. "
        "Reply with one JSON object in a ```json fence: " + subject.spawn_example(cfg) + ".")


def set_instructions(prep, cfg):
    rows = "\n".join(f"- issue {n}: file {prep.issue_files[n]}; blocked by {prep.blocked_by[n] or 'nothing'}; held-out command {json.dumps([prep.ac_commands[n]])}"
                     for n in prep.issues)
    return (
        "The task is this set of issues of one project. Read CLAUDE.md and the issue files. Do not change any file.\n" + rows + "\n"
        "You run in waves. Each time you are called, your input holds a `ledger`: one row per finished child with its task, outcome, files, checks, landed_head and held_out. "
        "Code lands each passing child into the project in the order you requested it. You never merge. A child with landed_head null did not land. A child with held_out \"fail\" landed, but its held-out command is still red: its issue is not done. "
        "Request, in this wave, only issues whose blockers are all landed in the ledger and that no ledger row already landed. Put two issues in the same wave only when they touch different files; two children that edit one file conflict at landing. When unsure, use separate waves. "
        f"Ask for at most three children. Each child uses the tdd skill, the tools Read, Edit, Write, Bash, Glob, Grep, a spend of {CHILD_SPEND} and a turn cap of {CHILD_TURNS}. "
        "Set each child's `task` to the issue number it works, so the loop ceiling counts attempts of one task: a child that advances to a new issue never spends the ceiling, and one issue asked for three times ends the run. "
        "Never forbid a child any file its held-out command needs: billing/api.py is the facade every acceptance test imports, so a child may always edit it. Keep the prompt free of other edit limits; a limit that blocks the fix makes the child give up. "
        "Name one issue file in the child prompt, and end the prompt with: '" + CONTRACT % "<that issue's held-out command, as a one-item list>" + "' "
        + (probe_break_text(prep) if PROBE_BREAK else "") +
        "If a child failed, did not land (for example reason merge_conflict), or has held_out \"fail\", request one repair child for that issue in the next wave: its worktree starts from the project as landed, so it redoes the issue on top of the landed work. "
        "When every issue has a landed row whose held_out is not \"fail\", or a second attempt failed, reply with one JSON object in a ```json fence: "
        '{"status": "pass", "spawns": [], "done": true}. Otherwise reply with one JSON object in a ```json fence: ' + subject.spawn_example(cfg) + ".")


def main():
    clean = subject.clean_env(os.environ)
    os.environ.clear()
    os.environ.update(clean)
    arg = sys.argv[1]
    issues = [int(x) for x in arg.split(",")]
    tag = "-".join(f"{n:02d}" for n in issues)
    out = ROOT / "runs" / f"router-{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
    home, bench = str(Path.home() / "sources" / "subject"), str(ROOT / "bench" / "subject")
    g = json.loads((ROOT / "docs" / "design" / "router.graph.json").read_text())
    cfg = subject.load_config()                      # the models, from config/subject.yaml (owner ruling 2026-10-02)
    subject.apply_router_models(g, cfg)
    # a fixed cap sized for 4 issues starves a larger set: spend and deadline scale with the set size.
    n = len(issues)
    g["run"].update(spend_cap=max(10, n * 1.5), deadline=max(5400, n * 450) if n > 1 else 3600)
    if n > 1:
        prep = subject.prepare_set(issues, home, bench, out)
        text, gp = set_instructions(prep, cfg), out / "graph.json"
        # `max_by: "task"` (#98): `max` is the ceiling of one issue's attempts, not of the wave count, so a wave that
        # advances to a new issue never spends it. A wave that lands nothing for 3 waves still ends the run `waves_stalled`.
        g["nodes"].append({"name": "waves", "kind": "loop", "max": {"value": 3, "min": 1}, "max_by": "task",
                            "stall_after": {"count": 3, "by": "land"}, "children": ["route"]})
        main_node = next(n for n in g["nodes"] if n["name"] == "main")
        main_node["children"] = ["waves", "final_gate"]
        next(n for n in g["nodes"] if n["name"] == "route")["next"] = {"pass": "repeat", "fail": "repeat"}    # a conflict gives the router a next wave to repair it
    else:
        prep = subject.prepare(issues[0], home, bench, out, runs_dir=str(ROOT / "runs"))
        text, gp = single_instructions(prep, cfg), prep.graph_path     # the subject graph that prepare wrote is replaced
    for n in g["nodes"]:
        if n["name"] == "router":
            n["instructions"] = text
        if n["name"] == "route":
            n["deadline"] = 3000 if len(issues) == 1 else 4800
    gp.write_text(json.dumps(g, indent=1))
    graph.load(gp)
    cfg = defaults.load(str(defaults.PATH))
    rec = {"path": str(defaults.PATH), "sha256": defaults.digest(str(defaults.PATH)), "engine": defaults.engine_view(cfg)}
    print("RUN DIR", out, flush=True)
    eng = Engine(graph.load(gp), SdkAdapter.from_defaults(cfg), out / "run", target=prep.target, graph_path=gp, defaults=rec)
    v = asyncio.run(eng.run())
    print("verdict", v, flush=True)
    missing = subject.undelivered(eng.record.events, issues) if len(issues) > 1 else []
    if missing:      # a router that gave up on an issue leaves a `completed` run: the final gate sees landed children only
        print("NOT DELIVERED: issues", missing, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
