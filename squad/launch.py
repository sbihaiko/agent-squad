"""Start a run: one module decides the run folder, the `graph.json` copy, the run id and the defaults record.

Input is a graph plus intent: an adapter (or none, for the SDK adapter built from the defaults file), a defaults file,
a target, options and a shadow. Output is a ready engine. The CLI and both harness scripts call `launch`.
A run keeps its record in its run folder, so `squad resume <folder>` finds it with no `--run-dir`.

Guards: `tests/test_launch.py`.
"""
import json
import os
import sys
import time
from pathlib import Path

from squad import caps, git, modelreg, record, workflows
from squad import defaults as D
from squad import project_settings as P
from squad.engine import Engine, ResumeRefused
from squad.options import OptionError


def recorded_defaults(path, project=None):
    """Load and check a defaults file, with the project settings laid over it. Return (cfg, record), the record being {path, sha256, engine}.
    The hash is the plugin file's; the project file has its own record. An unsound file raises OptionError."""
    path = Path(path)
    cfg = D.load(path)
    bad = D.check(cfg)
    if bad:
        raise OptionError(f"{path} is unsound: {', '.join(bad)}")
    if project:
        cfg = P.overlay(cfg, project)[0]
        bad = D.check(cfg)
        if bad:
            raise OptionError(f"the project settings make {path} unsound: {', '.join(bad)}")
    return cfg, {"path": str(path.resolve()), "sha256": D.digest(path), "engine": D.engine_view(cfg), "watch": D.watch_view(cfg)}


def project_settings(target):
    """The project settings under `target` as the engine takes them: (settings, record, call limits). No file means (None, None, {}). A bad file raises OptionError."""
    data, rec = P.load(target)
    return data, rec, P.call_limits(data)


def _lowered(options, data, graph):
    """Options that lower a run-block cap to the project's value, where the project's is lower. A caller's own option stays."""
    out = dict(options or {})
    for k in ("spend_cap", "max_parallel", "deadline"):
        v = ((data or {}).get("run") or {}).get(k)
        cur = graph.run.get(k)
        if v is not None and k not in out and (cur is None or v < cur):
            out[k] = v
    return out


def launch_ceiling(target, defaults_path):
    """The machine-wide ceiling of a run: the one resolution of `caps` (squad/caps.py) from the defaults file and the project file."""
    return caps.ceiling(target, defaults_path)


def headless():
    """True where no page can open: a flagged environment (`CI`, `SQUAD_HEADLESS`) or a Linux shell with no display. Read from the environment, never by trying a browser."""
    env = os.environ
    if env.get("CI") or env.get("SQUAD_HEADLESS"):
        return True
    return sys.platform.startswith("linux") and not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def open_dashboard(folder, watch_seconds=None, *, reuse=False, server=None, browser=None):
    """Open the dashboard page for a run folder through the one function `squad dashboard` uses (`dashboard_server.start`). Print the URL.
    A headless environment prints the URL and opens nothing. `reuse` (a resume) opens no second tab where a server already runs for the folder."""
    from squad import dashboard_server
    server = server or dashboard_server.start
    if browser is None:
        import webbrowser
        browser = webbrowser.open
    already = reuse and dashboard_server.running(folder) is not None
    port = server(folder, **({"watch": watch_seconds} if watch_seconds else {}))
    url = f"http://{dashboard_server.LOOPBACK}:{port}/"
    print(f"dashboard: {url}")
    if headless():
        print("squad: no display here; the page is not opened. Open the URL above.")
    elif not already:
        browser(url)
    return url


def _watch_page(opener, folder, rec, resume):
    """The dashboard step of the watch. A failure is a warning on stderr and never a verdict."""
    try:
        (opener or open_dashboard)(folder, (rec["engine"].get("dashboard") or {}).get("watch_seconds"), reuse=resume)
    except Exception as e:  # noqa: BLE001 - an opener may fail in many ways; none of them stops a run
        print(f"squad: warning: the dashboard did not open: {e}", file=sys.stderr)


def _watch_monitor(ticker, folder, rec):
    """The monitor step of the watch. A failure to start it is a warning on stderr and never a verdict."""
    from squad import monitor
    try:
        w = monitor.Watcher(folder)
        (ticker or monitor.run_timer)(w, rec["watch"]["monitor_seconds"])
    except Exception as e:  # noqa: BLE001 - none of the ways a timer can fail stops a run
        print(f"squad: warning: the monitor did not start: {e}", file=sys.stderr)


def _adapter_and_record(adapter, defaults, project=None, watch=None):
    """Return (adapter, defaults record). No adapter means the SDK adapter from the defaults file; with an adapter, the file is checked and recorded only when named."""
    cfg = rec = None
    if adapter is None or defaults:
        cfg, rec = recorded_defaults(defaults or D.PATH, project)
        if watch is False:
            rec["watch"] = {**rec["watch"], "dashboard": False, "monitor": False}   # a script that starts many runs opens no page and starts no timer
    if adapter is None:
        from squad.sdk_adapter import SdkAdapter
        adapter = SdkAdapter.from_defaults(cfg)
    if cfg is None:
        cfg = P.overlay(D.load(), project)[0] if project else D.load()
    return adapter, rec, D.autonomy(cfg)    # the autonomy setting is recorded on every run, with the project overlay


def _router_open(entry, hour):
    """Whether the router chain allows one entry at this local hour: its switch first, then its window. An unreadable window is open."""
    if not entry.on:
        return False
    if not entry.hrs:
        return True
    try:
        start, end = int(entry.hrs[:2]), int(entry.hrs[3:5])
    except (ValueError, IndexError):
        return True
    return start <= hour < end if start <= end else (hour >= start or hour < end)


def router_overlay(graph, models_path, now=None):
    """The models each dispatch `allow.models` keeps under `chains.router` (its on/hrs, v1 and v2 through `modelreg.chain`).

    Returns `{dispatch node name: [spelling, ...]}`, empty when every entry is open. A spelling outside the router chain
    has no switch, so it stays. The overlay is a no-op when the router chain closes every model of a node: a graph that
    already carries a valid envelope starts as it did before the overlay existed. A dispatch with no envelope at all
    refuses the run by name, because there is nothing to start with. `now` stands in for the clock (tests). The overlay
    is recorded in `run_start` and applied at dispatch, so the authored graph keeps its hash and its run-folder copy
    across a resume.
    """
    try:
        entries = modelreg.chain(models_path, "router").entries
    except (OSError, ValueError) as exc:
        raise OptionError(f"models {models_path}: {exc}") from exc
    if not entries:
        return {}
    hour = time.localtime(now).tm_hour
    closed = {e.id for e in entries if not _router_open(e, hour)}
    if not closed:
        return {}
    overlay = {}
    for node in graph.doc["nodes"]:
        if node.get("kind") != "dispatch":
            continue
        allow = node.get("allow") or {}
        authored = [m for m in allow.get("models") or [] if isinstance(m, str) and m]
        if not authored:
            raise OptionError(f"dispatch node {node['name']!r} has no model envelope: `allow.models` is empty or absent, "
                              f"so the router chain in {models_path} has nothing to narrow")
        kept = [m for m in authored if modelreg.normalize(m).id not in closed]
        if kept and kept != authored:      # a closed set that empties a valid envelope leaves it as authored
            overlay[node["name"]] = kept
    return overlay


def launch(graph, run_dir=None, *, runs_dir="runs", adapter=None, defaults=None, models=None, graph_path=None, target=None, options=None, shadow=None, invoker="cli", name=None, watch=None, opener=None, ticker=None):
    """Return an engine ready to `run()`.

    `adapter` None builds the SDK adapter from the defaults file (`defaults`, else config/agent-defaults.yaml).
    With an adapter, the defaults are loaded, checked and recorded only when `defaults` names a file.
    `models` is the models.json whose router chain filters each dispatch envelope before the graph copy is written; the
    engine never looks for the file, so only the caller that starts a run names it.
    `run_dir` None means `runs_dir/<run id>`. The graph copy `graph.json` is written in the run folder unless it exists.
    `graph_path` None means that copy.
    `name` is the run id and the folder name (the workflow id rule); a name that has a folder is refused before anything is written. No name: a random id.
    `watch` False turns the dashboard off for this run (the scripts that start many runs); None follows `watch.dashboard` in the defaults,
    and no defaults file means no page. `opener` stands in for `open_dashboard` (tests).
    `watch` False also turns the monitor off; otherwise `watch.monitor` decides. `ticker(watcher, seconds)` stands in for the timer thread (tests).
    """
    data, prec, limits = project_settings(target)                # before any folder exists: an unsound project file refuses the run
    adapter, rec, autonomy = _adapter_and_record(adapter, defaults, data, watch)  # before any folder exists: an unsound file refuses the run
    overlay = router_overlay(graph, models) if models else {}   # before any folder exists: a closed set refuses the start
    if name is not None:
        run_id = name
        if not workflows.valid_id(name):
            raise OptionError(f"{name!r} is not a run name: use letters, digits, '.', '_' and '-'")
        run_dir = Path(run_dir) if run_dir else Path(runs_dir) / run_id
        if run_dir.exists():
            raise OptionError(f"run name {name!r} is taken: {run_dir} exists")
    else:
        run_id = record.new_run_id()
        run_dir = Path(run_dir) if run_dir else Path(runs_dir) / run_id
    copy = run_dir / "graph.json"
    ceiling = launch_ceiling(target, rec["path"]) if rec else None   # a missing caps block refuses the run by name, before a folder exists
    eng = Engine(graph, adapter, run_dir, options=_lowered(options, data, graph), target=target, run_id=run_id, invoker=invoker,
                 graph_path=Path(graph_path).resolve() if graph_path else copy, defaults=rec, shadow=shadow, project={"record": prec, "limits": limits}, autonomy=autonomy,
                 ceiling=ceiling, router_overlay=overlay)   # a bad option raises here, before a folder exists
    run_dir.mkdir(parents=True, exist_ok=True)
    if target:
        keep_runs_out_of_the_target(target, run_dir)
    if not copy.exists():   # the dashboard reads this copy; the engine never does
        copy.write_text(json.dumps(graph.doc, indent=2))
    if rec and rec["watch"]["dashboard"]:
        _watch_page(opener, run_dir, rec, resume=False)
    if rec and rec["watch"].get("monitor"):
        _watch_monitor(ticker, run_dir, rec)
    return eng


def keep_runs_out_of_the_target(target, run_dir):
    """A run folder inside the target repo is untracked noise a blanket `git add` would commit. Add its top folder to the repo's
    local ignore file (`.git/info/exclude`); no tracked file changes. A folder outside the target, or a target that is no repo, is left alone."""
    target, run_dir = Path(target).resolve(), Path(run_dir).resolve()
    if not git.available(str(target)) or target not in run_dir.parents:
        return None
    rel = run_dir.relative_to(target).parts[0]
    if git.run(["check-ignore", "-q", rel + "/"], str(target)).returncode == 0:
        return None
    path = Path(git.run(["rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], str(target)).stdout.strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text() if path.exists() else ""
    path.write_text(old + ("" if not old or old.endswith("\n") else "\n") + f"/{rel}/\n")
    return path


def target_drift(start, target=None):
    """A refusal text when the run's target cannot take a resume, else None (ADR-0107). The target is the one named, else the recorded one.
    Refused: a target that is missing, the owner's checkout (an old record, or a replaced target), and a target now on another branch than
    the recorded one. Nothing is migrated: a person starts a new run. A record from before `target_branch` has no branch to compare."""
    named = target or start.get("target")
    if not named:
        return None
    path = Path(named).resolve()
    if not path.is_dir():
        return f"the run target {path} is missing: its worktree is gone, start a new run"
    if git.available(str(path)) and (git.main_worktree(path) == path or git.toplevel(path) != path):
        return f"the run target {path} is the owner's checkout, which a run never owns: start a new run in its own worktree"
    recorded = start.get("target_branch")
    if recorded and git.available(str(path)):
        r = git.run(["symbolic-ref", "--short", "-q", "HEAD"], str(path))
        now = r.stdout.strip() if r.returncode == 0 else None
        if now != recorded:
            return f"the run target {path} is on branch {now or 'no branch'}, not the recorded branch {recorded}: restore the branch or start a new run"
    return None


def resume_engine(graph, run_dir, *, adapter=None, defaults=None, target=None, options=None, watch=None, opener=None, ticker=None):
    """The engine for `resume`: the same defaults rules as `launch`, no new folder, no new run id (the replay takes the id from the record).
    The router overlay comes from `run_start`, so the clock and the graph copy cannot move it. A page already open for the folder is reused."""
    data, prec, limits = project_settings(target)
    adapter, rec, autonomy = _adapter_and_record(adapter, defaults, data, watch)
    events = record.read(Path(run_dir) / "record.jsonl")
    if events and events[0]["event"] == "run_start":
        gone = target_drift(events[0], target)
        if gone:
            raise ResumeRefused(gone)
    overlay = (events[0].get("router_overlay") or {}) if events and events[0]["event"] == "run_start" else {}
    eng = Engine(graph, adapter, Path(run_dir), options=_lowered(options, data, graph), target=target, invoker="cli", defaults=rec,
                 project={"record": prec, "limits": limits}, autonomy=autonomy, ceiling=launch_ceiling(target, rec["path"]) if rec else None,
                 router_overlay=overlay)
    if rec and rec["watch"]["dashboard"]:
        _watch_page(opener, Path(run_dir), rec, resume=True)
    if rec and rec["watch"].get("monitor"):
        _watch_monitor(ticker, Path(run_dir), rec)
    return eng
