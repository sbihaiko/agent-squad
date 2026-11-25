"""The command line: `squad run` and `squad resume`.

The exit code is the verdict's exit code. A usage or graph error exits 2.
`--script` runs the scripted adapter; `--adapter sdk` runs the real Claude Agent SDK adapter (squad.sdk_adapter).
"""
import argparse
import asyncio
import base64
import importlib.util
import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import quote

from squad import defaults, launch, record, tailnet_check
from squad import graph as G
from squad.adapter import ScriptedAdapter
from squad.engine import ResumeRefused
from squad.options import OptionError

USAGE = 2


def model_hours_arg(text):
    """{model: window} from `--model-hours`: MODEL=WINDOW pairs joined by ",". A window may hold "," itself
    ("mon-fri 00-01,10-24 UTC"), so a piece with no "=" belongs to the window before it."""
    pairs: list[list[str]] = []
    for piece in text.split(","):
        if "=" in piece:
            pairs.append(piece.split("=", 1))
        elif pairs:
            pairs[-1][1] += "," + piece
    return {m.strip(): w.strip() for m, w in pairs}


def model_ctx_arg(text):
    """{model: window} from `--model-ctx`: MODEL=WINDOW pairs joined by "," (for example `mimo=128k,opus=1m`)."""
    return {m.strip(): w.strip() for m, w in (p.split("=", 1) for p in text.split(",") if "=" in p)}


def _opts(a):
    m = {"stop_after": a.stop_after, "spend_cap": a.spend_cap, "deadline": a.deadline, "max_parallel": a.max_parallel, "tier_pin": a.tier_pin,
         "expect_graph_hash": a.expect_graph_hash, "keep_children": a.keep_children or None, "fix_run": a.fix_run or None, "wave_ceiling": a.wave_ceiling,
         "spent_models": a.spent_models.split(",") if a.spent_models is not None else None,
         "model_hours": model_hours_arg(a.model_hours) if a.model_hours is not None else None,
         "model_order": a.model_order.split(",") if a.model_order is not None else None,
         "model_ctx": model_ctx_arg(a.model_ctx) if a.model_ctx is not None else None}
    return {k: v for k, v in m.items() if v is not None}


def _adapter(a):
    """The scripted adapter, or None: `launch` then builds the SDK adapter from the defaults file."""
    if a.adapter == "sdk":
        return None
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
    ed = sub.add_parser("editor", help="open the editor on a workflow in docs/squad/workflows/ (the `default` workflow when no id)")
    ed.add_argument("workflow", nargs="?", help="workflow id: the file docs/squad/workflows/<id>.graph.json")
    ed.add_argument("--no-open", action="store_true", help="print the file and the page, open nothing")
    pchk = sub.add_parser("premise-check", help="run one premise-check round on a published spec and record it as one comment there")
    pchk.add_argument("spec", type=int, help="the number of the published spec the round checks")
    pchk.add_argument("--findings", help="the reviewer's findings, a JSON array of objects (required unless --refute)")
    pchk.add_argument("--refute", metavar="PREMISE", help="a probe refuted this premise: it must be one the check records as needs a probe")
    pchk.add_argument("--answers", help="the owner's answers, a JSON array of objects; each rewrites the spec and opens the next round")
    pchk.add_argument("--round", dest="round_no", type=int, default=None, help="the round number (default: the next one after the recorded round)")
    pchk.add_argument("--reviewer", help="the model this round ran under: checked against the registry; a no-tools model is refused")
    pchk.add_argument("--proxy", help="the registry model or 'panel' that answers while the owner is away; it must differ from the reviewer")
    pchk.add_argument("--repo", default="sbihaiko/agent-squad", help="OWNER/NAME of the repository that holds the spec")
    pchk.add_argument("--dry-run", action="store_true", help="print the comment and write nothing")
    su = sub.add_parser("setup", help="print the plan to prepare a project (an ADR index); --apply writes it, only adding")
    su.add_argument("--target", default=".", help="the project directory (default: the current one)")
    su.add_argument("--apply", action="store_true", help="write the missing files; an existing file is never overwritten")
    su.add_argument("--language", help="the language of the project's record; adds the language rule (omit to skip it)")
    su.add_argument("--test-command", help="the project's test command, named in the ADRs")
    su.add_argument("--tracker", help="the project's issue tracker, named in the ADRs")
    su.add_argument("--check", action="store_true", help="fail when a rule ADR has no CLAUDE.md bullet or a bullet does not resolve; writes nothing")
    su.add_argument("--memory-dir", help="the project's memory directory (default: SQUAD_MEMORY_DIR, else the Claude Code project folder)")
    su.add_argument("--approve-memory", action="store_true", help="with --apply, also write the memory files proposed for ledger lines of CLAUDE.md")
    su.add_argument("--no-auto-commit", action="store_true", help="the owner declines the commits-and-pushes-are-automatic rule")
    cf = sub.add_parser("config", help="change the project settings file docs/squad/defaults.yaml")
    cf.add_argument("--target", default=".", help="the project directory (default: the current one)")
    cf.add_argument("--stdin", action="store_true", help="read the subcommand and its words from standard input")
    cs = cf.add_subparsers(dest="config_cmd", required=True)
    c = cs.add_parser("set", help="write one key, for example limits.max_stalls 8")
    c.add_argument("key")
    c.add_argument("value")
    c = cs.add_parser("reset", help="remove one key; the file keeps the others")
    c.add_argument("key")
    cs.add_parser("check", help="report every defect of the project file (unknown key, credential key, bad type, out-of-range value); exit 1 on a defect")
    cs.add_parser("init", help="create the file with only the keys changed so far")
    c = cs.add_parser("show", help="print every setting with its value, unit and source layer")
    c.add_argument("--defaults", help="agent defaults file (default config/agent-defaults.yaml)")
    sl = sub.add_parser("slack", help="the project's Slack files: the committed slack.yaml and the profile's slack.local.yaml")
    sls = sl.add_subparsers(dest="slack_cmd", required=True)
    c = sls.add_parser("check", help="read both files, name the failing key in each message, and exit 1 on a defect")
    c.add_argument("--target", default=".", help="the project directory (default: the current one)")
    c.add_argument("--profile", help="the profile folder holding <project>.slack.local.yaml (default: SG_PROFILES_DIR)")
    c.add_argument("--project", help="the project name of the local file (default: SG_PROJECT, else the target folder name)")
    tn = sub.add_parser("tailnet", help="print the tailscale serve command for the hub, or check the hub is not on Funnel")
    tns = tn.add_subparsers(dest="tailnet_cmd", required=True)
    c = tns.add_parser("serve-command", help="print the tailscale serve command for the hub port; starts nothing")
    c.add_argument("port", nargs="?", type=int, default=tailnet_check.HUB_PORT)
    c = tns.add_parser("check", help="exit 0 when the hub port is served to the tailnet only; exit 1 and name the cause otherwise")
    c.add_argument("port", nargs="?", type=int, default=tailnet_check.HUB_PORT)
    c.add_argument("--status", help="read this JSON file instead of running `tailscale serve status --json`")
    for name in ("run", "resume", "start", "work", "task"):
        helps = {"start": "start a run from a request or a task; the `default` workflow unless --workflow names one",
                 "work": "alias of `start`: start a run from a request", "task": "alias of `start --task`: start a run for one task"}
        s = sub.add_parser(name, **({"help": helps[name]} if name in helps else {}))
        if name == "run":
            s.add_argument("graph")
        elif name in ("start", "work", "task"):
            s.add_argument("text", nargs="*", help="the request, or with --task the task")
            s.add_argument("--stdin", action="store_true", help="read the request from standard input; a `--workflow <id>` in it names the workflow")
            s.add_argument("--workflow", help="workflow id in docs/squad/workflows/ (default: the `default` workflow)")
            s.add_argument("--name", default=None, help="the run id and the run folder name (letters, digits, '.', '_', '-'); default: a random id")
            s.add_argument("--task", action="store_true", help="the text is one task, not a request")
        else:
            s.add_argument("run_id")
            s.add_argument("--graph")
            s.add_argument("--from", dest="from_node")
            s.add_argument("--answer", choices=["pass", "fail"])
            s.add_argument("--auto-source", help="an automatic resume: the decision source (`autonomy`). Records one autonomy_resume event with the resume count; refuses at the cap")
        if name != "resume":
            s.add_argument("--models", help="models.json for the router chain's on/hrs overlay; a resume never recomputes it")
        s.add_argument("--runs-dir", default="runs")
        s.add_argument("--run-dir")
        s.add_argument("--no-watch", action="store_true", help="start no dashboard page or monitor")
        s.add_argument("--target")
        s.add_argument("--script")
        s.add_argument("--adapter", choices=["sdk"], help="run the real Claude Agent SDK adapter")
        s.add_argument("--defaults", help="agent defaults file (default config/agent-defaults.yaml with --adapter sdk)")
        s.add_argument("--stop-after")
        s.add_argument("--spend-cap", type=float)
        s.add_argument("--deadline", type=float)
        s.add_argument("--max-parallel", type=int)
        s.add_argument("--tier-pin")
        s.add_argument("--spent-models", help="comma-separated model ids whose subscription is spent: a call chain leaves them out unless no model remains (an empty value lists none)")
        s.add_argument("--model-hours", help="comma-separated MODEL=WINDOW pairs: a call chain leaves a model out of its window, unless no model remains. "
                                                    "A window is \"HH-HH\" local, or clauses joined by \";\", each with optional days and a UTC suffix: "
                                                    "\"mon-fri 00-01,04-06,10-24 UTC; sat-sun 00-24 UTC\"")
        s.add_argument("--model-order", help="comma-separated model ids in the order the launcher read them (models.json coding_models): an in-run fallback follows it after its pick")
        s.add_argument("--model-ctx", help="comma-separated MODEL=WINDOW pairs: a chain entry whose declared context window is under the prompt it would "
                                           "send is skipped, unless no entry remains. A window is \"128k\", \"1m\" or a token count")
        s.add_argument("--keep-children", action="store_true",
                       help="keep every landed child's worktree and branch after the verdict: a later pass reads them (the loop's wave entry)")
        s.add_argument("--fix-run", action="store_true",
                       help="the run repairs a work branch, so its acceptance commands prove in the tree its child wrote, never in the target (#1374)")
        s.add_argument("--wave-ceiling", help="a file that holds one epoch, the wave's age cap: a call and a dispatch child end before it, "
                                              "less an entry reserve; no file means no ceiling (#2840)")
        s.add_argument("--expect-graph-hash", help="refuse the run unless the loaded graph has this sha256 (exit 2, no record written)")
    return p


def _config_argv(argv):
    """`config --stdin`: a slash command passes the subcommand in a quoted here-document, so no character of it reaches the shell."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["config"] and "--stdin" in argv:
        argv.remove("--stdin")
        argv += shlex.split(sys.stdin.read())
    return argv


def main(argv=None):
    a = parser().parse_args(_config_argv(argv))
    if a.cmd in ("work", "task"):      # aliases of `start`, as the plugin commands are
        a.task, a.cmd = a.task or a.cmd == "task", "start"
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
        if a.cmd == "setup":
            return _setup(a)
        if a.cmd == "premise-check":
            from squad import premise_check
            return premise_check.main([str(a.spec), "--repo", a.repo]
                                      + (["--findings", a.findings] if a.findings else [])
                                      + (["--refute", a.refute] if a.refute else [])
                                      + (["--answers", a.answers] if a.answers else [])
                                      + (["--round", str(a.round_no)] if a.round_no is not None else [])
                                      + (["--reviewer", a.reviewer] if a.reviewer else [])
                                      + (["--proxy", a.proxy] if a.proxy else []) + (["--dry-run"] if a.dry_run else []))
        if a.cmd == "config":
            return _config(a)
        if a.cmd == "slack":
            return _slack(a)
        if a.cmd == "tailnet":
            return _tailnet(a)
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


def _slack(a):
    """`squad slack check`: one `fail:` line per defect, each naming the file and the key a person fixes (ADR-0177)."""
    from squad import slackpolicy
    defects = slackpolicy.check(a.target, a.profile, a.project)
    for d in defects:
        print(f"fail: {d}")
    if defects:
        print(f"slack check: {len(defects)} defect(s); fix the key each line names")
        return 1
    print("slack check: ok")
    return 0


def _tailnet(a):
    if a.tailnet_cmd == "serve-command":
        print(tailnet_check.serve_command(a.port))
        return 0
    try:
        status = tailnet_check.load_status(a.status)
    except tailnet_check.StatusError as e:
        print(f"tailnet check: {e}")
        return 1
    problems = tailnet_check.diagnose(status, a.port)
    for m in problems:
        print(f"fail: {m}")
    if problems:
        return 1
    print(f"tailnet check: ok, hub port {a.port} is served to the tailnet only")
    return 0


def _setup(a):
    from squad import setup
    if not Path(a.target).is_dir():
        return _fail(f"no directory {a.target}")
    if a.check:
        problems = setup.check(a.target)
        for m in problems:
            print(f"drift: {m}")
        print("setup check: " + (f"{len(problems)} drift" if problems else "ok, CLAUDE.md and the rule ADRs agree"))
        return 1 if problems else 0
    opts = {"language": a.language, "test_command": a.test_command, "tracker": a.tracker, "auto_commit": not a.no_auto_commit,
            "memory_dir": a.memory_dir, "approve_memory": a.approve_memory}
    for action, rel, text in setup.plan(a.target, **opts):
        if action in ("keep", "report"):
            print(f"{action}: {rel}" + (f": {text}" if action == "report" else ""))
        else:
            verb = {"append": "append to", "link": "link", "propose": "propose", "arm": "arm"}.get(action, "create")
            done = a.apply and (action != "propose" or a.approve_memory)
            print(f"{verb if done else 'would ' + verb}: {rel}")
    if a.apply:
        setup.apply(a.target, **opts)
    else:
        print("plan only; nothing written. Pass --apply to write it.")
    return 0


def _config(a):
    from squad import project_settings as P
    path = P.path_for(a.target)
    if a.config_cmd == "show":
        data, _ = P.load(a.target)
        merged, sources = P.overlay(defaults.load(a.defaults or defaults.PATH), data)
        rows = []
        for key, (_, _, _, unit, _) in P.CATALOG.items():
            sec, _, name = key.partition(".")
            v = merged.get(sec, {}).get(name)
            rows.append((key, "(unset)" if v is None else json.dumps(v), unit, sources.get(key, "plugin")))
        for role, over in sorted((merged.get("roles") or {}).items()):
            for k, v in over.items():
                rows.append((f"roles.{role}.{k}", json.dumps(v), P.CATALOG.get(f"options.{k}", ("", 0, 0, "option"))[3], sources.get(f"roles.{role}.{k}", "plugin")))
        w = [max(len(r[i]) for r in rows) for i in range(3)]
        print(f"project file: {P.path_for(a.target)}" + ("" if data is not None else " (none)"))
        for r in rows:
            print("  ".join(r[i].ljust(w[i]) for i in range(3)) + "  " + r[3])
        return 0
    if a.config_cmd == "check":
        _, bad = P.check_file(a.target)
        for key, why in bad:
            print(f"{path}: {key}: {why}")
        if bad:
            return _fail(f"{len(bad)} defect(s) in {path}")
        print(f"{path}: ok" if path.is_file() else f"no project file at {path}; the plugin defaults apply")
        return 0
    if a.config_cmd == "set":
        changed = P.set_key(a.target, a.key, a.value)
        print(f"{'set' if changed else 'unchanged'}: {a.key} in {path}")
    elif a.config_cmd == "reset":
        changed = P.reset_key(a.target, a.key)
        print(f"{'reset' if changed else 'unchanged'}: {a.key} in {path}")
    else:
        print(f"{'created' if P.init(a.target) else 'kept'}: {path}")
    return 0


def _tailnet_of(target):
    """The tailnet host and allowed logins of the project file under `target`. A file that is refused leaves the server loopback only."""
    from squad import project_settings as P
    from squad.options import OptionError
    try:
        data, _ = P.load(target)
    except OptionError as e:
        print(f"squad: tailnet access off, the project file is refused: {e}", file=sys.stderr)
        return None, []
    block = (data or {}).get("tailnet") or {}
    return block.get("host") or None, list(block.get("allow") or [])


def _dashboard(a):
    from squad import dashboard_server, defaults, runs
    try:
        if a.list:
            listed = runs.list_runs(a.runs_dir)
            for r in listed:
                print(f"{r.number} {r.run_id or r.folder.name} {r.folder.name} {runs.request_of(r.folder)}".rstrip())
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
        tailnet_host, allowed = _tailnet_of(".")
        tailnet = {"tailnet_host": tailnet_host, "allowed_logins": allowed} if tailnet_host else {}
        try:
            port = dashboard_server.start(run.folder, watch=watch, **tailnet, **({"idle": a.idle} if a.idle is not None else {}))
        except RuntimeError as e:
            return _fail(str(e))
        url = f"http://{dashboard_server.LOOPBACK}:{port}/"
        note = "the page reads this run from a loopback server on this machine; its one write is a pause or resume request; it exits when idle"
        if tailnet_host:
            note += f"; on the tailnet at https://{tailnet_host}/ for the listed logins"
    print(f"run {run.number}: {run.run_id or run.folder.name}\nrequest: {runs.request_of(run.folder) or '(none)'}\nfolder: {run.folder}\n{note}\n{url}")
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    return 0


EDITOR_LINK_MAX = 20_000   # a longer link is cut or refused by a browser


def _editor(a):
    from squad import runs, workflows
    try:
        path = workflows.for_editor(a.workflow)
    except workflows.WorkflowError as e:
        return _fail(str(e))
    url = "file://" + quote(str(runs.PAGE))
    payload = base64.urlsafe_b64encode(json.dumps({"name": path.name, "text": path.read_text()}).encode("utf-8")).decode("ascii").rstrip("=")
    lines = [f"workflow: {path}", f"save folder: {workflows.default_dir()}",
             "Save graph downloads a copy; choose Open graph and pick the workflow above to write it in place."]
    if len(payload) <= EDITOR_LINK_MAX:
        url += "#workflow=" + payload
    else:
        lines.append("The workflow is too large for a link, so the page opens empty: choose Open graph and pick it above.")
    print("\n".join([*lines, url]))
    if not a.no_open:
        import webbrowser
        webbrowser.open(url)
    return 0


def _stdin_request(a):
    """The request from standard input: a slash command passes its arguments in a quoted here-document, so no character of the
    request reaches the shell. A `--workflow <id>` or `--name <name>` token in it is the flag; the rest is the text."""
    raw = sys.stdin.read()
    for flag in ("workflow", "name"):
        m = re.search(rf"(?:^|\s)--{flag}\s+(\S+)", raw)
        if m:
            setattr(a, flag, m.group(1))
            raw = raw[:m.start()] + " " + raw[m.end():]
    return " ".join(raw.split())


def _start(a):
    from squad import workflows
    text = _stdin_request(a) if a.stdin else " ".join(a.text)
    if not text:
        return _fail("start needs a request: give the text or use --stdin")
    if workflows.names_only_a_skill(text):
        return _fail(f"the request {text!r} names a skill and no work. Name the work: an issue number, a URL or a path.")
    try:
        g = workflows.with_request(workflows.load(workflows.resolve(a.workflow)), text, "task" if a.task else "work")
    except workflows.WorkflowError as e:
        return _fail(str(e))
    graph = G.from_doc(g)   # an invalid graph stops here, before a run folder exists
    cannot = workflows.unrunnable_skills(g, text)
    if cannot:
        known = ", ".join(workflows.ids()) or "none"
        print(f"squad: warning: the request names {', '.join('/' + c for c in cannot)}, which this workflow cannot run. Workflows: {known}. Use --workflow <id>.", file=sys.stderr)
    if not a.script:
        a.adapter = "sdk"
        if importlib.util.find_spec("claude_agent_sdk") is None:      # a bare install: stop before a run folder exists, not in a traceback
            return _fail("the claude-agent-sdk package is missing. Install it with `pip install 'agent-squad[sdk]'`, or run with `uv run --extra sdk squad start ...`.")
    return asyncio.run(_run(a, graph))


def _report(a):
    from squad import report  # the only importer: the engine never reads the report
    rec = (Path(a.run_dir) if a.run_dir else Path(a.runs_dir) / a.run_id) / "record.jsonl"
    if not rec.exists():
        return _fail(f"no run record at {rec}")
    rows = report.from_file(rec)
    print(json.dumps(rows, indent=2) if a.json else report.render(rows), end="")
    return 0


async def _run(a, g=None):
    """Start the run. `g` is a graph already loaded (the `start` command, whose graph file is the run folder's copy); else `a.graph` is the file."""
    graph = g or G.load(a.graph)
    eng = launch.launch(graph, a.run_dir, runs_dir=a.runs_dir, adapter=_adapter(a), defaults=a.defaults, models=getattr(a, "models", None),
                        graph_path=None if g else a.graph,
                        target=a.target, options=_opts(a), name=getattr(a, "name", None), watch=False if a.no_watch else None)
    if g:
        print(f"run: {eng.run_id}\nrun folder: {Path(eng.run_dir).resolve()}")
    v = await eng.run()
    print(v.message)
    return v.exit_code


def _run_folder(runs_dir, ref):
    """The folder of the run `ref` names: its folder name, else its run id or name. No match: the folder `ref` would be."""
    from squad import runs
    run = runs.find(runs.list_runs(runs_dir), ref)
    return run.folder if run else Path(runs_dir) / ref


def _auto_resume(rec, source):
    """Ticket #182: record one automatic resume. Return a refusal text when autonomy is off or the cap is reached, else None. The CLI never calls a model."""
    events = rec.events
    setting = events[0].get("autonomy") or {"enabled": True, "max_resumes": 3}
    done = sum(1 for e in events if e["event"] == "autonomy_resume")
    if not setting["enabled"]:
        return "autonomy is off for this run: no automatic resume"
    if done >= setting["max_resumes"]:
        return f"autonomy resume cap reached ({done} of {setting['max_resumes']}): the run stays as its verdict says"
    rec.append("autonomy_resume", count=done + 1, source=source)
    return None


async def _resume(a):
    run_dir = Path(a.run_dir) if a.run_dir else _run_folder(a.runs_dir, a.run_id)
    rec = run_dir / "record.jsonl"
    if not rec.exists():
        return _fail(f"no run record at {rec}")
    events = record.read(rec)
    graph = a.graph or (events[0].get("graph_path") if events else None)
    if not graph:
        return _fail("the record names no graph file: pass --graph")
    eng = launch.resume_engine(G.load(graph), run_dir, adapter=_adapter(a), defaults=a.defaults, target=a.target or events[0].get("target"),
                               options=_opts(a), watch=False if a.no_watch else None)
    if a.auto_source:
        refused = _auto_resume(eng.record, a.auto_source)
        if refused:
            return _fail(refused)
    v = await eng.resume(answer=a.answer, from_node=a.from_node, release=True)   # the person who resumes lets go of every hold
    print(v.message)
    return v.exit_code


if __name__ == "__main__":
    sys.exit(main())
