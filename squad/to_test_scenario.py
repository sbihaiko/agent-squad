"""`/to-test-scenario`: a manual GUI test scenario becomes a script, tracked issues and a run whose verdicts land back.

Everything here is deterministic: reading the case ids of the Markdown and the step ids of the `gui-test/1` script,
reporting the gap between them, building the issue **plan** (parent, sub-issues, labels, `## Blocked by`), and
turning a run's per-step results into sub-issue comments, closes and at most one bug. It also decides the coverage of
the scenario by content hash plus the scenario's own issue plan (MesenAI #1223, MesenAI #1224). `write_back`, the
command of MesenAI #1226, reads a run's results file and writes every verdict back to the case's issue: comment,
close, reopen, and the link to the one bug the run opened. Converting prose into steps is
model judgment and lives in the skill, not here.

Two outside dependencies are injected, and they are the only ones this module has:

- **the GitHub runner** — a callable `runner(op, **kwargs)`; the ops are `ensure_label`, `create_issue`,
  `update_issue`, `add_sub_issue`, `remove_sub_issue`, `list_sub_issues`, `set_title`, `issue_body`,
  `search_open_bug`, `comment`, `close`, `reopen`, `issue_state`, `board` and `file_bug`. `rest_runner`
  builds the real one on `gh api` (REST, except `item_status`, which reads GraphQL: a scenario publishes dozens of
  sub-issues). This module reads
  `issue_state` to know whether a verdict changed and whether a case is closed, and to read a record's numeric `id`
  when the map holds none (#1565), and it **never** calls `file_bug`: the
  run command owns the one bug a red run files, and the write-back only links it.
- **the run command** — `run_command(script)` returns one run report (`run_id`, `script_sha`, `platform`,
  `build_sha`, `core_available`, `results`). It is the `gui-test/1` runner of its own issue (MesenAI #1222); until it
  exists the scenario publishes and stops, and never fabricates a verdict.

Coverage is by content, not only by id: the case's text (precondition, steps, expected) is hashed, and the conversion
records that hash on the step it produced. A case whose id is there but whose text changed is **stale** and goes back
to conversion for that case only, so an edited case is never silently ignored.

Publishing (#1225) is idempotent, resumable and paced. It takes one injected **GitHub runner** — a callable
`github(op, **kwargs)` whose ops are `ensure_label`, `create_issue`, `update_issue`, `add_sub_issue`, `board` and
`item_status` — and it writes a map beside the script (`<script>.gui-test.issues.json`: the parent's number, and per case id the
issue number and the case's content hash) after every issue it creates. An interrupted publish, or a second machine,
resumes from that map and creates nothing twice; publishing again updates what the map knows, and a case the script
no longer covers is reported in `removed` and left open, because only a person closes a case. A secondary rate limit
is not a failure: the runner raises `RateLimited` and the publish waits it out (the API's own `retry_after` when it
gives one, an exponential back-off otherwise) and retries the same call. `rest_runner` builds the real runner on
`gh api` (REST, except `item_status`, which reads GraphQL: a scenario publishes hundreds of sub-issues).

`build_plan` serves both halves: with the script alone (plus `markdown=`, `script_path=`, `purpose=`) it is the #1223
plan from the script; with a parsed scenario and its script it is the #1224 plan, gated on the content hash coverage.

`gui-test/1` and the target adapter are unchanged (ADR-0272): one step per case and the step id is the case id, with
`role: setup`/`recovery` for a step that only reaches a state (a setup step gates the cases of its batch through their
`## Blocked by` edge) and `nav.goal` for a path that is not what is under test (ADR-0271).
"""
import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from squad.gui_test import validate
from squad.gui_test_report import EVIDENCE_FAMILIES, WITHHELD

CASE_ID = re.compile(r"[A-Z][A-Z0-9]*-\d+$")
CASE_LABEL = "Test-Case"
TRIAGE_LABEL = "ready-for-agent"
PARENT_STATUS = "Backlog"     # the parent's card, set before its triage label is added (#895)
INITIAL_STATUS = "To triage"
SETUP_ROLES = {"setup", "recovery"}
AUTO_MODES = {"automated"}
SETUP_STATUSES = {"needs review", "pending"}
RESULTS_FORMAT = "gui-test-results/1"
VERDICT_FAMILIES = ("passed", "failed", "needs review", "pending", "skipped")
_VERDICT_LINE = re.compile(r"\*\*(passed|failed|needs review|pending)\*\*")


FORMAT = "gui-test/1"
HASH_FIELD = "content_hash"

PARENT_LABEL = "ready-for-agent"
CASE_LABEL = "Test-Case"
SCENARIO_LABEL = "Test-Scenario"
GROUP_LABEL = "Test-Group"
OLD_TYPE_LABELS = {"testcase", "testarea", "testrun", "gui-test-run"}   # what the Test-<Kind> labels replace (#1481)
ISSUE_TYPE_LABELS = {"bug", "enhancement", "spec"}
DEFAULT_ISSUE_TYPE = "enhancement"

SUB_ISSUE_CAP = 100                      # GitHub refuses a parent with more sub-issues than this
MAP_SUFFIX = ".gui-test.issues.json"
AREA_LABEL = "Test-Area"
CASES_EMOJI = "📋"
DASHBOARD_START = "<!-- gui-test:dashboard:start -->"
DASHBOARD_END = "<!-- gui-test:dashboard:end -->"
AREA_BODY_LIMIT = 60_000                 # GitHub refuses an issue body over 65,536 characters; the rest is headroom
EXPECT_LIMIT = 160                       # the Cases table shows this much of a case's expected result
NO_OBJECTIVE = "(no objective stated)"

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


class StatusUnread(ScenarioError):
    """The board never read a card back with the Status it was written with: no triage label is added (#895)."""

    def __init__(self, number, status):
        self.number, self.status = number, status
        super().__init__([f"the Status of issue #{number} never read back as {status!r}; ready-for-agent was not added"])


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


# --------------------------------------------------------------------------- reading the two inputs


def case_ids(markdown_text):
    """The case ids of a manual scenario, in file order: the first cell of every row of a table headed `ID`."""
    out, in_table, is_case_table = [], False, False
    for line in markdown_text.splitlines():
        if not line.lstrip().startswith("|"):
            in_table = is_case_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            in_table, is_case_table = True, bool(cells) and cells[0].upper() == "ID"
            continue
        if all(set(c) <= set("-: ") for c in cells):        # the header separator
            continue
        if is_case_table and cells and CASE_ID.match(cells[0]):
            out.append(cells[0])
    return out


def steps(script):
    """Every step of the script as `(batch, kind, step)`, in the order the runner executes it."""
    out = []
    for batch in script["batches"]:
        for kind in ("setup", "steps", "teardown"):
            for s in batch.get(kind) or []:
                out.append((batch, kind, s))
    return out


def step_ids(script):
    return [s["id"] for _, _, s in steps(script)]


def gap_report(markdown_text, script):
    """Both directions of the coverage gap: a case with no step, and a step with no case.

    Nothing is published while a gap stands: a manual case silently dropped is the failure this skill exists to stop.
    """
    return _gap(markdown_text, script)


def _gap(markdown_text, script):
    ids, have = case_ids(markdown_text), step_ids(script)
    known = set(have)
    missing = [i for i in ids if i not in known]
    extra = [s for s in have if s not in set(ids)]
    return {"missing_in_script": missing, "steps_without_case": extra,
            "complete": not missing and not extra}


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


def _scenario_parent_body(scenario, script, steps, edges, areas):
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
        _areas_table(areas),
        "## Cases", "",
        "| Case | Batch | Mode | Blocked by |",
        "|---|---|---|---|",
        rows, "",
        "## Blocked by", "",
        "- None (can start immediately).", "",
    ])


def _plan_from_scenario(scenario, script, *, granularity="case"):
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
        issues = [dict(_case_issue(by_case[s["id"]], s, edges), batch=s["batch"]) for s in steps]
    else:
        issues = [dict(_batch_issue(batch["id"], steps, edges), batch=batch["id"]) for batch in script["batches"]]
    areas = _area_tree(script, issues)
    return {"scenario": scenario["name"], "granularity": granularity,
            "parent": {"title": f"Test scenario: {scenario['name']}",
                       "labels": [PARENT_LABEL, SCENARIO_LABEL],
                       "body": _scenario_parent_body(scenario, script, steps, edges, areas)},
            "areas": areas, "issues": issues}
# --------------------------------------------------------------------------- the area tree (ADR-0275)


def _area_key(batch):
    """The batch's `area` field, else its id up to the first `-` (ADR-0275 section 2)."""
    return batch.get("area") or batch["id"].split("-", 1)[0]


def _cell(text, limit=None):
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if limit is None or len(text) <= limit else text[:limit - 3] + "..."


def _action_check(step):
    return f"{_call(step.get('action'))} -> {_call(step.get('check'))}"


def _area_body(objective, batches):
    rows = [f"| `{s['id']}` | {_cell(s.get('expect'), EXPECT_LIMIT)} | {_cell(_action_check(s))} | "
            f"{s.get('severity', '-')} | {'manual' if s.get('mode') == 'manual' else 'automated'} | - |"
            for batch in batches for _batch, _kind, s in steps({"batches": [batch]})]
    return "\n".join([objective, "", "## Cases", "",
                      "| Case | Expected | Action -> check | Severity | Mode | Record |",
                      "|---|---|---|---|---|---|", *rows, ""])


def _containers(batches, by_batch):
    """The area's case issues in containers of at most SUB_ISSUE_CAP, each batch whole.

    Titles are plain ASCII like every plan title; publishing adds the container's emoji, as it adds a case's verdict.
    """
    groups, current = [], []
    for batch in batches:
        own = by_batch.get(batch["id"], [])
        if current and len(current) + len(own) > SUB_ISSUE_CAP:
            groups.append(current)
            current = []
        current = current + own
    groups.append(current)
    return [{"title": "Cases" if len(groups) == 1 else f"Cases ({n}/{len(groups)})", "cases": cases}
            for n, cases in enumerate(groups, 1)]


def _area_tree(script, issues):
    """The areas of the plan: each one's containers hold the very objects of `issues`, the flat run-ordered view.

    Raises ScenarioError — before anything is written — when a batch alone outgrows a container, a node holds more
    than SUB_ISSUE_CAP sub-issues, or an area body is over AREA_BODY_LIMIT characters.
    """
    by_batch = {}
    for issue in issues:
        by_batch.setdefault(issue["batch"], []).append(issue)
    grouped, problems = {}, []
    for batch in script["batches"]:
        area = grouped.setdefault(_area_key(batch), {"objective": None, "batches": []})
        area["objective"] = area["objective"] or batch.get("objective")
        area["batches"].append(batch)
        count = len(by_batch.get(batch["id"], []))
        if count > SUB_ISSUE_CAP:
            problems.append(f"batch {batch['id']!r} has {count} case issues and a node holds at most {SUB_ISSUE_CAP} "
                            "sub-issues: split the batch")
    if len(grouped) > SUB_ISSUE_CAP:
        problems.append(f"the scenario has {len(grouped)} areas and a node holds at most {SUB_ISSUE_CAP} sub-issues")
    areas = []
    for key, area in grouped.items():
        objective = area["objective"] or NO_OBJECTIVE
        body = _area_body(objective, area["batches"])
        if len(body) > AREA_BODY_LIMIT:
            problems.append(f"area {key!r}: the body is {len(body)} characters and the limit is {AREA_BODY_LIMIT}")
        areas.append({"key": key, "title": f"Area: {key}", "labels": [AREA_LABEL], "objective": objective,
                      "body": body, "containers": [] if problems else _containers(area["batches"], by_batch),
                      "cases": sum(len(list(steps({"batches": [b]}))) for b in area["batches"])})
    if problems:
        raise ScenarioError(problems)
    return areas


def _areas_table(areas):
    rows = [f"| `{a['key']}` | {_cell(a['objective'], EXPECT_LIMIT)} | {a['cases']} | - |" for a in areas]
    return "\n".join(["## Areas", "", "| Area | Objective | Cases | Latest result |", "|---|---|---|---|", *rows, ""])


# --------------------------------------------------------------------------- the plan


def _is_setup(kind, step):
    return kind == "setup" or step.get("role") in SETUP_ROLES


def _case_id(step):
    return step["id"]


def _call(action):
    if not action:
        return "—"
    args = ", ".join(f"{k}={json.dumps(v)}" for k, v in (action.get("args") or {}).items())
    return f"`{action['name']}({args})`"


def _variants(step):
    v = step.get("variants") or {}
    return ", ".join(f"{k}={x}" for k, x in v.items()) if v else "all variants"


def _case_body(step, kind, blocked_by, parent):
    lines = [f"Case `{step['id']}` of the test scenario " + (f"#{parent}." if parent else "this run."), "",
             f"- **Precondition:** `{step.get('precondition', '(none stated)')}`",
             f"- **Steps:** {_call(step.get('action'))}",
             f"- **Expected:** {step.get('expect', '(none stated)')}",
             f"- **Variants:** {_variants(step)}",
             f"- **Severity:** {step.get('severity', '(none stated)')}",
             f"- **Mode:** {'manual' if step.get('mode') == 'manual' else 'automated'}"
             f"{' (setup step: it only reaches a state)' if _is_setup(kind, step) else ''}"]
    if step.get("check"):
        lines.append(f"- **Check:** {_call(step['check'])}")
    lines += ["", "## Blocked by", "",
              "\n".join(f"- `{b}`" for b in blocked_by) or "None (can start immediately)"]
    return "\n".join(lines)


def _batch_body(batch, cases, blocked_by, parent):
    lines = [f"Batch `{batch['id']}` of the test scenario " + (f"#{parent}." if parent else "this run."),
             f"{len(cases)} step(s) in script order.", "",
             "| Case | Role | Expected | Mode |", "|---|---|---|---|"]
    for _kind, s in cases:
        lines.append(f"| `{s['id']}` | {s.get('role', 'under-test')} | {s.get('expect', '')} | "
                     f"{'manual' if s.get('mode') == 'manual' else 'automated'} |")
    lines += ["", "## Blocked by", "",
              "\n".join(f"- `{b}`" for b in blocked_by) or "None (can start immediately)"]
    return "\n".join(lines)


def _plan_from_script(script, markdown=None, granularity="per-case", script_path=None, purpose=None,
               title=None, parent=None):
    """The issues this scenario would publish: one parent, then one issue per case (or per batch).

    Nothing is published here, and nothing is created on GitHub: the plan is what the user approves first.
    """
    if granularity not in ("per-case", "per-batch"):
        raise ValueError(f"unknown granularity {granularity!r}")
    name = script.get("name") or "scenario"
    parent_title = title or f"Test scenario: {name}"
    parent_blocked = script.get("blocked_by") or []
    issues = (_plan_per_case if granularity == "per-case" else _plan_per_batch)(script, parent)
    for i in issues:
        i["labels"] = [CASE_LABEL]
    areas = _area_tree(script, issues)
    parent_body = _parent_body(name, script, script_path, purpose, parent_blocked, areas)
    all_steps = [s for _, _, s in steps(script)]
    return {
        "name": name,
        "granularity": granularity,
        "parent": {"title": parent_title, "body": parent_body, "labels": [TRIAGE_LABEL, SCENARIO_LABEL]},
        "areas": areas,
        "issues": issues,
        "variants": script.get("variants") or {},
        "fixtures": script.get("fixtures") or {},
        "manual": [s["id"] for s in all_steps if s.get("mode") == "manual"],
        "goals": [s["id"] for s in all_steps
                  if (s.get("action") or {}).get("name") in ("nav.goal", "nav.sweep")],
    }


def _parent_body(name, script, script_path, purpose, blocked, areas):
    lines = [f"Scenario `{name}`: every case below is run against the target adapter and its verdict is written back "
             "to the case's sub-issue.", ""]
    if purpose:
        lines += [purpose, ""]
    lines += [f"- **Script:** `{script_path or name}`",
              f"- **Target:** `{script.get('target', '(none stated)')}`", ""]
    lines += ["## Fixtures", "", "```json", json.dumps(script.get("fixtures") or {}, indent=2), "```", ""]
    lines += ["## Variants", ""]
    lines += [f"- `{axis}`: {', '.join(values)}" for axis, values in (script.get("variants") or {}).items()]
    lines += ["", _areas_table(areas), "## Cases", "", "| Batch | Case | Expected | Mode |", "|---|---|---|---|"]
    for batch in script["batches"]:
        for _, kind, s in steps({"batches": [batch]}):
            lines.append(f"| `{batch['id']}` | `{s['id']}` | {s.get('expect', '')} | "
                         f"{'manual' if s.get('mode') == 'manual' else 'automated'}"
                         f"{' (setup)' if _is_setup(kind, s) else ''} |")
    lines += ["", "## Blocked by", "",
              "\n".join(f"- #{b}" if isinstance(b, int) else f"- {b}" for b in blocked)
              or "None (can start immediately)."]
    return "\n".join(lines)


def _plan_per_case(script, parent):
    out = []
    for batch, kind, s in steps(script):
        blockers = [x["id"] for b, k, x in steps({"batches": [batch]}) if k == "setup" or x.get("role") in SETUP_ROLES]
        blockers = [b for b in blockers if b != s["id"]] if not _is_setup(kind, s) else []
        out.append({"cases": [s["id"]], "batch": batch["id"], "kind": kind,
                    "title": f"Test case {s['id']}",
                    "body": _case_body(s, kind, blockers, parent),
                    "blocked_by": blockers})
    return out


def _plan_per_batch(script, parent):
    out = []
    for batch in script["batches"]:
        cases = [(kind, s) for b, kind, s in steps({"batches": [batch]})]
        out.append({"cases": [s["id"] for _, s in cases], "batch": batch["id"], "kind": "batch",
                    "title": f"Test batch {batch['id']}",
                    "body": _batch_body(batch, cases, [], parent),
                    "blocked_by": []})
    return out




# --------------------------------------------------------------------------- the two halves meet here


_NO_SCRIPT = object()


def build_plan(first, second=_NO_SCRIPT, **kwargs):
    """The issue plan, from whichever half was asked for — the one name both halves publish.

    `build_plan(script)`, with `markdown=`, `script_path=`, `purpose=`, `granularity="per-case"|"per-batch"`, is the
    plan of the #1223 pipeline: one issue per case, or per batch. `build_plan(scenario, script)` — a parsed scenario
    and its script, with `granularity="case"|"batch"` — is the #1224 plan, refused with a
    `ScenarioError` while the coverage has a case with no step, a step with no case, or a stale case. The two
    signatures never collide: only the second half takes a second positional argument.
    """
    if second is _NO_SCRIPT:
        return _plan_from_script(first, **kwargs)
    return _plan_from_scenario(first, second, **kwargs)
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
    """The map beside the script, or an empty one: the parent's number, whether it is on the board, and per case.

    Every other key of the document is kept as it is — `layout`, `areas` and whatever a later slice adds — and so is
    every key of a case entry (`area`, `container`, ...), because publishing writes the whole document back.
    """
    blank = {"parent": None, "boarded": False, "cases": {}}
    if not path:
        return blank
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return blank
    if not isinstance(saved, dict):
        return blank
    return dict(saved, parent=saved.get("parent"), boarded=bool(saved.get("boarded")),
                cases={key: dict(value) for key, value in (saved.get("cases") or {}).items()})


def _with_dashboard(body, held):
    """`body` carrying the dashboard block `held` (the body GitHub has now) has between its markers, if it has one."""
    start = held.find(DASHBOARD_START) if isinstance(held, str) else -1
    end = held.find(DASHBOARD_END, start) if start >= 0 else -1
    if end < 0:
        return body
    block = held[start:end + len(DASHBOARD_END)]
    first = body.find(DASHBOARD_START)
    last = body.find(DASHBOARD_END, first) if first >= 0 else -1
    if last >= 0:
        return body[:first] + block + body[last + len(DASHBOARD_END):]
    return body.rstrip("\n") + "\n\n" + block + "\n"


def _case_key(issue):
    return issue["id"] if "id" in issue else issue["cases"][0]


def _dry_counts(plan, state):
    """What a publish would do to this map, counted from the map alone: nothing is asked of GitHub."""
    counts = {"create": 0, "link": 0, "move": 0, "update": 0}

    def need(entry):
        counts["update" if entry else "create"] += 1
        counts["link"] += 0 if entry else 1

    if state["parent"]:
        counts["update"] += 1
    else:
        counts["create"] += 1
    known = state.get("areas") or {}
    for area in plan.get("areas") or []:
        held = known.get(area["key"])
        need(held)
        for container in area["containers"]:
            have = next((c for c in (held or {}).get("containers", []) if c["title"] == container["title"]), None)
            need(have)
            for issue in container["cases"]:
                entry = state["cases"].get(_case_key(issue))
                need(entry)
                if entry and (entry.get("area") != area["key"] or not entry.get("container")):
                    counts["move"] += 1
    if not plan.get("areas"):
        for issue in plan["issues"]:
            need(state["cases"].get(_case_key(issue)))
    return counts


def publish(plan, github, *, script_path=None, map_path=None, saved=None, sleep=None, attempts=4, backoff=1.0,
            dry_run=False, status_reads=4, board):
    """Publish the plan as a tree: the scenario, its areas, each area's containers, each container's case records.

    Idempotent and resumable through the map beside the script, flushed after every issue created and every link
    made: a known issue is updated, never created twice, so an interrupted publish — or another machine — resumes
    from it. Links go top-down (scenario → area → container → record; a record is never linked to the scenario), and
    a node's existing sub-issues are read before it is linked to or created in, so a lost map or a concurrent writer
    never makes two containers. Re-publishing rewrites titles after their emoji prefix and bodies outside their
    dashboard block, and never touches a record's state. A case whose area changed is re-parented. A case, or an
    area, the map knows and the plan no longer covers is reported in `removed` / `removed_areas` and stays open.
    A flat map (no `"layout": "areas"`, case records under the scenario) is migrated in the same pass: areas and
    containers first, then each known record re-parented (`replace_parent`, nothing closed or deleted), then the
    records the cap left out created, and `"layout": "areas"` written last. The scenario's links to its areas are
    made in that last step too, because the flat scenario already holds the cap's 100 sub-issues: linking an area
    before the records leave the parent is refused, and a pass that dies there resumes into the same refusal. Creations are paced: a secondary rate limit (`RateLimited` from the runner) is waited out and retried.

    `dry_run` returns the counts to create, link, move and update, asks GitHub nothing and writes nothing. A plan with
    no `areas` is published flat, every case a sub-issue of the scenario.

    `board` says whether a project board is set. With one, the parent's Status is read back before its triage label;
    with none there is no card to read, so the label is added right after the create (#895).
    """
    if script_path is None and map_path is None and saved:
        path = None
    else:
        path = Path(map_path) if map_path else (issues_path(script_path) if script_path else None)
    if saved is None:
        state = read_issues(path)
    else:
        state = copy.deepcopy(dict(saved))
        state.update(parent=saved.get("parent"), boarded=bool(saved.get("boarded")), cases=state.get("cases") or {})
    tree = plan.get("areas") or []
    if not tree and len(plan["issues"]) > SUB_ISSUE_CAP:
        raise ScenarioError([f"the plan has {len(plan['issues'])} issues and GitHub allows {SUB_ISSUE_CAP} sub-issues "
                             "per parent: publish per batch, or split the scenario; nothing was created"])
    if dry_run:
        return {"dry_run": True, "counts": _dry_counts(plan, state), "map_path": None}
    wait = sleep or time.sleep
    call = _paced(github, wait=wait, attempts=attempts, backoff=backoff)

    def flush():
        if path:
            Path(path).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    fresh, listed = set(), {}

    def children(number):
        """The sub-issues a node has now; a node this publish just created has none, so nothing is asked."""
        if number not in listed:
            listed[number] = [] if number in fresh else list(call("list_sub_issues", number=number) or [])
        return listed[number]

    def adopt(parent, matches):
        return next((kid for kid in children(parent) if matches(kid.get("title") or "")), None)

    def ident_of(number, container):
        """A record's numeric id when neither the plan nor the map holds one (#1565: every map the flat publish wrote).

        The record is about to join `container`, so an interrupted pass may already have linked it there and died
        before the write that says so: the container is asked first, then the scenario the record left, and last the
        issue itself — `GET issues/<number>` answers with the id whatever its parent is now.
        """
        found = _id_in(children(container), number) or _id_in(children(scenario), number)
        if found is None:
            found = (call("issue_state", number=number) or {}).get("id")
        if found is None:
            raise ScenarioError([f"issue #{number} has no numeric id: neither the map nor GitHub carries one, so it "
                                "cannot be linked; re-run the publish once GitHub answers for the issue"])
        return found

    def link(parent, number, ident, replace=False):
        held = children(parent)
        if any(kid["number"] == number for kid in held):
            return
        extra = {"replace_parent": True} if replace else {}
        call("add_sub_issue", parent=parent, sub=ident, **extra)   # the numeric id, never the number
        held.append({"number": number, "id": ident})

    def refresh(number, title, body, labels, default):
        """Update a known issue: its title keeps the emoji prefix it has, else takes `default`."""
        state = call("issue_state", number=number) or {}
        held = state.get("title") or ""
        prefix = _held_emoji(held) or default
        kept = [x for x in state.get("labels") or [] if x not in OLD_TYPE_LABELS and x not in labels
                and not x.startswith("wave:")]   # a wave label an older publish wrote comes off, and none is written (#2132)
        labels = list(labels) + kept          # the type label replaces the old one; a label somebody added stays
        call("update_issue", number=number, title=with_emoji(title, prefix) if prefix else title, body=body,
             labels=labels)

    def create(title, body, labels):
        labels = list(labels)
        if not ISSUE_TYPE_LABELS.intersection(labels):
            labels.append(DEFAULT_ISSUE_TYPE)
        made = call("create_issue", title=title, body=body, labels=labels)
        fresh.add(made["number"])
        return made

    for label in _labels(plan):
        call("ensure_label", name=label)
    updated, created, issues = [], [], {}
    areas_done = {"created": [], "updated": []}
    containers_created = []
    # #895: a new card is Todo the moment it joins the board, and Todo plus ready-for-agent is the dispatch signal. So the
    # parent is created and boarded in Backlog without the triage label, and the label is added only after that write.
    # A known parent keeps the labels it has and is not read back: its owner may have moved the card since (#895).
    gated = [x for x in plan["parent"]["labels"] if x != TRIAGE_LABEL]
    if state["parent"]:
        body = _with_dashboard(plan["parent"]["body"], call("issue_body", number=state["parent"]))
        refresh(state["parent"], plan["parent"]["title"], body, gated, None)
        updated.append("parent")
    else:
        made = create(plan["parent"]["title"], plan["parent"]["body"], gated)
        state["parent"] = made["number"]
        flush()                     # known from here on, so an interrupt below never buys a second parent
    if not state["boarded"]:
        call("board", number=state["parent"], status=PARENT_STATUS)
        state["boarded"] = True
        state["triage_pending"] = TRIAGE_LABEL in plan["parent"]["labels"]   # kept until the label is written: a resume adds it
        flush()
    if state.get("triage_pending"):
        if board:
            # #895 follow-up: the board is eventually consistent, so the card's Status is read back, bounded, before the
            # triage label that makes it dispatchable. A Status that never reads back adds no label and is named.
            delay, read = backoff, 0
            while call("item_status", number=state["parent"]) != PARENT_STATUS:
                read += 1
                if read >= status_reads:
                    raise StatusUnread(state["parent"], PARENT_STATUS)
                wait(delay)
                delay *= 2
        refresh(state["parent"], plan["parent"]["title"], call("issue_body", number=state["parent"]) or "",
                plan["parent"]["labels"], None)
        state["triage_pending"] = False
        flush()
    scenario = state["parent"]

    def place(issue, area_key, container):
        """One case record: created or adopted or updated, then linked into its container, then written to the map."""
        key = _case_key(issue)
        entry = state["cases"].get(key)
        if entry is None:
            kid = adopt(container, lambda held: title_base(held) == issue["title"])
            entry = {"number": kid["number"], "id": kid["id"]} if kid else None
        if entry is None:
            made = create(with_emoji(issue["title"], NOT_RUN), issue["body"], issue["labels"])
            entry = {"number": made["number"], "id": made["id"]}
            created.append(key)
            state["cases"][key] = dict(entry, hash=issue.get("hash"), area=area_key)
            flush()
            link(container, entry["number"], entry["id"])
        else:
            refresh(entry["number"], issue["title"], issue["body"], issue["labels"], NOT_RUN)
            updated.append(key)
            held = state["cases"].setdefault(key, {})
            if held.get("container") != container:
                ident = entry.get("id") or held.get("id") or ident_of(entry["number"], container)
                link(container, entry["number"], ident, replace=True)    # a moved case, or one never linked
        state["cases"][key].update(entry, hash=issue.get("hash"), area=area_key, container=container)
        issues[key] = state["cases"][key]
        flush()

    if not tree:
        for issue in plan["issues"]:
            key = _case_key(issue)
            if key in state["cases"]:
                number = state["cases"][key]["number"]
                refresh(number, issue["title"], issue["body"], issue["labels"], NOT_RUN)   # keeps a verdict, back-fills ⚪
                updated.append(key)
            else:
                made = create(with_emoji(issue["title"], NOT_RUN), issue["body"], issue["labels"])
                number = made["number"]
                call("add_sub_issue", parent=scenario, sub=made["id"])
                created.append(key)
            state["cases"][key] = dict(state["cases"].get(key, {}), number=number, hash=issue.get("hash"))
            issues[key] = state["cases"][key]
            flush()
    else:
        known = state.setdefault("areas", {})
    flat = bool(tree) and state.get("layout") != "areas" and bool(state["cases"])
    waiting = []                    # a flat scenario's records are placed after every area and container exists
    untied = []                     # and its areas, linked to a scenario the flat records still fill
    for area in tree:
        entry = known.get(area["key"])
        if entry is None:
            kid = adopt(scenario, lambda held, title=area["title"]: title_base(held) == title)
            entry = {"number": kid["number"], "id": kid["id"]} if kid else None
        if entry is None:
            made = create(with_emoji(area["title"], NOT_RUN), area["body"], area["labels"])
            entry = known[area["key"]] = {"number": made["number"], "id": made["id"], "containers": [], "runs": {}}
            areas_done["created"].append(area["key"])
            flush()
        else:
            body = _with_dashboard(area["body"], call("issue_body", number=entry["number"]))
            refresh(entry["number"], area["title"], body, area["labels"], NOT_RUN)
            known[area["key"]] = entry = dict(entry, containers=entry.get("containers") or [],
                                              runs=entry.get("runs") or {})
            areas_done["updated"].append(area["key"])
        ident = entry.get("id") or ident_of(entry["number"], scenario)   # an area is linked straight to the scenario
        entry["id"] = ident
        if flat:
            untied.append((entry["number"], ident))     # a full parent has no room until the records move out
        else:
            link(scenario, entry["number"], ident)
        flush()
        for container in area["containers"]:
            held = next((c for c in entry["containers"] if c["title"] == container["title"]), None)
            if held is None:
                title = f"{CASES_EMOJI} {container['title']}"
                kid = adopt(entry["number"], lambda text, held=title: text == held)
                if kid is None:
                    made = create(title, _container_body(area["key"]), [GROUP_LABEL])
                    containers_created.append(f"{area['key']}: {title}")
                    kid = {"number": made["number"], "id": made["id"]}
                held = {"title": container["title"], "number": kid["number"], "id": kid["id"]}
                entry["containers"].append(held)
                flush()
            _retype(call, held["number"], GROUP_LABEL, fresh)
            link(entry["number"], held["number"], held["id"])
            for issue in container["cases"]:
                if flat:
                    waiting.append((issue, area["key"], held["number"]))
                else:
                    place(issue, area["key"], held["number"])
    # A flat scenario migrates in order: areas and containers, the known records re-parented, the records the cap left
    # out created, and only then the layout, so an interrupted pass is still flat and resumes the same way.
    for issue, area_key, container in sorted(waiting, key=lambda w: _case_key(w[0]) not in state["cases"]):
        place(issue, area_key, container)
    for number, ident in untied:    # the parent has room again: the flat records left it for their containers
        link(scenario, number, ident)
    if tree:
        state["layout"] = "areas"
        flush()
    return {"parent": scenario, "boarded": state["boarded"], "cases": issues,
            "issues": {key: value["number"] for key, value in issues.items()}, "created": created,
            "updated": updated, "removed": [k for k in state["cases"] if k not in issues],
            "removed_areas": [k for k in (state.get("areas") or {}) if tree and k not in {a["key"] for a in tree}],
            "areas": areas_done, "containers_created": containers_created,
            "map_path": str(path) if path else None, "map": state}


def _retype(call, number, label, fresh=()):
    """Give an issue its type label: the old ones dropped, every other label kept, nothing written when it is right."""
    if number in fresh:
        return
    held = (call("issue_state", number=number) or {}).get("labels")
    if held is None:
        return
    wanted = [x for x in held if x not in OLD_TYPE_LABELS and x != label] + [label]
    if sorted(wanted) != sorted(held):
        call("set_labels", number=number, labels=wanted)


def _id_in(kids, number):
    """The numeric id of an issue among the sub-issues listed, for an entry an older map wrote with no id.

    `None` when the list does not hold the record: a resume asks the next parent instead of dying on a `StopIteration`
    (#1565)."""
    return next((kid["id"] for kid in kids if kid["number"] == number), None)


def _container_body(area_key):
    return f"The permanent case records of the area `{area_key}`, one issue per case."


def _labels(plan):
    """Every label the plan's issues need, the case label included, whatever the granularity."""
    out = {CASE_LABEL, GROUP_LABEL, *plan["parent"]["labels"]}
    for area in plan.get("areas") or []:
        out.update(area["labels"])
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


# --------------------------------------------------------------------------- writing a run back


VERDICT_EMOJI = {"passed": "✅", "failed": "❌", "needs review": "🟡", "pending": "👤"}
NOT_RUN = "⚪"
_PREFIXES = (NOT_RUN, *VERDICT_EMOJI.values())


def title_base(title):
    """The title without a verdict prefix (an emoji of the table and one space)."""
    while title[:1] in _PREFIXES and title[1:2] == " ":
        title = title[2:]
    return title


def with_emoji(title, emoji):
    """`title` led by `emoji`, replacing any prefix it carries: latest run wins and the prefix never stacks."""
    return f"{emoji} {title_base(title)}"


def _held_emoji(title):
    """The verdict emoji a title already leads with, or None for an unprefixed one."""
    return title[0] if title[:1] in _PREFIXES and title[1:2] == " " else None


def _verdict_of(verdicts):
    """One verdict for a case (or a whole batch): the worst of its steps, in the order a reader cares about."""
    for bad in ("failed", "needs review", "pending", "skipped"):
        if any(v.startswith(bad) for v in verdicts):
            return bad if bad != "skipped" else "pending"
    return "passed"


# --------------------------------------------------------------------------- the results file (#1222/#1223)

class ResultsError(ValueError):
    """A file that is not the run's results schema; `.problems` names every reason it is not."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _step_problems(pos, step):
    if not isinstance(step, dict):
        return [f"step {pos} is not an object"]
    name = step.get("id") if isinstance(step.get("id"), str) and step["id"] else f"step {pos}"
    out = []
    if not isinstance(step.get("id"), str) or not step["id"]:
        out.append(f"step {pos} has no id")
    if not isinstance(step.get("batch"), str) or not step["batch"]:
        out.append(f"{name} has no batch")
    verdict = step.get("verdict")
    if not isinstance(verdict, str) or not verdict.startswith(VERDICT_FAMILIES):
        out.append(f"{name} has verdict {verdict!r}, not one of " + ", ".join(VERDICT_FAMILIES))
    return out


def validate_results(report):
    """Every problem with a run's results file, in the schema frozen by #1222/#1223; empty when it is well formed.

    The schema is the run command's, consumed unchanged: run id, script name and sha, platform, build sha, the start
    time and one result per step (id, batch, a verdict of `passed`/`failed`/`needs review`/`pending`, and an optional
    evidence URL). A file that does not match is rejected, never read loosely.
    """
    if not isinstance(report, dict):
        return ["the results file is not one JSON object"]
    out = []
    if report.get("format") != RESULTS_FORMAT:
        out.append(f"format is {report.get('format')!r}, not {RESULTS_FORMAT!r}")
    for field in ("run_id", "platform", "build_sha", "started"):
        if not report.get(field):
            out.append(f"{field} is missing")
    script = report.get("script")
    if not isinstance(script, dict):
        out.append("script is missing: the run names the script it ran")
    else:
        for field in ("name", "sha256"):
            if not script.get(field):
                out.append(f"script.{field} is missing")
    steps = report.get("steps")
    if not isinstance(steps, list):
        return out + ["steps is missing: a run reports one result per step"]
    for pos, s in enumerate(steps):
        out += _step_problems(pos, s)
    seen = [s["id"] for s in steps if isinstance(s, dict) and isinstance(s.get("id"), str)]
    duplicates = sorted({i for i in seen if seen.count(i) > 1})
    if duplicates:
        out.append("duplicate step id(s): " + ", ".join(duplicates))
    return out


def read_results(path):
    """Read a run's results file; raise `ResultsError` naming every reason it is not the frozen schema."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as ex:
        raise ResultsError([f"cannot read {p}: {ex}"]) from ex
    except ValueError as ex:
        raise ResultsError([f"{p} is not JSON: {ex}"]) from ex
    problems = validate_results(data)
    if problems:
        raise ResultsError(problems)
    return data


def read_map(path):
    """The map beside the script: `{"parent": <number>, "issues": {case id: issue number}}`.

    Tolerant of the shapes a publish writes (#1225): a flat `case id -> number` map, or a `cases`/`issues` object whose
    values are a number or `{"number": ..., "content_hash": ...}`. A missing map is an empty map, never an error: the
    scenario has simply not been published yet. Every other key of the document comes through unchanged, `cases`
    (the full entries) and `areas` among them.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"parent": None, "issues": {}}
    if not isinstance(raw, dict):
        return {"parent": None, "issues": {}}
    inner = raw.get("issues") if isinstance(raw.get("issues"), dict) else raw.get("cases")
    source = inner if isinstance(inner, dict) else raw
    issues = {}
    for case, value in source.items():
        if case in ("parent", "issues", "cases"):
            continue
        number = value.get("number") if isinstance(value, dict) else value
        if isinstance(number, int) and not isinstance(number, bool):
            issues[case] = number
    parent = raw.get("parent")
    kept = {key: value for key, value in raw.items()
            if key not in ("parent", "issues") and not (source is raw and key in issues)}
    return dict(kept, parent=parent if isinstance(parent, int) and not isinstance(parent, bool) else None,
                issues=issues)


def _bug_url(run):
    """The URL of the one bug the run command opened, or None; this module links it and never files one."""
    bug = run.get("bug")
    if isinstance(bug, dict):
        return bug.get("url")
    return bug if isinstance(bug, str) else None


def _case_line(case, result):
    """One case's line: its verdict, and the screenshot only where a person must look at a failure or a doubt (#1244).

    The evidence URL is embedded as an image so the screenshot is in the comment itself, not one click away. A capture
    the privacy gate withheld is said to be withheld, never linked, and a passed case carries no evidence.
    """
    verdict, evidence = result["verdict"], result.get("evidence")
    if not evidence or not verdict.startswith(EVIDENCE_FAMILIES):
        return f"- `{case}`: {verdict}"
    if evidence == WITHHELD:
        return f"- `{case}`: {verdict} — evidence withheld by the privacy gate"
    return f"- `{case}`: {verdict}\n\n![{case}]({evidence})"


def write_results(plan, run, github, published):
    """Comment every case's verdict on its sub-issue, close the passed ones, reopen a closed failure, link the bug.

    A run that cannot load what the adapter needs (the native core, the binary) fails, and a run that executed no step
    is a failure too: neither writes anything back, so a green case always means a step executed.

    A verdict is commented only when it changed since the last one on the issue (the first run comments every verdict).
    `passed` closes the case; a `failed` verdict on a closed case reopens it, so a regression never stays hidden behind
    a closed issue. Evidence is linked for `failed` and `needs review` only. **No bug is ever opened here**: the run
    command owns the one bug a red run files, and this links it on the parent.
    """
    if run.get("core_available") is False:
        return {"ok": False, "reason": "the native core is unavailable; the run did not execute (never a skip)"}
    results = run.get("results") or run.get("steps") or []
    if not results:
        said = "; ".join(run.get("reasons") or []) or "nothing to report"
        return {"ok": False, "reason": f"empty run: no step executed; the run reported: {said}"}
    by_id = {r["id"]: r for r in results}
    counts, commented, unchanged, closed, reopened = {}, [], [], [], []
    review, pending, missing, failed = [], [], [], []
    for issue in plan["issues"]:
        verdicts, lines = [], []
        for case in issue["cases"]:
            r = by_id.get(case)
            if r is None:
                missing.append(case)
                continue
            verdicts.append(r["verdict"])
            lines.append(_case_line(case, r))
        if not verdicts:
            continue
        key = issue["cases"][0]
        verdict = _verdict_of(verdicts)
        counts[verdict] = counts.get(verdict, 0) + 1
        number = published["issues"][key]
        prior = github("issue_state", number=number) or {}
        held = prior.get("title")
        if held is not None and held != with_emoji(held, VERDICT_EMOJI[verdict]):
            github("retitle", number=number, title=with_emoji(held, VERDICT_EMOJI[verdict]))
        if prior.get("verdict") == verdict:
            unchanged.append(key)
        else:
            github("comment", number=number, body=f"**{verdict}**\n\n" + "\n".join(lines))
            commented.append(key)
        if verdict == "passed":
            if prior.get("state") != "closed":
                github("close", number=number)
                closed.append(key)
        elif verdict == "failed":
            failed.extend(issue["cases"])
            if prior.get("state") == "closed":
                github("reopen", number=number)
                reopened.append(key)
        elif verdict == "needs review":
            review.append(key)
        else:
            pending.append(key)
    bug = _bug_url(run)
    github("comment", number=published["parent"], body=_summary(plan, run, counts, review, pending, missing, bug))
    counts.setdefault("passed", 0)
    return {"ok": not failed and not missing, "counts": counts, "commented": commented, "unchanged": unchanged,
            "closed": closed, "reopened": reopened, "needs_review": review, "pending": pending,
            "missing_results": missing, "bug": bug,
            **({"reason": f"{len(missing)} case(s) have no result in the run"} if missing else {})}


def _summary_line(plan, run, counts):
    """The one-line digest every run leaves on the scenario: counts per verdict and a link to the results."""
    ran = sum(counts.values())
    not_run = max(len(plan["issues"]) - ran, 0)
    link = run.get("results_url") or f"run `{run.get('run_id', '(none)')}`"
    return (f"{counts.get('passed', 0)} passed, {counts.get('failed', 0)} failed, "
            f"{counts.get('needs review', 0)} needs review, {counts.get('pending', 0)} manual, "
            f"{not_run} not run - {link}")


def _summary(plan, run, counts, review, pending, missing, bug):
    lines = [_summary_line(plan, run, counts), "",
             f"## Run summary: `{plan['name']}`", "",
             f"- Run: `{run.get('run_id', '(none)')}` | script `{run.get('script_sha', '(none)')}` | "
             f"build `{run.get('build_sha', '(none)')}` | platform `{run.get('platform', '(none)')}`", ""]
    lines += [f"- {verdict}: {n}" for verdict, n in sorted(counts.items())]
    if review:
        lines.append(f"- needs review: {', '.join('`' + c + '`' for c in review)}")
    if pending:
        lines.append(f"- pending: {', '.join('`' + c + '`' for c in pending)}")
    if missing:
        lines.append(f"- no result in the run: {', '.join('`' + c + '`' for c in missing)}")
    lines.append(f"- bug: {bug}" if bug else "- bug: none reported by the run command")
    lines += ["", "The parent is closed by a person after reading these results, never by the run."]
    return "\n".join(lines)


RUN_LABEL = "Test-Run"
RUN_BLOCK = re.compile(r"<!-- gui-test:run (\{.*\}) -->")
RUNS_EMOJI = "📅"
RUNS_CONTAINER = re.compile(rf"{RUNS_EMOJI} Runs (\d{{4}}-\d{{2}})(?: \((\d+)\))?$")
TREND_WINDOW = 10                        # a trend and a flaky check read this many runs containing the case
RUN_CAP = 100                            # a month's runs container holds this many run issues before the next one
_WORST_FIRST = ("failed", "needs review", "pending", "skipped", "passed")    # ADR-0275 §3: ❌ > 🟡 > 👤 > ⚪ > ✅
_RUN_EMOJI = dict(VERDICT_EMOJI, skipped=NOT_RUN)


def _family(verdict):
    return next((f for f in VERDICT_FAMILIES if verdict.startswith(f)), "skipped")


def _overall(families):
    return next(f for f in _WORST_FIRST if f in families) if families else "skipped"


def _utc(text):
    """A results file's `started` as an aware UTC datetime; a time with no offset is read as UTC."""
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC)


def _totals(families):
    counts = {f: sum(1 for x in families if x == f) for f in _WORST_FIRST}
    return " · ".join(f"{_RUN_EMOJI[f]} {counts[f]}" for f in ("passed", "failed", "needs review", "pending", "skipped")) \
        + f" (of {len(families)})"


def _ranked(history):
    """The run blocks of an area, oldest first: by `started`, ties by run id, never by issue number (ADR-0275 §4)."""
    return sorted(history, key=lambda run: (run["block"]["started"], run["block"]["run_id"]))


def _runs_listing(call, area_number):
    """The area's 📅 containers as they are on GitHub now, newest month first, each with the run issues it holds."""
    found = []
    for kid in call("list_sub_issues", number=area_number) or []:
        hit = RUNS_CONTAINER.match(kid.get("title") or "")
        if hit:
            found.append({"title": kid["title"], "number": kid["number"], "id": kid["id"], "month": hit.group(1),
                          "n": int(hit.group(2) or 1), "kids": list(call("list_sub_issues", number=kid["number"]) or [])})
    return sorted(found, key=lambda c: (c["month"], c["n"]), reverse=True)


def _runs_container(call, entry, listing, month):
    """The container this month's next run goes into: adopted when it exists and has room, else created and linked."""
    held = sorted((c for c in listing if c["month"] == month), key=lambda c: c["n"])
    if held and len(held[-1]["kids"]) < RUN_CAP:
        _retype(call, held[-1]["number"], GROUP_LABEL)
        return held[-1]
    n = held[-1]["n"] + 1 if held else 1
    title = f"{RUNS_EMOJI} Runs {month}" + (f" ({n})" if n > 1 else "")
    made = call("create_issue", title=title, body="", labels=[GROUP_LABEL, DEFAULT_ISSUE_TYPE])
    call("add_sub_issue", parent=entry["number"], sub=made["id"])
    container = {"title": title, "number": made["number"], "id": made["id"], "month": month, "n": n, "kids": []}
    listing.append(container)
    listing.sort(key=lambda c: (c["month"], c["n"]), reverse=True)
    return container


def _read_history(call, listing, cases):
    """The run blocks of an area, read from its containers newest month first until each case has a full window."""
    seen, runs = {case: 0 for case in cases}, []
    for container in listing:
        if cases and all(n >= TREND_WINDOW for n in seen.values()):
            break
        for kid in container["kids"]:
            hit = RUN_BLOCK.search(call("issue_body", number=kid["number"]) or "")
            if hit:
                run = {"number": kid["number"], "block": json.loads(hit.group(1))}
                runs.append(run)
                for case in run["block"]["cases"]:
                    if case in seen:
                        seen[case] += 1
    return runs


def _changes(block, earlier):
    """New failures, fixed and still failing, each case against its newest earlier run of the area that has it."""
    new, fixed, still = [], [], []
    for case, verdict in block["cases"].items():
        prior = next((r["block"]["cases"][case] for r in reversed(earlier) if case in r["block"]["cases"]), None)
        if verdict == "failed":
            (still if prior == "failed" else new).append(case)
        elif verdict == "passed" and prior == "failed":
            fixed.append(case)
    return new, fixed, still


def _names(cases):
    return ", ".join(f"`{c}`" for c in cases) or "none"


def _run_body(report, area, block, changes, numbers, evidence):
    new, fixed, still = changes
    lines = [f"- Started: {block['started']} | run `{block['run_id']}` | area `{area}`",
             f"- Script `{report['script']['sha256']}` | build `{report['build_sha']}` | platform `{report['platform']}`",
             "", "## Totals", "", _totals(list(block["cases"].values())), "", "## Changes", "",
             f"- New failures: {_names(new)}", f"- Fixed: {_names(fixed)}", f"- Still failing: {_names(still)}",
             "", "## Cases", "", "| | Case | Record | Evidence | Bug |", "|---|---|---|---|---|"]
    for case, verdict in block["cases"].items():
        shown = evidence.get(case) if verdict in ("failed", "needs review") else None
        shown = "withheld by the privacy gate" if shown == WITHHELD else (f"![{case}]({shown})" if shown else "-")
        record = f"#{numbers[case]}" if case in numbers else "-"
        lines.append(f"| {_RUN_EMOJI[verdict]} | `{case}` | {record} | {shown} | - |")
    return "\n".join(lines + ["", f"<!-- gui-test:run {json.dumps(block, sort_keys=True)} -->"])


def _trend(history, cases):
    """Per case: its last verdicts oldest to newest (runs that did not contain it are not in it) and its pass rate."""
    rows = ["### Trend (last 10 runs)", "", "| Case | Trend | Pass rate |", "|---|---|---|"]
    ranked = _ranked(history)
    for case in cases:
        seen = [r["block"]["cases"][case] for r in ranked if case in r["block"]["cases"]][-TREND_WINDOW:]
        if not seen:
            continue
        ran = [v for v in seen if v in ("passed", "failed", "needs review")]
        rate = f"{round(100 * ran.count('passed') / len(ran))}%" if ran else "-"
        rows.append(f"| `{case}` | {' '.join(_RUN_EMOJI[v] for v in seen)} | {rate} |")
    return "\n".join(rows)


def _flaky(histories, cases_of):
    """Cases that alternated ✅/❌ within their last 10 runs: at least two flips between a pass and a failure."""
    flaky = []
    for area, history in histories.items():
        ranked = _ranked(history)
        for case in cases_of(area):
            seen = [r["block"]["cases"][case] for r in ranked if case in r["block"]["cases"]][-TREND_WINDOW:]
            ran = [v for v in seen if v in ("passed", "failed")]
            if sum(1 for a, b in zip(ran, ran[1:], strict=False) if a != b) >= 2:
                flaky.append(f"- `{case}` ({area}): {' '.join(_RUN_EMOJI[v] for v in seen)}")
    return flaky


def _scenario_dashboard(histories, cases_of):
    by_run = {}
    for area, history in histories.items():
        for run in history:
            by_run.setdefault(run["block"]["run_id"], []).append((area, run))
    ordered = sorted(by_run.items(), key=lambda kv: (min(r["block"]["started"] for _, r in kv[1]), kv[0]), reverse=True)
    lines = ["### Latest run", ""]
    if ordered:
        lines += [f"Run `{ordered[0][0]}`", "", "| Area | Result | Totals |", "|---|---|---|"]
        for area, run in sorted(ordered[0][1], key=lambda x: x[0]):
            fam = list(run["block"]["cases"].values())
            lines.append(f"| {area} | {_RUN_EMOJI[_overall(fam)]} | {_totals(fam)} |")
    else:
        lines.append("No run yet.")
    lines += ["", "### Runs", "", "| Run | Started | Result | Totals | Area runs |", "|---|---|---|---|---|"]
    for run_id, parts in ordered[:TREND_WINDOW]:
        fam = [v for _, r in parts for v in r["block"]["cases"].values()]
        links = " · ".join(f"{a} #{r['number']}" for a, r in sorted(parts, key=lambda x: x[0]))
        started = min(r["block"]["started"] for _, r in parts)
        lines.append(f"| {run_id} | {started} | {_RUN_EMOJI[_overall(fam)]} | {_totals(fam)} | {links} |")
    return "\n".join(lines + ["", "### Flaky cases", ""] + (_flaky(histories, cases_of) or ["None."]))


def _set_dashboard(call, number, content):
    """Rewrite the block between the markers of an issue's body, or append one when it has none; the rest stays."""
    held = call("issue_body", number=number) or ""
    block = f"{DASHBOARD_START}\n{content}\n{DASHBOARD_END}"
    start = held.find(DASHBOARD_START)
    end = held.find(DASHBOARD_END, start) if start >= 0 else -1
    body = held[:start] + block + held[end + len(DASHBOARD_END):] if end >= 0 else held.rstrip("\n") + "\n\n" + block + "\n"
    call("set_body", number=number, body=body)


_BUG_LINE = re.compile(r"^Bug: #(\d+)[ \t]*$", re.M)
_SEVERITY_LINE = re.compile(r"\*\*Severity:\*\*\s*(\w+)")
_PRIORITY = {"critical": "P0", "major": "P1"}


def _record_bug(call, record, number, title):
    """The open bug of a case: the record's `Bug: #N` line first, then an exact-title search; and the closed one it named."""
    named = _BUG_LINE.search(record)
    held = int(named.group(1)) if named else None
    if held is not None and (call("issue_state", number=held) or {}).get("state") == "open":
        return held, None
    found = call("search_open_bug", title=title)
    return (found["number"], None) if found else (None, held)


def _with_bug_line(body, bug):
    line = f"Bug: #{bug}"
    return _BUG_LINE.sub(line, body, count=1) if _BUG_LINE.search(body) else f"{body.rstrip()}\n\n{line}\n"


def _write_record(call, report, case, step, number, run_issue):
    """Slice F of ADR-0275: one case's record follows its verdict, and a failing case owns exactly one bug.

    `passed` closes the record; `failed` and `needs review` reopen it and set the title emoji, read against the prior
    one on the title. `pending` and `skipped` never touch it, so a record a person closed stays closed.
    """
    family = _family(step["verdict"])
    if family not in ("passed", "failed", "needs review"):
        return
    prior = call("issue_state", number=number) or {}
    title, emoji = prior.get("title"), VERDICT_EMOJI[family]
    if title is not None and title != with_emoji(title, emoji):
        call("retitle", number=number, title=with_emoji(title, emoji))
    if family == "passed" and prior.get("state") != "closed":
        call("close", number=number)
    elif family != "passed" and prior.get("state") == "closed":
        call("reopen", number=number)
    bug_title = f"gui-test ❌ {report['script']['name']} · {case}"
    record = call("issue_body", number=number) or ""
    bug, closed = _record_bug(call, record, number, bug_title)
    where = f"run `{report['run_id']}`" + (f" (#{run_issue})" if run_issue else "")
    if family == "passed":
        if bug is not None:
            call("comment", number=bug, body=f"Passed in {where}: closing.")
            call("close", number=bug)
        return
    if family != "failed":
        return
    if bug is not None:
        call("comment", number=bug, body=f"Failed again in {where}.\n\n{_case_line(case, step)}")
        return
    severity = (_SEVERITY_LINE.search(record) or [None, step.get("severity")])[1]
    body = f"Case `{case}` (record #{number}) failed in {where}.\n\n{_case_line(case, step)}"
    if closed is not None:
        body += f"\n\nAn earlier bug for this case, #{closed}, was closed after a pass."
    made = call("file_bug", title=bug_title, body=body, priority=_PRIORITY.get(severity, "P2"))
    if made.get("number"):
        call("set_body", number=number, body=_with_bug_line(record, made["number"]))


def _write_areas(report, doc, map_path, github):
    """Slice E of ADR-0275: one closed run issue per touched area, then every dashboard from the run blocks."""
    call = _paced(github, wait=time.sleep, attempts=4, backoff=1.0)
    areas, cases = doc["areas"], doc.get("cases") or {}
    numbers = {case: entry["number"] for case, entry in cases.items() if isinstance(entry, dict) and "number" in entry}
    of_area = {}
    for case, entry in cases.items():
        of_area.setdefault(entry.get("area") if isinstance(entry, dict) else None, []).append(case)
    cases_of = lambda area: sorted(of_area.get(area, []))   # noqa: E731
    mine, unknown = {}, []
    for step in report["steps"]:
        area = (cases.get(step["id"]) or {}).get("area")
        if area in areas:
            mine.setdefault(area, []).append(step)
        else:
            unknown.append(step["id"])
    when = _utc(report["started"])
    stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    call("ensure_label", name=RUN_LABEL)
    call("ensure_label", name=GROUP_LABEL)
    histories, listings, written = {}, {}, []
    for area in sorted(areas):
        entry = areas[area]
        listings[area] = _runs_listing(call, entry["number"])
        histories[area] = _read_history(call, listings[area], cases_of(area))
    failed = []
    for area, steps in sorted(mine.items()):
        entry, month = areas[area], when.strftime("%Y-%m")
        container = _runs_container(call, entry, listings[area], month)
        held = entry.setdefault("runs", {}).setdefault(month, [])
        if not any(c["number"] == container["number"] for c in held):
            held.append({"title": container["title"], "number": container["number"], "id": container["id"]})
            Path(map_path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        block = {"run_id": report["run_id"], "started": stamp, "area": area, "build_sha": report["build_sha"],
                 "cases": {s["id"]: _family(s["verdict"]) for s in steps}}
        key = (block["started"], block["run_id"])
        earlier = [r for r in _ranked(histories[area]) if (r["block"]["started"], r["block"]["run_id"]) < key]
        changes = _changes(block, earlier)
        title = f"{_RUN_EMOJI[_overall(list(block['cases'].values()))]} Run {when:%Y-%m-%d %H:%M} UTC · {area} · " \
                f"{report['build_sha'][:7]}"
        evidence = {s["id"]: s.get("evidence") for s in steps}
        made = call("create_issue", title=title, body=_run_body(report, area, block, changes, numbers, evidence),
                    labels=[RUN_LABEL, DEFAULT_ISSUE_TYPE])
        call("add_sub_issue", parent=container["number"], sub=made["id"])
        call("close", number=made["number"])
        container["kids"].append({"number": made["number"], "id": made["id"]})
        histories[area].append({"number": made["number"], "block": block})
        failed += [c for c, v in block["cases"].items() if v == "failed"]
        for step in steps:
            if step["id"] in numbers:
                _write_record(call, report, step["id"], step, numbers[step["id"]], made["number"])
        written.append({"area": area, "number": made["number"], "title": title, "container": container["number"]})
    for area in mine:
        _set_dashboard(call, areas[area]["number"], _trend(histories[area], cases_of(area)))
    _set_dashboard(call, doc["parent"], _scenario_dashboard(histories, cases_of))
    return {"ok": not failed, "written": True, "runs": written, "failed": failed,
            **({"unknown_cases": unknown} if unknown else {})}


# --------------------------------------------------------------------------- the write-back command (#1226)

def write_back(results_path, map_path, github, plan=None):
    """Read a run's results file and write every verdict back to the scenario's issues. Deterministic, no model.

    The map beside the script (#1225) says which issue each case is; `plan` groups the steps that belong to one issue
    (a per-batch publish), and without it every case id of the map is one issue. This never opens a bug: the run
    command owns the one bug a red run files, and this links it on the parent.

    A map in the areas layout (ADR-0275 slice E) is written as one closed run issue per area plus the rewritten
    dashboards, under a lock beside the map; a flat map keeps the per-case comments. A malformed results file, a map
    that names no case or no parent, a run that executed no step and a core that never loaded are all failures, and
    none of them writes anything back.
    """
    try:
        report = read_results(results_path)
    except ResultsError as ex:
        return {"ok": False, "reason": "malformed results file: " + "; ".join(ex.problems),
                "problems": ex.problems}
    report.setdefault("script_sha", report["script"]["sha256"])
    lock = Path(str(map_path) + ".lock")
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return {"ok": False, "reason": f"another write-back holds the lock {lock}: nothing was written"}
    try:
        published = read_map(map_path)     # read again under the lock: another writer may have changed the map
        if not published["issues"]:
            return {"ok": False, "reason": f"the map {map_path} names no case: publish the scenario first (#1225)"}
        if published["parent"] is None:
            return {"ok": False, "reason": f"the map {map_path} names no parent issue: publish the scenario first"}
        if published.get("layout") == "areas" and published.get("areas") and not plan:
            if report.get("core_available") is False or not report["steps"]:
                return write_results({"name": "", "issues": []}, report, github, published)
            return _write_areas(report, read_issues(map_path), map_path, github)
        if plan is None:
            plan = {"name": report["script"]["name"],
                    "issues": [{"cases": [case]} for case in sorted(published["issues"])]}
        out = write_results(plan, report, github, published)
        grouped = {case for issue in plan["issues"] for case in issue["cases"]}
        unknown = [r["id"] for r in report["steps"] if r["id"] not in grouped]
        if unknown:
            out["unknown_cases"] = unknown
        return out
    finally:
        lock.unlink(missing_ok=True)


def _publish_command(args, github):
    scenario, script = read_scenario(args.markdown), read_script(args.script)
    if args.stamp:
        script = record_hashes(scenario, script)
        if not args.dry_run:
            Path(args.script).write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    runner = github if github is not None else rest_runner(args.repo, project=args.board)
    try:
        plan = build_plan(scenario, script, granularity=args.granularity)
        out = publish(plan, runner, script_path=args.script, dry_run=args.dry_run, board=bool(args.board))
    except ScenarioError as err:
        print(json.dumps({"ok": False, "published": False, "problems": err.problems}, indent=2))
        return 1
    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "counts": out["counts"]}, indent=2))
        return 0
    print(json.dumps({"ok": True, "parent": out["parent"], "created": len(out["created"]),
                      "updated": len(out["updated"]), "removed": out["removed"], "removed_areas": out["removed_areas"],
                      "map": out["map_path"]}, indent=2))
    return 0


def main(argv=None, github=None):
    """The write-back command: `python -m squad.to_test_scenario write-back <results> --map <map>`.

    It runs from CI with no model: the REST runner behind `--repo` is the only outside dependency, and injecting
    `github` is what a test does instead. Exit 0 when the verdicts were written back, 1 when the file, the run or the
    map makes that impossible.
    """
    ap = argparse.ArgumentParser(prog="python -m squad.to_test_scenario",
                                 description="Write a gui-test run's results back to the scenario's issues.")
    sub = ap.add_subparsers(dest="command", required=True)
    wb = sub.add_parser("write-back", help="write one run's results file back to the scenario's issues")
    wb.add_argument("results", help="the results file the run command wrote (#1222)")
    wb.add_argument("--map", required=True, help="the map beside the script (<script>.gui-test.issues.json)")
    wb.add_argument("--repo", help="owner/name; the REST runner is built on it when no runner is injected")
    wb.add_argument("--board", help="the board every issue joined, e.g. users/sbihaiko/projects/1")
    wb.add_argument("--bug-helper", help="the repo's scripts/report-bug.sh, for the runner and not for this command")
    sr = sub.add_parser("start-run", help="start the run command on a script and return; nothing waits for the run")
    sr.add_argument("script", help="the gui-test/1 script")
    sr.add_argument("--out-dir", help="where the results file goes (default ./gui-test-results)")
    sr.add_argument("--batch", help="run only this batch")
    for flag in ("--repo", "--board", "--bug-helper"):
        sr.add_argument(flag, help="carried into the write-back command it prints")
    pb = sub.add_parser("publish", help="publish the scenario's issues; idempotent, resumable and paced")
    pb.add_argument("markdown", help="the manual scenario")
    pb.add_argument("script", help="its gui-test/1 script; the issue map is written beside it")
    pb.add_argument("--granularity", choices=("case", "batch"), default="case")
    pb.add_argument("--stamp", action="store_true", help="record each case's content hash on its step, in the script file")
    pb.add_argument("--dry-run", action="store_true", help="print the counts to create, link, move and update; write nothing")
    for flag in ("--repo", "--board"):
        pb.add_argument(flag)
    args = ap.parse_args(argv)
    if args.command == "publish":
        return _publish_command(args, github)
    if args.command == "start-run":
        out = start_run(args.script, out_dir=args.out_dir, batch=args.batch,
                        repo=args.repo, board=args.board, bug_helper=args.bug_helper)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["started"] else 1
    runner = github if github is not None else rest_runner(args.repo, project=args.board, bug_helper=args.bug_helper)
    out = write_back(args.results, args.map, runner)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("ok") else 1



# --------------------------------------------------------------------------- starting a run, never waiting on it

RUN_COMMAND = [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "gui_test_run_script.py")]


def start_run(script_path, *, command=None, out_dir=None, adapter="mesen-gui", batch=None, case=None,
              evidence_repo=None, repo=None, board=None, bug_helper=None, env=None):
    """Start the run command on a script and return at once: a run takes minutes, and nothing here waits for it.

    The run command writes its results file when it ends; this returns the file's path, the pid, the log, and the
    **write-back argv** to run afterwards (`write-back <results> --map ...`, a separate step that reads the file). When
    the run command is not there it starts nothing and says `run command unavailable`: a run is never simulated.
    """
    command = list(command or RUN_COMMAND)
    if not Path(command[-1]).is_file():
        return {"started": False, "reason": "run command unavailable"}
    script = Path(script_path)
    out_dir = Path(out_dir) if out_dir else Path.cwd() / "gui-test-results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = script.name.split(".")[0]
    results = out_dir / f"{stem}-{time.strftime('%Y%m%dT%H%M%S')}{'-' + (batch or case) if batch or case else ''}.results.json"
    log = results.with_suffix(".log")
    args = [*command, str(script), "--adapter", adapter, "--out", str(results)]
    if evidence_repo:
        args += ["--evidence-repo", evidence_repo]
    if batch:
        args += ["--batch", batch]
    elif case:
        args += ["--case", case]
    with open(log, "w") as sink:
        proc = subprocess.Popen(args, stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, env=env)
    write_back = [sys.executable, "-m", "squad.to_test_scenario", "write-back", str(results),
                  "--map", str(issues_path(script))]
    for flag, value in (("--repo", repo), ("--board", board), ("--bug-helper", bug_helper)):
        if value:
            write_back += [flag, value]
    return {"started": True, "pid": proc.pid, "results": str(results), "log": str(log), "write_back": write_back}


# --------------------------------------------------------------------------- the whole flow


def run_scenario(markdown, script, github, run_command=None, granularity="per-case", script_path=None,
                 purpose=None, title=None, saved=None, parent=None, map_path=None, *, board):
    """Publish the scenario and, when a run command exists, run it and write every verdict back.

    Without a run command the scenario stops after publishing and says so: a result is never simulated (#1222 is the
    run command's own issue). A coverage gap stops before publishing.
    """
    gaps = _gap(markdown, script)
    if not gaps["complete"]:
        return {"gaps": gaps, "published": None, "plan": None, "run": None,
                "reason": "the Markdown and the script disagree; nothing was published"}
    plan = _plan_from_script(script, markdown, granularity, script_path, purpose, title, parent)
    if saved is None and parent:
        saved = {"parent": parent}
    published = publish(plan, github, script_path=script_path, map_path=map_path, saved=saved, board=board)
    if run_command is None:
        return {"gaps": gaps, "plan": plan, "published": published, "run": None,
                "reason": "run command unavailable"}
    run = run_command(script)
    out = write_results(plan, run, github, published)
    return {"gaps": gaps, "plan": plan, "published": published, "run": run.get("run_id"), **out}


# --------------------------------------------------------------------------- the real GitHub runner


def _sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def _project_ref(project):
    """`users/<owner>/projects/<n>` (or `orgs/...`) as the `(owner, n)` that `gh project item-add` takes."""
    parts = str(project).strip("/").split("/")
    if len(parts) == 4 and parts[2] == "projects":
        return parts[1], parts[3]
    raise ValueError(f"project must look like users/<owner>/projects/<n>, not {project!r}")


def _rate_limit(exc):
    """A `gh` failure that is a secondary rate limit: a back-off, not an error the caller must handle."""
    text = ((exc.stderr or "") + (exc.stdout or "")).lower()
    return RateLimited() if "rate limit" in text or "secondary rate" in text else None


def rest_runner(repo, shell=_sh, project=None, bug_helper=None, initial_status=INITIAL_STATUS):
    """The real GitHub runner: REST through `gh api` (number, not GraphQL), plus the repo's label and board helpers.

    `item_status` is the one op that reads GraphQL: a card's Status is on the issue's own project items, which REST
    does not list by board.

    `project` is the board to add every issue to, e.g. `users/sbihaiko/projects/1`; without it the board op is a no-op.
    `bug_helper` is the path of the repo's own bug helper (`scripts/report-bug.sh` in the checkout), the one route a
    failed run's single bug takes.
    """
    def call(args):
        try:
            return shell(args)
        except subprocess.CalledProcessError as exc:
            limited = _rate_limit(exc)
            if limited is not None:
                raise limited from exc     # a back-off for `publish`, not an error
            raise

    def api(method, path, fields=(), json_out=False):
        args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"] if method != "GET" else \
               ["gh", "api", f"repos/{repo}/{path}"]
        for k, v in fields:
            # `-F` types the value: numbers, and booleans — GitHub wants a JSON boolean, and `-f` would send the
            # string "true", which it answers with 422. `-f` stays for everything else.
            args += ["-F" if isinstance(v, (int, bool)) else "-f",
                     f"{k}={str(v).lower() if isinstance(v, bool) else v}"]
        out = call(args)
        return json.loads(out) if json_out else out

    def gh(op, **kw):
        if op == "ensure_label":
            call(["gh", "label", "create", kw["name"], "--force", "--repo", repo])
            return {}
        if op == "create_issue":
            if not ISSUE_TYPE_LABELS.intersection(kw["labels"]):
                raise ValueError("an issue needs one of: bug, enhancement, spec")
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("POST", "issues", fields, json_out=True)
        if op == "update_issue":
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("PATCH", f"issues/{kw['number']}", fields, json_out=True)
        if op == "add_sub_issue":
            fields = [("sub_issue_id", kw["sub"])] + ([("replace_parent", True)] if kw.get("replace_parent") else [])
            return api("POST", f"issues/{kw['parent']}/sub_issues", fields, json_out=True)
        if op == "remove_sub_issue":
            return api("DELETE", f"issues/{kw['parent']}/sub_issue", [("sub_issue_id", kw["sub"])], json_out=True)
        if op == "list_sub_issues":
            found, page = [], 1
            while True:
                batch = api("GET", f"issues/{kw['number']}/sub_issues?per_page=100&page={page}", json_out=True) or []
                found += batch
                if len(batch) < 100:
                    return found
                page += 1
        if op == "set_labels":
            return api("PATCH", f"issues/{kw['number']}", [("labels[]", x) for x in kw["labels"]], json_out=True)
        if op == "set_body":
            return api("PATCH", f"issues/{kw['number']}", [("body", kw["body"])], json_out=True)
        if op == "set_title":
            return api("PATCH", f"issues/{kw['number']}", [("title", kw["title"])], json_out=True)
        if op == "issue_body":
            return ((api("GET", f"issues/{kw['number']}", json_out=True) or {}).get("body")) or ""
        if op == "search_open_bug":
            query = urllib.parse.quote(f'repo:{repo} is:issue is:open label:bug in:title "{kw["title"]}"')
            found = json.loads(call(["gh", "api", f"search/issues?q={query}"])).get("items", [])
            hit = next((i for i in found if i.get("title") == kw["title"]), None)
            return None if hit is None else {"number": hit["number"], "url": hit.get("html_url"), "title": hit["title"]}
        if op == "comment":
            return api("POST", f"issues/{kw['number']}/comments", [("body", kw["body"])], json_out=True)
        if op == "retitle":
            return api("PATCH", f"issues/{kw['number']}", [("title", kw["title"])], json_out=True)
        if op == "close":
            return api("PATCH", f"issues/{kw['number']}", [("state", "closed")], json_out=True)
        if op == "reopen":
            return api("PATCH", f"issues/{kw['number']}", [("state", "open")], json_out=True)
        if op == "issue_state":
            issue = api("GET", f"issues/{kw['number']}", json_out=True)
            comments = api("GET", f"issues/{kw['number']}/comments", json_out=True)
            said = next((m.group(1) for c in reversed(comments or [])
                         if (m := _VERDICT_LINE.match((c.get("body") or "").strip()))), None)
            return {"state": (issue or {}).get("state"), "verdict": said, "title": (issue or {}).get("title"),
                    "labels": [x.get("name") for x in (issue or {}).get("labels") or []],
                    "id": (issue or {}).get("id")}     # the record's numeric id, whatever its parent is (#1565)
        if op == "board":
            if not project:
                return {}
            url = f"https://github.com/{repo}/issues/{kw['number']}"
            owner, number = _project_ref(project)
            item = json.loads(call(["gh", "project", "item-add", number, "--owner", owner, "--url", url,
                                    "--format", "json"]))
            if kw.get("status") is not None:     # a caller that names a status wants it set on the new item
                # A Status is a single-select field: the write names its field id and its option id, never `--text`.
                pid = json.loads(call(["gh", "project", "view", number, "--owner", owner, "--format", "json"]))["id"]
                fields = json.loads(call(["gh", "project", "field-list", number, "--owner", owner, "--format", "json",
                                          "--limit", "100"]))["fields"]
                field = next((f for f in fields if f.get("name") == "Status"), None)
                if field is None:
                    raise ValueError(f"no Status field on {project}")
                option = next((o for o in field.get("options") or [] if o.get("name") == kw["status"]), None)
                if option is None:
                    raise ValueError(f"no Status option {kw['status']!r} on {project}")
                call(["gh", "project", "item-edit", "--id", item["id"], "--project-id", pid,
                      "--field-id", field["id"], "--single-select-option-id", option["id"]])
            return item
        if op == "item_status":
            # The Status the issue's own card reads back now, on the named board: an issue's project items, not a list
            # of the board's items (a board over 1000 items would miss the card). None while the board does not list it.
            if not project:
                raise ValueError("item_status needs a project board: pass --board")
            owner, name = repo.split("/", 1)
            query = ("query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number)"
                     "{projectItems(first:50){nodes{project{url} fieldValueByName(name:\"Status\")"
                     "{... on ProjectV2ItemFieldSingleSelectValue{name}}}}}}}")
            # owner and name are strings (-f); only the issue number is typed (-F).
            found = json.loads(call(["gh", "api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                                     "-f", f"name={name}", "-F", f"number={kw['number']}"]))
            nodes = (((found.get("data") or {}).get("repository") or {}).get("issue") or {}).get("projectItems") or {}
            want = "/" + str(project).strip("/")
            hit = next((n for n in nodes.get("nodes") or [] if str((n.get("project") or {}).get("url") or "").endswith(want)),
                       None)
            return None if hit is None else (hit.get("fieldValueByName") or {}).get("name")
        if op == "file_bug":
            if not bug_helper:
                raise ValueError("file_bug needs bug_helper (the repo's scripts/report-bug.sh path)")
            out = call([bug_helper, kw["title"], kw["body"], kw["priority"]])
            url = next((w for w in out.split() if w.startswith("http")), None)
            tail = url.rstrip("/").rsplit("/", 1)[-1] if url else ""
            return {"url": url, "number": int(tail) if tail.isdigit() else None}
        raise ValueError(f"unknown op {op!r}")
    return gh


if __name__ == "__main__":
    sys.exit(main())
