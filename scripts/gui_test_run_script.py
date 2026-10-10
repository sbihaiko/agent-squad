#!/usr/bin/env python3
"""Run a whole `gui-test/1` script end to end on the squad's GUI test infrastructure (ADR-0272 items 6-8, #1222).

    python3 scripts/gui_test_run_script.py <script.json> [--adapter mesen-gui] [--out <results.json>]
                                           [--batch <id> | --case <id>] [--file-bug]

The runner, the script format and the adapter interface live with the squad (item 6, P1-A); the emulator adapter this
command drives lives in MesenAI, loaded from `MESENAI_ROOT` through `squad.gui_test_worker`. This command is the door
`/to-test-scenario`'s Run step and a person use: one script in, one result per step out, no pytest code.

Order of work, and why: the file is read and validated as `gui-test/1` first, then the adapter's advertised
capabilities are read (this only builds the adapter class, never launches the application), then the script is
validated *against those capabilities*, and only then is anything started. A script that names an action, check or
variant value the adapter does not advertise is rejected before launch, naming what is missing (item 6).

The environment is a precondition, never a skip: `MESENAI_ROOT` without `scripts/gui_test`, `MESEN_GUI_BINARY` that is
not a file, or a native core that cannot be loaded makes the run fail with a non-zero exit and no step result at all.
A skipped run is exactly the green-but-blind CI the ADR names as the trap.

Verdicts, one per step in script order: `passed`, `failed (expected X, observed Y)`, `failed (unknown id)`,
`failed (timeout waiting for ...)`, `needs review` (an automated step nothing objective decided and no model saw,
item 8), `pending` (a `manual` step: nobody ran it). Exactly one of the last two groups is what a run may leave open:
`failed` steps make the run red, `needs review` and `pending` make it neither red nor green and are listed. A run
that executed no step is a failure (a claim needs a step behind it).

`--batch <id>` and `--case <id>` narrow the run (a case keeps its batch's setup) and the results file records a
`selection` naming what was run, so a write-back can call it a partial run and never present it as the health of the
whole scenario; a full run carries no `selection`. A selector the script does not have is refused before the adapter is
asked anything. A **variant** selector waits for the supervised runner's variant hook (`squad.gui_test_variants` has
the in-process one, the supervised path does not), so no `--variant` is accepted yet: it would name a window mode
nothing switched.

A red run files **one** bug for the whole run through the MesenAI failure-report path (`scripts/report-bug.sh`, see
`squad.gui_test_report`), and only when `--file-bug` asks for it: filing is **opt-in**, so a test, acceptance or CI run
never pollutes the board (#1241). The bug is filed only for the registered emulator adapter — a run against a fake
adapter never touches GitHub. A bug that cannot be filed is recorded as `bug.filed=false` with the reason and never
aborts the run: the exit code reads the steps, never the filing.
"""
import argparse
import copy
import ctypes
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from squad.gui_test import ScriptError, validate  # noqa: E402
from squad.gui_test_report import (evidence_lock, evidence_store, file_bug_with,  # noqa: E402
                                   report_failures, upload_evidence)
from squad.gui_test_supervise import Crash, Hang, Session, run_supervised  # noqa: E402

RESULTS_FORMAT = "gui-test-results/1"
CAPABILITIES_TIMEOUT_S = 60
STEP_TIMEOUT_S = 180  # a launch is one call and waits for the application's window and hook
UNRESOLVED = ("needs review", "pending")
EXECUTED = ("passed", "failed", "needs review")  # `pending` is a step nobody ran, so it is not one that executed
ADAPTERS = {
    "mesen-gui": {
        "spec": "mesen_gui_adapter:MesenGuiAdapter",
        "env": ("MESENAI_ROOT", "MESEN_GUI_BINARY", "MESEN_CORE_LIB"),
        "repo": "sbihaiko/MesenAI",
    },
}


def platform_tag():
    return f"{sys.platform}-{platform.machine()}"


def build_sha(root):
    """The SHA of the build under test: the MesenAI checkout's HEAD, or "unknown" when there is none to read."""
    if not root:
        return "unknown"
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out or "unknown"


def env_problems(names):
    """Every problem with the environment the adapter needs; an empty list means it can run."""
    bad = []
    root = os.environ.get("MESENAI_ROOT", "")
    if "MESENAI_ROOT" in names and not (root and (Path(root) / "scripts" / "gui_test").is_dir()):
        bad.append(f"MESENAI_ROOT is not a MesenAI checkout holding scripts/gui_test: {root or '(unset)'}")
    binary = os.environ.get("MESEN_GUI_BINARY", "")
    if "MESEN_GUI_BINARY" in names and not (binary and Path(binary).is_file()):
        bad.append(f"MESEN_GUI_BINARY is not a file: {binary or '(unset)'}")
    lib = os.environ.get("MESEN_CORE_LIB", "")
    if "MESEN_CORE_LIB" in names:
        if not (lib and Path(lib).is_file()):
            bad.append(f"MESEN_CORE_LIB is not a file: {lib or '(unset)'}")
        else:
            try:  # the core must load, not merely exist: a stale or mis-built library fails here, not mid-run
                ctypes.CDLL(lib)
            except OSError as ex:
                bad.append(f"the native core {lib} cannot be loaded: {ex}")
    return bad


def capabilities(spec, timeout_s=CAPABILITIES_TIMEOUT_S):
    """Ask the adapter what it advertises, in a throwaway worker process: the class is built, the app is not."""
    with tempfile.TemporaryDirectory(prefix="gui-test-caps-") as tmp:
        session = Session(spec, {"out_dir": tmp}, timeout_s, Path(tmp) / "capabilities.log")
        try:
            return session.call("capabilities")
        finally:
            session.close()


def kind(verdict):
    """The group a verdict belongs to: what the summary counts and what the exit code reads."""
    if verdict.startswith("failed"):
        return "failed"
    return verdict if verdict in ("passed", "pending", *UNRESOLVED) else "not executed"


def rows_for(script, out):
    """One result per step, in script order, including the steps the supervised run never reported.

    A `manual` step is not sent to the adapter, so it has no result: it is `pending` — nobody ran it. An automated
    step that ran, passed and named no objective check was decided by nobody (there is no model here, item 8): it is
    `needs review`, never a silent pass.
    """
    seen = {r["id"]: r for r in out["results"]}
    rows = []
    for batch in script["batches"]:
        for s in batch["setup"] + batch["steps"] + batch["teardown"]:
            r = seen.get(s["id"])
            verdict = "pending" if r is None else r["verdict"]
            if verdict == "passed" and s["mode"] == "automated" and "check" not in s:
                verdict = "needs review"
            rows.append({"id": s["id"], "batch": batch["id"], "mode": s["mode"], "role": s.get("role"),
                         "expect": s.get("expect", ""), "verdict": verdict,
                         "capture": (r or {}).get("capture"), "held": bool(s.get("sensitive"))})
    return rows


def outcome(rows, batches):
    """(status, reasons): what makes the run red. `needs review` and `pending` never do, a failed batch does."""
    reasons = []
    executed = [r for r in rows if kind(r["verdict"]) in EXECUTED]
    if not executed:
        reasons.append("no step executed")
    failed = [r for r in rows if kind(r["verdict"]) == "failed"]
    if failed:
        reasons.append(f"{len(failed)} step(s) failed: " + ", ".join(r["id"] for r in failed))
    said = {r["batch"] for r in failed}
    for b in batches:  # a batch that failed for a reason no step of it carries: a crash, a hang, a bad precondition
        if b["verdict"].startswith("failed") and b["id"] not in said:
            reasons.append(f"batch {b['id']} {b['verdict']}" + (f" ({b['output'][-400:]})" if b.get("output") else ""))
    return ("failed" if reasons else "passed"), reasons


def summary_of(rows):
    counts = {"passed": 0, "failed": 0, "needs review": 0, "pending": 0, "not executed": 0}
    for r in rows:
        counts[kind(r["verdict"])] += 1
    return {**counts, "steps": len(rows),
            "executed": len(rows) - counts["pending"] - counts["not executed"]}


def attach_evidence(rows, repo, approvals_path, store=None, lock=None):
    """Give the run's failed and needs-review rows their evidence URL, or `withheld` when the gate holds the capture.

    With no `repo` nothing is published: a command a person runs keeps its captures local (ADR-0135 rule 5, `local_store`).
    With one, every push is serialized on the branch's lock, so two runs never collide on the evidence branch (#1244).
    """
    if not repo:
        return rows
    return upload_evidence(rows, store or evidence_store(repo), approvals_path,
                           lock_path=lock or evidence_lock(repo))


def local_store(path, name=None):
    """What the bug says about a capture off CI: the file's name, next to the results file this run wrote.

    Publishing a screenshot to an issue needs the target repo's evidence branch, and its first write needs the owner's
    grant (ADR-0135 rules 1 and 5), so a command a person runs never publishes one: the results file carries the path
    and the sha256, and the bug names the file. CI injects the evidence store (squad.gui_test_report.evidence_store).
    """
    return Path(path).name


def file_one_bug(script, rows, repo_root, approvals_path, store=None, runner=None):
    """File ONE bug listing every failed step (never one per step); return its URL."""
    file_bug = file_bug_with(str(repo_root), runner) if runner else file_bug_with(str(repo_root))
    return report_failures(script, rows, store or local_store, file_bug, Path(approvals_path), priority="P1")


def bug_reason(entry, rows, repo_root, asked):
    """Why this run files no bug, or None when it files one.

    Filing is opt-in (#1241): the default asks nobody, so a test, acceptance or CI run never leaves anything on the
    board. With `--file-bug` the run still files nothing for a fake adapter, for a green run, or when the repo's bug
    helper is not in the checkout — each named, so `bug.reason` always says why the file has no URL.
    """
    if not asked:
        return "no bug was asked for: filing is opt-in (--file-bug)"
    if not entry.get("repo"):
        return "the adapter is not the registered emulator adapter: a run against a fake adapter files nothing"
    if not any(kind(r["verdict"]) == "failed" for r in rows):
        return "no step failed"
    reporter = Path(repo_root) / "scripts" / "report-bug.sh"
    if not reporter.is_file():
        return f"{reporter} is missing"
    return None


def narrow(script, batch=None, case=None):
    """(the script the selectors name, the `selection` this run records); (script, None) when nothing was selected.

    A batch runs that batch alone; a case runs its batch's setup and teardown with that one step, because a case whose
    state its setup left is otherwise unrunnable. An id the script does not have raises `ScriptError` naming it, before
    the adapter is asked for its capabilities and before anything is launched.
    """
    if batch is None and case is None:
        return script, None
    ids = [b["id"] for b in script["batches"]]
    if batch is not None:
        if batch not in ids:
            raise ScriptError([f"no batch {batch!r} in {script['name']}: it has {', '.join(ids)}"])
        view = copy.deepcopy(script)
        view["batches"] = [b for b in view["batches"] if b["id"] == batch]
        return view, {"kind": "batch", "value": batch}
    steps = [(b["id"], s["id"]) for b in script["batches"] for s in b["setup"] + b["steps"]]
    if case not in [sid for _, sid in steps]:
        raise ScriptError([f"no case {case!r} in {script['name']}: it has {', '.join(sid for _, sid in steps)}"])
    holder = next(bid for bid, sid in steps if sid == case)
    view = copy.deepcopy(script)
    for b in view["batches"]:
        b["setup"], b["teardown"] = (b["setup"], b["teardown"]) if b["id"] == holder else ([], [])
        b["steps"] = [s for s in b["steps"] if s["id"] == case]
    view["batches"] = [b for b in view["batches"] if b["steps"]]
    return view, {"kind": "case", "value": case}


def report(record, path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(text)
    return record


def _failed(record, path, headline, reasons):
    record.update({"status": "failed", "reasons": reasons})
    print("gui test run: FAILED", file=sys.stderr)
    for reason in reasons:
        print(f"  {reason}", file=sys.stderr)
    return report(record, path, f"{headline} (no step ran)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gui_test_run_script.py", description=__doc__.splitlines()[0])
    ap.add_argument("script", help="the gui-test/1 JSON script to run")
    ap.add_argument("--adapter", default="mesen-gui",
                    help="a name in the adapter registry (mesen-gui) or a literal module:Class spec")
    ap.add_argument("--out", help="where to write the results JSON (default: ./gui-test-results/<name>-<run id>.json)")
    ap.add_argument("--artifacts", help="where the batch logs and captures go (default: <out>.artifacts)")
    ap.add_argument("--step-timeout", type=float, default=STEP_TIMEOUT_S,
                    help="host-time watchdog of one adapter call, in seconds")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--batch", help="run only this batch: a partial run, and the results file records the selection")
    sel.add_argument("--case", help="run only this case id, with the setup and teardown steps it needs")
    ap.add_argument("--evidence-repo", help="owner/name whose gui-test-evidence branch holds this run's failed and "
                                            "needs-review captures; without it nothing is published (ADR-0135)")
    ap.add_argument("--file-bug", action="store_true",
                    help="file the one bug a red run files through scripts/report-bug.sh; off by default, so a test, "
                         "acceptance or CI run never touches the board")
    args = ap.parse_args(argv)

    name = args.adapter
    if ":" in name:
        entry = {"spec": name, "env": (), "repo": None}
    elif name in ADAPTERS:
        entry = ADAPTERS[name]
    else:
        print(f"gui test run: FAILED\n  unknown adapter {name!r}; known: {', '.join(sorted(ADAPTERS))}", file=sys.stderr)
        return 1

    path = Path(args.script).expanduser()
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    try:
        script = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        print(f"gui test run: FAILED\n  cannot read the script {path}: {ex}", file=sys.stderr)
        return 1
    out = Path(args.out).expanduser() if args.out else \
        Path.cwd() / "gui-test-results" / f"{script.get('name', 'script') if isinstance(script, dict) else 'script'}-{run_id}.json"
    artifacts = Path(args.artifacts).expanduser() if args.artifacts else Path(str(out) + ".artifacts")
    record = {"format": RESULTS_FORMAT, "run_id": run_id, "adapter": entry["spec"], "adapter_name": name,
              "script": {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                         if path.is_file() else None},
              "platform": platform_tag(), "build_sha": build_sha(os.environ.get("MESENAI_ROOT", "")),
              "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": "failed", "reasons": [],
              "steps": [], "batches": [], "bug": {"filed": False, "reason": "the run did not start"}}
    if not isinstance(script, dict):
        return _failed(record, out, "gui test run", [f"{path} is not one JSON object"])
    try:
        validate(script)
    except ScriptError as ex:
        return _failed(record, out, "gui test run", ["the script is not a well-formed gui-test/1 file: " + p
                                                     for p in ex.problems])
    try:  # the selectors narrow before the capabilities are read: a refused id never asks the adapter anything
        script, selection = narrow(script, args.batch, args.case)
    except ScriptError as ex:
        return _failed(record, out, "gui test run",
                       ["nothing was launched: " + p for p in ex.problems])
    if selection is not None:  # a partial run says so; a full run carries no `selection` at all
        record["selection"] = selection
    problems = env_problems(entry["env"])
    if problems:
        return _failed(record, out, "gui test run", problems)
    try:
        caps = capabilities(entry["spec"])
    except (Crash, Hang) as ex:
        return _failed(record, out, "gui test run", [f"the adapter {name} did not answer its capabilities: {ex}"])
    try:
        validate(script, caps)
    except ScriptError as ex:
        return _failed(record, out, "gui test run",
                       ["the script names what the adapter does not advertise: " + p for p in ex.problems])

    print(f"gui test run {run_id}: {script['name']} on {name} ({platform_tag()})")
    out_dir = run_supervised(script, entry["spec"], {}, step_timeout_s=args.step_timeout, out_dir=artifacts)
    rows = attach_evidence(rows_for(script, out_dir), args.evidence_repo, artifacts / "approvals.json")
    record.update({"script": {**record["script"], "name": script["name"]},
                   "batches": out_dir["batches"], "steps": rows,
                   "summary": summary_of(rows)})
    status, reasons = outcome(rows, out_dir["batches"])
    record.update({"status": status, "reasons": reasons})
    bug = {"filed": False, "reason": bug_reason(entry, rows, os.environ.get("MESENAI_ROOT", ""), args.file_bug)}
    if bug["reason"] is None:
        try:  # a bug that could not be filed is recorded, never raised: the exit code reads the steps, not the filing
            bug = {"filed": True, "url": file_one_bug(script, rows, os.environ.get("MESENAI_ROOT", ""),
                                                      artifacts / "approvals.json"),
                   "reason": f"one bug for the run's {len([r for r in rows if kind(r['verdict']) == 'failed'])} failed step(s)"}
        except (subprocess.SubprocessError, OSError) as ex:
            bug = {"filed": False, "reason": f"report-bug.sh failed: {ex}"}
    record["bug"] = bug
    lines = [f"  {k}: {v}" for k, v in record["summary"].items()]
    lines += [f"  {r['id']}: {r['verdict']}" for r in rows if kind(r["verdict"]) in ("failed", *UNRESOLVED)]
    lines.append(f"  results: {out}")
    if bug["filed"]:
        lines.append(f"  bug: {bug['url']}")
    report(record, out, f"gui test run: {'RED' if status == 'failed' else 'GREEN'}\n" + "\n".join(lines))
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
