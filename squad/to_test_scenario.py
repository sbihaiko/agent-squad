"""The deterministic half of `/to-test-scenario` (issue #1224, spec #1223).

A manual scenario (Markdown, hand written, one case per row: id, precondition, steps, expected) and, when it exists,
its `gui-test/1` script. This module says what the script covers and builds the issue plan; publishing the plan and
writing a run's verdicts back are later slices, and they consume the plan as data. Nothing here talks to GitHub.

Coverage is by content, not only by id: the case's text (precondition, steps, expected) is hashed, and the conversion
records that hash on the step it produced. A case whose id is there but whose text changed is **stale** and goes back
to conversion for that case only, so an edited case is never silently ignored.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from squad.gui_test import validate

FORMAT = "gui-test/1"
HASH_FIELD = "content_hash"

PARENT_LABEL = "ready-for-agent"
CASE_LABEL = "test-case"

_SCRIPT_SUFFIXES = (".gui-test.json", ".gui-test")
_SEPARATOR = re.compile(r":?-{2,}:?")


class ScenarioError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


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
            "labels": [CASE_LABEL], "blocked_by": edges.get(case["id"]), "body": body}


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
    return {"kind": "batch", "id": batch, "title": f"Test case batch {batch}",
            "labels": [CASE_LABEL], "blocked_by": None, "body": body}


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
