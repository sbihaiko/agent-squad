"""The subject run harness: one issue of the subject project, run through the engine.

The subject is a plain git repository. A run never writes it: `prepare` clones it into the run directory and the
engine lands its merge in the clone. The held-out acceptance test stays in `bench/subject/` and reaches the
clone only through the ac-verify command, after the actor has finished.

A blocked issue: `prepare` applies each blocker's accepted merge to the new clone, in dependency order.
A blocker counts when `runs_dir` holds a run record of it with the verdict `completed`. The merge commit is read from
that record (the passing `merge` outcome's effect head), never from a model's claim, and it is applied from the
blocker's own clone by a cherry-pick against its first parent. A blocker with no such record refuses the issue and
the refusal names it. The fixture and the subject are never edited.
"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "subject.yaml"
SUITE = "python3 -m pytest -q"

# The sections of config/subject.yaml and the keys each one must name. A key is never defaulted in code: the file is
# the single place a model is chosen, so a change of model is a change of one file, reviewed like any other.
SECTIONS = {"issue": ("model", "effort"), "router": ("model", "effort", "child_model", "child_effort")}


class Refused(Exception):
    pass


def load_config(path=CONFIG):
    """The models a subject run uses, read from config/subject.yaml. Return {"issue": {...}, "router": {...}}.

    A missing file, a missing section or a missing key refuses: a run must never guess a model. The owner rules the
    model (2026-10-02: claude-sonnet-5-5 only), so the file is the one place that ruling lives.
    """
    p = Path(path)
    if not p.exists():
        raise Refused(f"no subject config at {p}")
    try:
        doc = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise Refused(f"{p} is not valid YAML: {e}") from e
    out = {}
    for section, keys in SECTIONS.items():
        blk = doc.get(section)
        if not isinstance(blk, dict):
            raise Refused(f"{p} has no `{section}` section")
        missing = [k for k in keys if not blk.get(k)]
        if missing:
            raise Refused(f"{p}: `{section}` names no {', '.join(missing)}")
        out[section] = {k: blk[k] for k in keys}
        if "spend_cap" in blk:
            out[section]["spend_cap"] = blk["spend_cap"]
    return out


def apply_router_models(doc, cfg):
    """Point the router graph at the configured models, in place. Return the same document.

    Two changes. The agent that asks for children takes the configured router model. The dispatch envelope's
    `models` becomes the configured child model alone, so the envelope rejects a child that asks for anything else
    (`model_outside_allow`, squad/envelope.py:47) and defaults an omitted model to it (squad/envelope.py:33). The
    configured child effort joins the allowed efforts; the other efforts stay, because the ruling is about the model.
    """
    r = cfg["router"]
    for n in doc["nodes"]:
        if n.get("kind") == "agent" and n.get("may_spawn"):
            n["model"], n["effort"] = r["model"], r["effort"]
        elif n.get("kind") == "dispatch":
            allow = n.setdefault("allow", {})
            allow["models"] = [r["child_model"]]
            allow["efforts"] = list(dict.fromkeys([*allow.get("efforts", []), r["child_effort"]]))
    return doc


def no_spend_cap(cfg):
    """True when config/subject.yaml sets `router.spend_cap: off`: the run sets no spend cap at any level."""
    v = cfg["router"].get("spend_cap")      # YAML reads a bare `off` as False
    return v is False or str(v).lower() == "off"


def spawn_example(cfg):
    """The JSON example the router copies when it asks for a child, with the configured child model and effort.
    The router reads it literally, so the model in this example is the model the children run."""
    r = cfg["router"]
    ex = {"prompt": "...", "skill": "tdd", "task": "14", "model": r["child_model"], "effort": r["child_effort"],
          "tools": ["Read"], "stalls": 4, "spend": 1.0, "deadline": 900, "after": []}
    if no_spend_cap(cfg):
        del ex["spend"]      # a child with no `spend` gets the full limit, and the limit is uncapped
    return '{"status": "pass", "spawns": [' + json.dumps(ex) + "]}"


@dataclass
class Prepared:
    issue: int
    issue_file: str            # relative to the project root
    target: Path               # the clone the run works in
    graph_path: Path
    run_dir: Path
    ac_commands: list
    chain: list = field(default_factory=list)   # the blockers applied to the clone, in order: issue, run, merge commit


def _agent(name, tools, output_key, instructions, model, effort, *, workspace=None, stalls=(2, 5), spend=(0.05, 2.0), deadline=900):
    n = {"name": name, "kind": "agent", "model": model, "effort": effort, "tools": tools, "output_key": output_key,
         "instructions": instructions, "budget": {"stalls": {"min": stalls[0], "max": stalls[1]}, "spend": {"min": spend[0], "max": spend[1]}},
         "deadline": deadline, "next": {"fail": "repeat"}}
    if workspace:
        n["workspace"] = workspace
    return n


def graph_doc(issue_file, ac_commands, cfg=None):
    """The five-phase shape on a fixed issue: spec, a worktree, work with gates, a merge, then verify."""
    cfg = cfg or load_config()
    model, effort = cfg["issue"]["model"], cfg["issue"]["effort"]
    spec = _agent("spec_draft", ["Read", "Grep", "Glob"], "spec",
                  f"Read {issue_file} and CLAUDE.md in this repository. Do not change any file. Reply with one JSON object in a ```json fence: "
                  '{"status": "pass", "spec": {"summary": "<one paragraph>", "ac_commands": ' + json.dumps(ac_commands) + "}}. "
                  "Copy ac_commands exactly.", model, effort, workspace=".", stalls=(2, 5))
    actor = _agent("actor", ["Read", "Edit", "Write", "Bash", "Glob", "Grep"], "diff",
                   f"Implement the issue in {issue_file}. Your working directory is the project. Follow CLAUDE.md. "
                   f"Write a test for each acceptance criterion and run `{SUITE}` until it passes. Do not run git commit: the harness commits. "
                   'When done, reply with one JSON object in a ```json fence: {"status": "pass", "diff": {"files": [{"path": "<changed file>", "generated": false}]}} '
                   'listing every file you changed. If you cannot finish, reply {"status": "fail", "reasons": ["<why>"]}.',
                   model, effort, workspace="worktrees/item", stalls=(2, 5), spend=(0.2, 2.0), deadline=1800)
    return {
        "schema_version": 1, "name": "subject-issue",
        "description": "One subject issue through the five-phase shape: spec, work in a worktree behind scope and security gates, merge, verify.",
        "run": {"deadline": 5400, "max_parallel": 1, "spend_cap": 10},
        "facts": [{"name": "git", "type": "bool", "instrument": "code: git is available in the target", "source_kind": "target"}],
        "gates": [{"id": "spec-shape", "params": []}, {"id": "scope", "params": ["allowed_generated"]}, {"id": "security", "params": ["sensitive_paths"]},
                  {"id": "ac-verify", "params": ["commands_from"]}],
        "root": "main",
        "nodes": [
            {"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["spec", "worktree", "work", "merge", "verify"]},
            {"name": "spec", "kind": "loop", "max": {"value": 3, "min": 1}, "children": ["spec_draft", "spec_gate"]},
            spec,
            {"name": "spec_gate", "kind": "gate", "check": "spec-shape", "params": {}, "next": {"fail": "repeat"}},
            {"name": "worktree", "kind": "action", "effect": "worktree_create", "workspace": "worktrees/item"},
            {"name": "work", "kind": "loop", "max": {"value": 2, "min": 1}, "children": ["actor", "scope_gate", "security_gate"]},
            actor,
            {"name": "scope_gate", "kind": "gate", "check": "scope", "params": {"allowed_generated": []}, "next": {"fail": "repeat"}},
            {"name": "security_gate", "kind": "gate", "check": "security", "params": {"sensitive_paths": [".env*", "auth", "crypto"]}, "next": {"fail": "repeat"}},
            {"name": "merge", "kind": "action", "effect": "merge_land", "workspace": "worktrees/item", "deadline": 300, "next": {"fail": "exit:fail"}},
            {"name": "verify", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["ac_verify"]},
            {"name": "ac_verify", "kind": "gate", "check": "ac-verify", "params": {"commands_from": "spec"}, "next": {"fail": "exit:fail"}},
        ],
    }


def _issue_file(root, n):
    hits = sorted((Path(root) / "issues").glob(f"{n:02d}-*.md"))
    if not hits:
        raise Refused(f"no issue file for #{n:02d} in {root}/issues")
    return hits[0]


def _closed(root, n):
    try:
        return re.search(r"^Status:\s*closed\s*$", _issue_file(root, n).read_text(), re.M) is not None
    except Refused:
        return False


def _vcs(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=agent-squad", "-c", "user.email=squad@localhost", *args], cwd=cwd, capture_output=True, text=True)


def completed_run(runs_dir, n):
    """The newest completed run of issue n under runs_dir, as {issue, run, target, merge}, or None.
    Completed means: a `verdict` event `completed`, and a passing `merge` outcome whose head commit exists in the run's clone."""
    if runs_dir is None:
        return None
    from squad import record
    for d in sorted(Path(runs_dir).glob(f"subject-{n:02d}-*"), reverse=True):
        try:
            events = record.read(d / "record.jsonl")
        except Exception:  # noqa: BLE001 an unreadable or partial record is not a completed run
            continue
        if not any(e["event"] == "verdict" and e.get("verdict") == "completed" for e in events):
            continue
        merges = [e for e in events if e["event"] == "outcome" and e["node"] == "merge" and e["outcome"] == "pass"]
        head = merges[-1].get("effect", {}).get("head") if merges else None
        target = d / "target"
        if head and _vcs(target, "cat-file", "-e", f"{head}^{{commit}}").returncode == 0:
            return {"issue": n, "run": str(d), "target": target, "merge": head}
    return None


def _closure(rows, issue):
    """Every transitive blocker of the issue, blockers first (a depth-first post-order, so a blocker follows its own blockers)."""
    by = {r["issue"]: r for r in rows}
    order = []

    def visit(n):
        for b in by.get(n, {}).get("blocked_by", []):
            if b not in order:
                visit(b)
                order.append(b)
    visit(issue)
    return order


def _apply(clone, c):
    fetched = _vcs(clone, "fetch", "-q", str(c["target"]), c["merge"])
    if fetched.returncode:
        raise Refused(f"blocker {c['issue']}: cannot fetch its merge {c['merge']}: {fetched.stderr.strip()[-200:]}")
    parents = _vcs(clone, "rev-list", "--parents", "-n", "1", c["merge"]).stdout.split()[1:]
    r = _vcs(clone, "cherry-pick", *(["-m", "1"] if len(parents) > 1 else []), c["merge"])
    if r.returncode:
        _vcs(clone, "cherry-pick", "--abort")
        raise Refused(f"blocker {c['issue']}: its merge {c['merge']} does not apply: {(r.stdout + r.stderr).strip()[-200:]}")
    return _vcs(clone, "rev-parse", "HEAD").stdout.strip()


def prepare(issue, subject, bench, out_dir, runs_dir=None, cfg=None, holds=()):
    subject, bench, out = Path(subject).resolve(), Path(bench).resolve(), Path(out_dir).resolve()   # a relative path would break once a git call runs in another folder
    runs_dir = Path(runs_dir).resolve() if runs_dir is not None else None
    rows = json.loads((bench / "issues.json").read_text())
    row = next((r for r in rows if r["issue"] == issue), None)
    if row is None:
        raise Refused(f"issue {issue} is not in {bench}/issues.json")
    chain, missing = [], []
    for b in _closure(rows, issue):
        if b in holds or _closed(subject, b):   # `holds`: issues the subject tree already carries (a chained run, #156)
            continue
        run = completed_run(runs_dir, b)
        if run:
            chain.append(run)
        else:
            missing.append(b)
    if missing:
        raise Refused(f"issue {issue} is blocked by issues with no completed run {missing}")
    held = (bench / row["acceptance_test"]).resolve()
    if out.exists():
        raise Refused(f"{out} exists")
    out.mkdir(parents=True)
    target = out / "target"
    subprocess.run(["git", "clone", "-q", str(subject), str(target)], check=True)
    try:
        for c in chain:
            c["applied"] = _apply(target, c)
    except Refused:
        shutil.rmtree(out)
        raise
    chain = [{**c, "target": str(c["target"])} for c in chain]
    if holds:
        (out / "holds.json").write_text(json.dumps({"held": sorted(holds)}))   # the dashboard's strip counts them
    if chain:
        (out / "chain.json").write_text(json.dumps(chain, indent=1))
    rel = _issue_file(target, issue).relative_to(target).as_posix()
    cmds = [f"cp {held} tests/api/test_zz_heldout_{issue:02d}.py && {SUITE}"]
    gp = out / "graph.json"
    gp.write_text(json.dumps(graph_doc(rel, cmds, cfg), indent=1))
    from squad import graph
    graph.load(gp)   # a graph defect stops here, before any spend
    return Prepared(issue, rel, target, gp, out, cmds, chain)   # the record sits in the run folder itself: `squad resume <folder>` needs no --run-dir


@dataclass
class PreparedSet:
    issues: list               # the issues of the run, blockers first
    issue_files: dict          # issue -> its file, relative to the project root
    blocked_by: dict           # issue -> the blockers that are also in the set
    ac_commands: dict          # issue -> the held-out command: copy its test into the clone, then the suite
    target: Path
    run_dir: Path


def _held_command(test, n):
    """Copy one held-out test in, run the suite, and take the copy out again, so a failing test never leaks into the next command."""
    dest = f"tests/api/test_zz_heldout_{n:02d}.py"
    return f"cp {Path(test).resolve()} {dest} && {{ {SUITE}; s=$?; rm -f {dest}; exit $s; }}"


def prepare_set(issues, subject, bench, out_dir):
    """One clone for a set of issues, one run. A blocker outside the set must be closed in the subject: the clone holds it
    already, and a run never chains another run's merge here. The held-out tests stay in `bench` until a command copies them in."""
    subject, bench, out = Path(subject), Path(bench), Path(out_dir)
    rows = {r["issue"]: r for r in json.loads((bench / "issues.json").read_text())}
    unknown = [n for n in issues if n not in rows]
    if unknown:
        raise Refused(f"issues {unknown} are not in {bench}/issues.json")
    open_blockers = sorted({b for n in issues for b in rows[n].get("blocked_by", []) if b not in issues and not _closed(subject, b)})
    if open_blockers:
        raise Refused(f"the issues {sorted(issues)} are blocked by open issues outside the set {open_blockers}")
    ordered = []
    for n in sorted(issues):
        for b in [*_closure(list(rows.values()), n), n]:
            if b in issues and b not in ordered:
                ordered.append(b)
    if out.exists():
        raise Refused(f"{out} exists")
    out.mkdir(parents=True)
    target = out / "target"
    subprocess.run(["git", "clone", "-q", str(subject), str(target)], check=True)
    files = {n: _issue_file(target, n).relative_to(target).as_posix() for n in ordered}
    cmds = {n: _held_command(bench / rows[n]["acceptance_test"], n) for n in ordered}
    return PreparedSet(ordered, files, {n: [b for b in rows[n].get("blocked_by", []) if b in issues] for n in ordered}, cmds, target, out)


def _landed_issues(events):
    """The issues a landed child delivered, by the child's `task` (several allowed: "13, 21") or, when it left `task` empty, by the issue file its prompt names."""
    by_child = {}
    for e in events:
        if e["event"] == "spawn_accepted":
            d = e.get("definition") or {}
            task = str(d.get("task") or "")
            named = re.findall(r"\b\d+\b", task) or re.findall(r"(\d\d)-[\w-]+\.md", str(d.get("prompt") or ""))
            by_child[e["child"]] = {str(int(x)) for x in named}
    done = set()
    for e in events:
        if e["event"] == "land" and e["outcome"] == "pass" and (e.get("effect") or {}).get("held_out") != "fail":
            done |= by_child.get(e["node"], set())
    return done


def passing_on_tree(commands, target):
    """The issues whose held-out command passes on the final tree (GLOSSARY: Delivered issue). `commands` is {issue: shell command}."""
    from squad import workcmd
    return {n for n, cmd in commands.items() if workcmd.run(cmd, str(target)).ok}


def undelivered(events, issues, passing=None):
    """The issues of a set that are not delivered. With `passing` (the issues whose held-out test passes on the final tree) that test decides:
    a router that left `task` empty cannot make a delivered issue read undelivered. Without it, a landed child with a held-out result that is not
    `fail` decides, found on run-20261004-012737: a router that gave up on an issue still ends `completed`."""
    done = {int(x) for x in passing} if passing is not None else {int(x) for x in _landed_issues(events)}
    return [n for n in issues if n not in done]


def delivery_mismatches(events, issues, passing):
    """Issues whose held-out test passes on the final tree with no landed child behind them: the test passed on the base tree, or a child
    landed work the record does not attribute. The `land` event stays the record of which child delivered an issue."""
    landed = _landed_issues(events)
    return [n for n in issues if n in passing and str(n) not in landed]
