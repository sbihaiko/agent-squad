"""The deterministic half of `/to-test-scenario` (issues #1224 and #1225, spec #1223).

A manual scenario (Markdown, hand written, one case per row: id, precondition, steps, expected) and, when it exists,
its `gui-test/1` script. This module says what the script covers, builds the issue plan, and publishes that plan to
GitHub; writing a run's verdicts back is the later slice, and it consumes the published map as data.

Coverage is by content, not only by id: the case's text (precondition, steps, expected) is hashed, and the conversion
records that hash on the step it produced. A case whose id is there but whose text changed is **stale** and goes back
to conversion for that case only, so an edited case is never silently ignored.

Publishing (#1225) is idempotent, resumable and paced. It takes one injected **GitHub runner** — a callable
`github(op, **kwargs)` whose ops are `ensure_label`, `create_issue`, `update_issue`, `add_sub_issue` and `board` —
and it writes a map beside the script (`<script>.gui-test.issues.json`: the parent's number, and per case id the
issue number and the case's content hash) after every issue it creates. An interrupted publish, or a second machine,
resumes from that map and creates nothing twice; publishing again updates what the map knows, and a case the script
no longer covers is reported in `removed` and left open, because only a person closes a case. A secondary rate limit
is not a failure: the runner raises `RateLimited` and the publish waits it out (the API's own `retry_after` when it
gives one, an exponential back-off otherwise) and retries the same call. `rest_runner` builds the real runner on
`gh api` (REST, never GraphQL: a scenario publishes hundreds of sub-issues).
"""
import copy
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from squad.gui_test import validate

FORMAT = "gui-test/1"
HASH_FIELD = "content_hash"

PARENT_LABEL = "ready-for-agent"
CASE_LABEL = "test-case"

MAP_SUFFIX = ".gui-test.issues.json"

_SCRIPT_SUFFIXES = (".gui-test.json", ".gui-test")
_SEPARATOR = re.compile(r":?-{2,}:?")


class ScenarioError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class RateLimited(Exception):
    """The API answered with a secondary rate limit: the runner raises this, and a publish waits and retries."""

    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__("secondary rate limit" + (f", retry after {retry_after}s" if retry_after else ""))


def case_hash(precondition, steps, expected):
    """The content hash of one case: its text with whitespace collapsed, so reflowing a row is not an edit."""
    text = "\n".join(" ".join(str(part).split()) for part in (precondition, steps, expected))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells):
    return bool(cells) and all(_SEPARATOR.fullmatch(c) for c in cells)


def _columns(header):
    """Where each case field sits in a table, by header name.

    `ID`, `Precondition` and `Expected` are named; the steps column is the one that says so, and when a table has no
    such column (the Markdown rendered from a script names the cells instead) the columns between Precondition and
    Expected carry the steps.
    """
    low = [h.lower() for h in header]
    if "id" not in low or "precondition" not in low:
        return None
    where = {"id": low.index("id"), "precondition": low.index("precondition")}
    expected = next((i for i, h in enumerate(low) if "expected" in h), None)
    if expected is None:
        return None
    where["expected"] = expected
    steps = next((i for i, h in enumerate(low) if "step" in h), None)
    where["steps"] = list(range(where["precondition"] + 1, expected)) if steps is None else [steps]
    return where


def _case(where, cells):
    if max(max(where["steps"]), where["expected"], where["id"]) >= len(cells):
        return None
    cid = cells[where["id"]].strip().strip("`")
    if not cid:
        return None
    steps = " · ".join(cells[i] for i in where["steps"] if cells[i])
    return {"id": cid, "precondition": cells[where["precondition"]], "steps": steps,
            "expected": cells[where["expected"]]}


def _default_name(path):
    name = Path(path).name
    for suffix in _SCRIPT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(path).stem


def read_scenario(path, name=None):
    """Read a manual scenario Markdown; return {name, path, cases: [{id, precondition, steps, expected, hash}]}.

    Only tables that name every case field are read, so the sheet, the pass matrix and the prose around them are
    ignored.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    cases, where = [], None
    for pos, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            where = None
            continue
        cells = _cells(line)
        if where is None:
            if pos + 1 < len(lines) and _is_separator(_cells(lines[pos + 1])):
                where = _columns(cells)
            continue
        if _is_separator(cells):
            continue
        case = _case(where, cells)
        if case:
            cases.append(case)
    return {"name": name or _default_name(path), "path": str(path), "cases": cases}


def read_script(path):
    """Read a `gui-test/1` script; None when the path holds no script, a ScriptError when it is not one."""
    path = Path(path)
    if not path.exists():
        return None
    script = json.loads(path.read_text(encoding="utf-8"))
    validate(script)
    return script


def script_steps(script):
    """Every step of the script in run order — setup, then steps, then teardown, batch by batch — tagged with its batch."""
    out = []
    for batch in script["batches"]:
        for part in ("setup", "steps", "teardown"):
            for s in batch.get(part, []):
                out.append(dict(s, batch=batch["id"], part=part))
    return out


def coverage(scenario, script):
    """What the script covers of the scenario: gaps both ways, stale cases, and whether it may be reused.

    A case is covered when the script has a step with its id; it is fresh when that step records the case's hash exactly
    as the Markdown has it now. A step that records no hash is stale: nothing says which version of the case it was
    converted from.
    """
    steps = script_steps(script)
    by_id = {s["id"]: s for s in steps}
    covered, missing, stale = [], [], []
    for case in scenario["cases"]:
        s = by_id.get(case["id"])
        if s is None:
            missing.append(case["id"])
            continue
        covered.append(case["id"])
        if s.get(HASH_FIELD) != case_hash(case["precondition"], case["steps"], case["expected"]):
            stale.append(case["id"])
    case_ids = {c["id"] for c in scenario["cases"]}
    unknown = [s["id"] for s in steps if s["id"] not in case_ids]
    return {"reusable": not missing and not stale and not unknown,
            "covered": covered, "missing_steps": missing, "steps_without_case": unknown, "stale": stale,
            "total_cases": len(scenario["cases"]), "total_steps": len(steps)}


def record_hashes(scenario, script):
    """The script with every step's content hash taken from the scenario case of its id (a copy; the input is untouched)."""
    by_case = {c["id"]: c for c in scenario["cases"]}
    out = copy.deepcopy(script)
    for batch in out["batches"]:
        for part in ("setup", "steps", "teardown"):
            for s in batch.get(part, []):
                case = by_case.get(s["id"])
                if case:
                    s[HASH_FIELD] = case_hash(case["precondition"], case["steps"], case["expected"])
    return out


# --- the issue plan (publishing is a later slice; this is data) ----------------------------------

def _variants_text(step):
    variants = step.get("variants") or {}
    return ", ".join(f"{axis}={value}" for axis, value in variants.items()) if variants else "all"


def _edge_text(blocked_by, batch):
    if not blocked_by:
        return "- None (can start immediately)."
    return f"- Case `{blocked_by}` (the setup case of batch `{batch}`)."


def _setup_edges(steps):
    """The case each step waits for: the setup step before it, and the batch's last setup step for everything else."""
    edges, setup_of_batch, previous, previous_batch = {}, {}, None, None
    for s in steps:
        if s["batch"] != previous_batch:
            previous, previous_batch = None, s["batch"]
        if s["part"] == "setup":
            edges[s["id"]] = previous
            previous = s["id"]
            setup_of_batch[s["batch"]] = s["id"]
        else:
            edges[s["id"]] = setup_of_batch.get(s["batch"])
    return edges


def _case_issue(case, step, edges):
    body = "\n".join([
        "## Case", "",
        f"- Id: `{case['id']}`",
        f"- Batch: `{step['batch']}`",
        f"- Role: `{step['role']}`",
        f"- Mode: `{step['mode']}`",
        f"- Variants: {_variants_text(step)}", "",
        "## Precondition", "",
        case["precondition"] or "None.", "",
        "## Steps", "",
        case["steps"] or "None.", "",
        "## Expected result", "",
        case["expected"] or "None.", "",
        "## Blocked by", "",
        _edge_text(edges.get(case["id"]), step["batch"]), "",
    ])
    return {"kind": "case", "id": case["id"], "title": f"Test case {case['id']}",
            "labels": [CASE_LABEL], "blocked_by": edges.get(case["id"]), "body": body,
            "hash": step.get(HASH_FIELD)}


def _batch_issue(batch, steps, edges):
    own = [s for s in steps if s["batch"] == batch]
    rows = "\n".join(f"| `{s['id']}` | `{s['role']}` | `{s['mode']}` | {_variants_text(s)} |" for s in own)
    body = "\n".join([
        "## Batch", "",
        f"- Id: `{batch}`",
        f"- Cases: {len(own)}", "",
        "| Case | Role | Mode | Variants |",
        "|---|---|---|---|",
        rows, "",
        "## Blocked by", "",
        _edge_text(None, batch), "",
    ])
    hashes = "".join(s.get(HASH_FIELD) or "" for s in own)
    return {"kind": "batch", "id": batch, "title": f"Test case batch {batch}",
            "labels": [CASE_LABEL], "blocked_by": None, "body": body,
            "hash": hashlib.sha256(hashes.encode("utf-8")).hexdigest() if hashes else None}


def _parent_body(scenario, script, steps, edges):
    fixtures = script.get("fixtures") or {}
    variants = script.get("variants") or {}
    rows = "\n".join(f"| `{s['id']}` | `{s['batch']}` | `{s['mode']}` | "
                     f"{'`' + edges[s['id']] + '`' if edges.get(s['id']) else 'None'} |" for s in steps)
    return "\n".join([
        f"Run every case of the manual scenario **{scenario['name']}** on the squad's GUI test infrastructure.",
        "A person reads the results on the case issues; the run itself is a separate command.", "",
        "## Script", "",
        f"- Name: `{script['name']}`",
        f"- Format: `{script['format']}`",
        f"- Target: `{script['target']}`", "",
        "## Fixtures", "",
        "```json", json.dumps(fixtures, indent=2, sort_keys=True), "```", "",
        "## Variants", "",
        "\n".join(f"- `{axis}`: " + ", ".join(f"`{value}`" for value in values)
                  for axis, values in variants.items()) or "None.", "",
        "## Cases", "",
        "| Case | Batch | Mode | Blocked by |",
        "|---|---|---|---|",
        rows, "",
        "## Blocked by", "",
        "- None (can start immediately).", "",
    ])


def build_plan(scenario, script, *, granularity="case", wave="01"):
    """The issue plan of a scenario the script covers: one parent and one issue per case (or per batch), in run order.

    Raises ScenarioError when the script does not cover the scenario exactly — a case with no step, a step with no
    case, or a stale case (its text changed since the conversion) goes back to conversion first.
    """
    if granularity not in ("case", "batch"):
        raise ScenarioError([f"unknown granularity {granularity!r}"])
    report = coverage(scenario, script)
    problems = []
    for key, label in (("missing_steps", "cases with no step"), ("steps_without_case", "steps with no case"),
                       ("stale", "stale cases")):
        if report[key]:
            problems.append(f"{label}: " + ", ".join(report[key]))
    if problems:
        raise ScenarioError(problems)
    steps = script_steps(script)
    edges = _setup_edges(steps)
    by_case = {c["id"]: c for c in scenario["cases"]}
    if granularity == "case":
        issues = [_case_issue(by_case[s["id"]], s, edges) for s in steps]
    else:
        issues = [_batch_issue(batch["id"], steps, edges) for batch in script["batches"]]
    return {"scenario": scenario["name"], "granularity": granularity,
            "parent": {"title": f"Test scenario: {scenario['name']}",
                       "labels": [PARENT_LABEL, f"wave:{wave}"],
                       "body": _parent_body(scenario, script, steps, edges)},
            "issues": issues}


# --- publishing the plan, idempotently, resumably and paced (#1225) -------------------------------

def issues_path(script_path):
    """The map of a script's issues, beside the script: `<script>.gui-test.issues.json`.

    The script keeps its own name without its format suffix, so `play-pad-only.gui-test.json` maps to
    `play-pad-only.gui-test.issues.json` — one committed file per script, and no state inside the script itself.
    """
    path = Path(script_path)
    name = path.name
    for suffix in _SCRIPT_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    else:
        name = Path(name).stem
    return path.with_name(name + MAP_SUFFIX)


def read_issues(path):
    """The map beside the script, or an empty one: the parent's number, whether it is on the board, and per case."""
    if not path:
        return {"parent": None, "boarded": False, "cases": {}}
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"parent": None, "boarded": False, "cases": {}}
    return {"parent": saved.get("parent"), "boarded": bool(saved.get("boarded")),
            "cases": {key: {"number": value.get("number"), "hash": value.get("hash")}
                      for key, value in (saved.get("cases") or {}).items()}}


def publish(plan, github, *, script_path=None, map_path=None, saved=None, sleep=None, attempts=4, backoff=1.0):
    """Publish the plan: one parent, one natively linked sub-issue per case, and only the parent on the board.

    Idempotent and resumable through the map beside the script, flushed after every issue is created: a case the map
    knows is updated, never created twice, so an interrupted publish — or another machine — resumes from it. A case
    the map knows and the plan no longer covers is reported in `removed` and stays open. Creations are paced: a
    secondary rate limit (`RateLimited` from the runner) is waited out and retried, never a failure.
    """
    if script_path is None and map_path is None and saved:
        path = None
    else:
        path = Path(map_path) if map_path else (issues_path(script_path) if script_path else None)
    state = read_issues(path) if saved is None else {
        "parent": saved.get("parent"), "boarded": bool(saved.get("boarded")),
        "cases": {key: dict(value) for key, value in (saved.get("cases") or {}).items()}}
    wait = sleep or time.sleep
    call = _paced(github, wait=wait, attempts=attempts, backoff=backoff)

    def flush():
        if path:
            Path(path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues, created, updated = {}, [], []
    for label in _labels(plan):
        call("ensure_label", name=label)
    if state["parent"]:
        call("update_issue", number=state["parent"], title=plan["parent"]["title"],
             body=plan["parent"]["body"], labels=plan["parent"]["labels"])
        updated.append("parent")
    else:
        made = call("create_issue", title=plan["parent"]["title"], body=plan["parent"]["body"],
                    labels=plan["parent"]["labels"])
        state["parent"] = made["number"]
        flush()                     # known from here on, so an interrupt below never buys a second parent
    if not state["boarded"]:
        call("board", number=state["parent"])
        state["boarded"] = True
        flush()
    for issue in plan["issues"]:
        key = issue["id"]
        if key in state["cases"]:
            number = state["cases"][key]["number"]
            call("update_issue", number=number, title=issue["title"], body=issue["body"], labels=issue["labels"])
            updated.append(key)
        else:
            made = call("create_issue", title=issue["title"], body=issue["body"], labels=issue["labels"])
            number = made["number"]
            call("add_sub_issue", parent=state["parent"], sub=made["id"])   # the numeric id, never the number
            created.append(key)
        state["cases"][key] = {"number": number, "hash": issue.get("hash")}
        issues[key] = dict(state["cases"][key])
        flush()
    return {"parent": state["parent"], "boarded": state["boarded"], "cases": issues, "created": created,
            "updated": updated, "removed": [k for k in state["cases"] if k not in issues],
            "map_path": str(path) if path else None, "map": state}


def _labels(plan):
    """Every label the plan's issues need, the case label included, whatever the granularity."""
    out = {CASE_LABEL, *plan["parent"]["labels"]}
    for issue in plan["issues"]:
        out.update(issue["labels"])
    return sorted(out)


def _paced(github, *, wait, attempts, backoff):
    """Every runner call goes through here, so a rate limit delays a publish instead of failing it."""
    def call(op, **kwargs):
        delay = backoff
        for attempt in range(1, attempts + 1):
            try:
                return github(op, **kwargs)
            except RateLimited as limited:
                if attempt == attempts:
                    raise
                wait(limited.retry_after or delay)
                delay *= 2
    return call


# --------------------------------------------------------------------------- the real GitHub runner


def _sh(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def _rate_limit(exc):
    """A `gh` failure that is a secondary rate limit: a back-off, not an error the caller must handle."""
    text = ((exc.stderr or "") + (exc.stdout or "")).lower()
    return RateLimited() if "rate limit" in text or "secondary rate" in text else None


def rest_runner(repo, shell=_sh, project=None):
    """The real GitHub runner: REST through `gh api`, plus the repo's own label and board helpers.

    `project` is the board the parent is added to, e.g. `users/sbihaiko/projects/3`; without it the board op is a
    no-op, so a fork or a dry environment publishes issues without touching a board. Sub-issues are linked with the
    issue's numeric **id** (`sub_issue_id`), which is what the REST relation takes.
    """
    def call(args):
        try:
            return shell(args)
        except subprocess.CalledProcessError as exc:
            limited = _rate_limit(exc)
            if limited is not None:
                raise limited from exc
            raise

    def api(method, path, fields=(), json_out=False):
        args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"] if method != "GET" \
            else ["gh", "api", f"repos/{repo}/{path}"]
        for key, value in fields:
            args += ["-f", f"{key}={value}"]
        out = call(args)
        return json.loads(out) if json_out else out

    def gh(op, **kwargs):
        if op == "ensure_label":
            return call(["gh", "label", "create", kwargs["name"], "--force", "--repo", repo]) and {}
        if op == "create_issue":
            fields = [("title", kwargs["title"]), ("body", kwargs["body"])] + \
                     [("labels[]", label) for label in kwargs["labels"]]
            return api("POST", "issues", fields, json_out=True)
        if op == "update_issue":
            fields = [("title", kwargs["title"]), ("body", kwargs["body"])] + \
                     [("labels[]", label) for label in kwargs["labels"]]
            return api("PATCH", f"issues/{kwargs['number']}", fields, json_out=True)
        if op == "add_sub_issue":
            return api("POST", f"issues/{kwargs['parent']}/sub_issues",
                       [("sub_issue_id", kwargs["sub"])], json_out=True)
        if op == "board":
            if not project:
                return {}
            url = f"https://github.com/{repo}/issues/{kwargs['number']}"
            return json.loads(call(["gh", "project", "item-add", project, "--url", url, "--format", "json"]))
        raise ValueError(f"unknown op {op!r}")
    return gh
