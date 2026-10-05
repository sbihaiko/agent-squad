"""The command line: `squad run` and `squad resume` (ADR-0106, ADR-0107).

The exit code is the verdict's exit code (ADR-0019). A usage or graph error exits 2.
`--script` runs the scripted adapter; `--adapter sdk` runs the real Claude Agent SDK adapter (squad.sdk_adapter).
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from squad import defaults, record
from squad import graph as G
from squad.adapter import ScriptedAdapter
from squad.engine import Engine, ResumeRefused
from squad.options import OptionError

USAGE = 2


def _opts(a):
    m = {"stop_after": a.stop_after, "spend_cap": a.spend_cap, "deadline": a.deadline, "max_parallel": a.max_parallel, "tier_pin": a.tier_pin,
         "expect_graph_hash": a.expect_graph_hash}
    return {k: v for k, v in m.items() if v is not None}


def _defaults(a):
    """The per-invocation load of the engine-level defaults. None unless the SDK adapter runs or --defaults names a file."""
    if a.adapter != "sdk" and not a.defaults:
        return None
    path = Path(a.defaults) if a.defaults else defaults.PATH
    cfg = defaults.load(path)
    bad = defaults.check(cfg)
    if bad:
        raise SystemExit(_fail(f"{path} is unsound: {', '.join(bad)}"))
    return {"path": str(path.resolve()), "sha256": defaults.digest(path), "engine": defaults.engine_view(cfg), "_cfg": cfg}


def _recorded(dflt):
    return {k: v for k, v in dflt.items() if k != "_cfg"} if dflt else None


def _adapter(a, dflt=None):
    if a.adapter == "sdk":
        from squad.sdk_adapter import SdkAdapter
        return SdkAdapter.from_defaults(dflt["_cfg"]) if dflt else SdkAdapter()
    if not a.script:
        raise SystemExit(_fail("pass --adapter sdk, or --script FILE (a JSON object of node name to steps)"))
    data = json.loads(Path(a.script).read_text())
    return ScriptedAdapter(data.get("script", data), repeat_last=bool(data.get("repeat_last", False)))


def _fail(msg):
    print(f"squad: {msg}", file=sys.stderr)
    return USAGE


def parser():
    p = argparse.ArgumentParser(prog="squad")
    sub = p.add_subparsers(dest="cmd", required=True)
    nn = sub.add_parser("new-node", help="print an agent node built from the defaults file and a role prompt")
    nn.add_argument("role")
    nn.add_argument("--name")
    nn.add_argument("--defaults")
    rp = sub.add_parser("report")
    rp.add_argument("run_id")
    rp.add_argument("--runs-dir", default="runs")
    rp.add_argument("--run-dir")
    rp.add_argument("--json", action="store_true")
    db = sub.add_parser("dashboard", help="open the run dashboard page for one run (never starts a run)")
    db.add_argument("run", nargs="?", help="run number (see --list), folder name or run id; the latest run when omitted")
    db.add_argument("--runs-dir", default="runs")
    db.add_argument("--no-open", action="store_true", help="print the page URL and open nothing")
    db.add_argument("--file", action="store_true", help="open the page from disk with no server; you then connect the run folder yourself")
    db.add_argument("--idle", type=float, default=None, help="seconds with no request before the server exits (default 7200)")
    db.add_argument("--defaults", default=None, help="agent defaults file, for the stream's watch step (default config/agent-defaults.yaml)")
    db.add_argument("--list", action="store_true", help="list the runs with their numbers")
    ed = sub.add_parser("editor", help="open the editor on a workflow in docs/squad/workflows/ (the dynamic workflow when no id)")
    ed.add_argument("workflow", nargs="?", help="workflow id: the file docs/squad/workflows/<id>.graph.json")
    ed.add_argument("--no-open", action="store_true", help="print the file and the page, open nothing")
    for name in ("run", "resume", "start"):
        s = sub.add_parser(name, **({"help": "start a run from a request or a task; the dynamic workflow unless --workflow names one"} if name == "start" else {}))
        if name == "run":
            s.add_argument("graph")
        elif name == "start":
            s.add_argument("text", nargs="+", help="the request, or with --task the task")
            s.add_argument("--workflow", help="workflow id in docs/squad/workflows/ (default: the dynamic workflow)")
            s.add_argument("--task", action="store_true", help="the text is one task, not a request")
        else:
            s.add_argument("run_id")
            s.add_argument("--graph")
            s.add_argument("--from", dest="from_node")
            s.add_argument("--answer", choices=["pass", "fail"])
        s.add_argument("--runs-dir", default="runs")
        s.add_argument("--run-dir")
        s.add_argument("--target")
        s.add_argument("--script")
        s.add_argument("--adapter", choices=["sdk"], help="run the real Claude Agent SDK adapter")
        s.add_argument("--defaults", help="agent defaults file (default config/agent-defaults.yaml with --adapter sdk)")
        s.add_argument("--stop-after")
        s.add_argument("--spend-cap", type=float)
        s.add_argument("--deadline", type=float)
        s.add_argument("--max-parallel", type=int)
        s.add_argument("--tier-pin")
        s.add_argument("--expect-graph-hash", help="refuse the run unless the loaded graph has this sha256 (exit 2, no record written)")
    return p


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        if a.cmd == "report":
            return _report(a)
        if a.cmd == "new-node":
            return _new_node(a)
        if a.cmd == "dashboard":
            return _dashboard(a)
        if a.cmd == "editor":
            return _editor(a)
        if a.cmd == "start":
            return _start(a)
        return asyncio.run(_run(a) if a.cmd == "run" else _resume(a))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else USAGE
    except G.InvalidGraph as e:
        return _fail("invalid graph:\n" + "\n".join(f"  {x}" for x in e.errors))   # the error list, never its joined text: joining a string prints one letter per line
    except (OptionError, ResumeRefused, record.RecordError) as e:
        return _fail(str(e))


def _new_node(a):
    from squad import scaffold
    try:
        node = scaffold.agent_node(defaults.load(a.defaults or defaults.PATH), a.role, a.name)
    except scaffold.ScaffoldError as e:
        return _fail(str(e))
    print(json.dumps(node, indent=2))
    return 0


def _dashboard(a):
    from squad import dashboard_server, defaults, runs
    try:
        if a.list:
            listed = runs.list_runs(a.runs_dir)
            for r in listed:
                print(f"{r.number} {r.run_id or r.folder.name} {r.folder.name}")
            if not listed:
                print(f"squad: no runs under {a.runs_dir}", file=sys.stderr)   # an empty list says why it is empty
            return 0
        run = runs.resolve(a.runs_dir, a.run)
    except runs.RunNotFound as e:
        return _fail(str(e))
    if a.file:
        url = runs.page_url(run)
        note = "connect this folder on the page when it asks"
    else:
        try:
            watch = (defaults.load(a.defaults or defaults.PATH).get("dashboard") or {}).get("watch_seconds", dashboard_server.WATCH_SECONDS)
        except (OSError, defaults.OptionError):
            watch = dashboard_server.WATCH_SECONDS   # the page still follows the run, at the server's own step
        try:
            port = dashboard_server.start(run.folder, watch=watch, **({"idle": a.idle} if a.idle is not None else {}))
        except RuntimeError as e:
            return _fail(str(e))
        url = f"http://{dashboard_server.LOOPBACK}:{port}/"
        note = "the page reads this run from a loopback server on this machine; its one write is a pause or resume request; it exits when idle"
    print(f"run {run.number}: {run.run_id or run.folder.name}\nfolder: {run.folder}\n{note}\n{url}")
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    return 0


def _editor(a):
    from squad import runs, workflows
    try:
        path = workflows.for_editor(a.workflow)
    except workflows.WorkflowError as e:
        return _fail(str(e))
    url = "file://" + quote(str(runs.PAGE))
    print(f"workflow: {path}\nsave folder: {workflows.default_dir()}\nIn the page, choose Open graph and pick the workflow above; Save graph then writes it in place.\n{url}")
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    return 0


def _start(a):
    from squad import workflows
    try:
        g = workflows.with_request(workflows.load(workflows.resolve(a.workflow)), " ".join(a.text), "task" if a.task else "work")
    except workflows.WorkflowError as e:
        return _fail(str(e))
    G.from_doc(g)   # an invalid graph stops here, before a run folder exists
    if not a.script:
        a.adapter = "sdk"
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(a.run_dir) if a.run_dir else Path(a.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "graph.json").write_text(json.dumps(g, indent=2))   # the graph that runs: the workflow plus the request
    a.graph, a.run_dir = str(run_dir / "graph.json"), str(run_dir)
    print(f"run folder: {run_dir}")
    return asyncio.run(_run(a))


def _report(a):
    from squad import report  # the only importer: the engine never reads the report (ADR-0109 rule 2)
    rec = (Path(a.run_dir) if a.run_dir else Path(a.runs_dir) / a.run_id) / "record.jsonl"
    if not rec.exists():
        return _fail(f"no run record at {rec}")
    rows = report.from_file(rec)
    print(json.dumps(rows, indent=2) if a.json else report.render(rows), end="")
    return 0


async def _run(a):
    g = G.load(a.graph)
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(a.run_dir) if a.run_dir else Path(a.runs_dir) / run_id
    dflt = _defaults(a)
    eng = Engine(g, _adapter(a, dflt), run_dir, options=_opts(a), target=a.target, run_id=run_id, graph_path=Path(a.graph).resolve(),
                 defaults=_recorded(dflt))
    run_dir.mkdir(parents=True, exist_ok=True)
    if not (run_dir / "graph.json").exists():   # the dashboard reads this copy; the engine never does (ADR-0127)
        (run_dir / "graph.json").write_text(json.dumps(g.doc, indent=2))
    v = await eng.run()
    print(v.message)
    return v.exit_code


async def _resume(a):
    run_dir = Path(a.run_dir) if a.run_dir else Path(a.runs_dir) / a.run_id
    rec = run_dir / "record.jsonl"
    if not rec.exists():
        return _fail(f"no run record at {rec}")
    events = record.read(rec)
    graph = a.graph or (events[0].get("graph_path") if events else None)
    if not graph:
        return _fail("the record names no graph file: pass --graph")
    dflt = _defaults(a)
    eng = Engine(G.load(graph), _adapter(a, dflt), run_dir, options=_opts(a), target=a.target or events[0].get("target"), invoker="cli",
                 defaults=_recorded(dflt))
    v = await eng.resume(answer=a.answer, from_node=a.from_node, release=True)   # the person who resumes lets go of every hold (ADR-0128)
    print(v.message)
    return v.exit_code


if __name__ == "__main__":
    sys.exit(main())
