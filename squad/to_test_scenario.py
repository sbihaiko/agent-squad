"""`/to-test-scenario`: a manual GUI test scenario becomes a script, tracked issues and a run whose verdicts land back.

Everything here is deterministic: reading the case ids of the Markdown and the step ids of the `gui-test/1` script,
reporting the gap between them, building the issue **plan** (parent, sub-issues, labels, waves, `## Blocked by`), and
turning a run's per-step results into sub-issue comments, closes and at most one bug. It also decides the coverage of
the scenario by content hash plus the scenario's own issue plan (MesenAI #1223, MesenAI #1224). `write_back`, the
command of MesenAI #1226, reads a run's results file and writes every verdict back to the case's issue: comment,
close, reopen, and the link to the one bug the run opened. Converting prose into steps is
model judgment and lives in the skill, not here.

Two outside dependencies are injected, and they are the only ones this module has:

- **the GitHub runner** — a callable `runner(op, **kwargs)`; the ops are `ensure_label`, `create_issue`,
  `update_issue`, `add_sub_issue`, `remove_sub_issue`, `list_sub_issues`, `set_title`, `issue_body`,
  `search_open_bug`, `comment`, `close`, `reopen`, `issue_state`, `board` and `file_bug`. `rest_runner`
  builds the real one on `gh api` (REST, never GraphQL: a scenario publishes dozens of sub-issues). This module reads
  `issue_state` to know whether a verdict changed and whether a case is closed, and it **never** calls `file_bug`: the
  run command owns the one bug a red run files, and the write-back only links it.
- **the run command** — `run_command(script)` returns one run report (`run_id`, `script_sha`, `platform`,
  `build_sha`, `core_available`, `results`). It is the `gui-test/1` runner of its own issue (MesenAI #1222); until it
  exists the scenario publishes and stops, and never fabricates a verdict.

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

`build_plan` serves both halves: with the script alone (plus `markdown=`, `script_path=`, `purpose=`) it is the #1223
plan from the script; with a parsed scenario and its script it is the #1224 plan, gated on the content hash coverage.

`gui-test/1` and the target adapter are unchanged (ADR-0272): one step per case and the step id is the case id, with
`role: setup`/`recovery` for a step that only reaches a state (a setup step gates the cases of its batch, one wave
later) and `nav.goal` for a path that is not what is under test (ADR-0271).
"""
import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from squad.gui_test import validate
from squad.gui_test_report import EVIDENCE_FAMILIES, WITHHELD

CASE_ID = re.compile(r"[A-Z][A-Z0-9]*-\d+$")
CASE_LABEL = "testcase"
TRIAGE_LABEL = "ready-for-agent"
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
CASE_LABEL = "testcase"

SUB_ISSUE_CAP = 100                      # GitHub refuses a parent with more sub-issues than this
MAP_SUFFIX = ".gui-test.issues.json"
AREA_LABEL = "testarea"
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


def _plan_from_scenario(scenario, script, *, granularity="case", wave="01"):
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
                       "labels": [PARENT_LABEL, f"wave:{wave}"],
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
        i["wave"] = f"{i['wave']:02d}"
        i["labels"] = [CASE_LABEL, f"wave:{i['wave']}"]
    areas = _area_tree(script, issues)
    parent_body = _parent_body(name, script, script_path, purpose, parent_blocked, areas)
    all_steps = [s for _, _, s in steps(script)]
    return {
        "name": name,
        "granularity": granularity,
        "parent": {"title": parent_title, "body": parent_body, "labels": [TRIAGE_LABEL, "wave:01"]},
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
                    "blocked_by": blockers, "wave": 1 + (1 if blockers else 0)})
    return out


def _plan_per_batch(script, parent):
    out = []
    for batch in script["batches"]:
        cases = [(kind, s) for b, kind, s in steps({"batches": [batch]})]
        out.append({"cases": [s["id"] for _, s in cases], "batch": batch["id"], "kind": "batch",
                    "title": f"Test batch {batch['id']}",
                    "body": _batch_body(batch, cases, [], parent),
                    "blocked_by": [], "wave": 1})
    return out




# --------------------------------------------------------------------------- the two halves meet here


_NO_SCRIPT = object()


def build_plan(first, second=_NO_SCRIPT, **kwargs):
    """The issue plan, from whichever half was asked for — the one name both halves publish.

    `build_plan(script)`, with `markdown=`, `script_path=`, `purpose=`, `granularity="per-case"|"per-batch"`, is the
    plan of the #1223 pipeline: one issue per case, or per batch. `build_plan(scenario, script)` — a parsed scenario
    and its script, with `granularity="case"|"batch"` and `wave="NN"` — is the #1224 plan, refused with a
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
            dry_run=False):
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
    records the cap left out created, and `"layout": "areas"` written last. Creations are paced: a secondary rate limit (`RateLimited` from the runner) is waited out and retried.

    `dry_run` returns the counts to create, link, move and update, asks GitHub nothing and writes nothing. A plan with
    no `areas` is published flat, every case a sub-issue of the scenario.
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

    def link(parent, number, ident, replace=False):
        held = children(parent)
        if any(kid["number"] == number for kid in held):
            return
        extra = {"replace_parent": True} if replace else {}
        call("add_sub_issue", parent=parent, sub=ident, **extra)   # the numeric id, never the number
        held.append({"number": number, "id": ident})

    def refresh(number, title, body, labels, default):
        """Update a known issue: its title keeps the emoji prefix it has, else takes `default`."""
        held = (call("issue_state", number=number) or {}).get("title") or ""
        prefix = _held_emoji(held) or default
        call("update_issue", number=number, title=with_emoji(title, prefix) if prefix else title, body=body,
             labels=labels)

    def create(title, body, labels):
        made = call("create_issue", title=title, body=body, labels=labels)
        fresh.add(made["number"])
        return made

    for label in _labels(plan):
        call("ensure_label", name=label)
    updated, created, issues = [], [], {}
    areas_done = {"created": [], "updated": []}
    containers_created = []
    if state["parent"]:
        body = _with_dashboard(plan["parent"]["body"], call("issue_body", number=state["parent"]))
        refresh(state["parent"], plan["parent"]["title"], body, plan["parent"]["labels"], None)
        updated.append("parent")
    else:
        made = create(plan["parent"]["title"], plan["parent"]["body"], plan["parent"]["labels"])
        state["parent"] = made["number"]
        flush()                     # known from here on, so an interrupt below never buys a second parent
    if not state["boarded"]:
        call("board", number=state["parent"])
        state["boarded"] = True
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
                ident = entry.get("id") or held.get("id") or _id_in(children(scenario), entry["number"])
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
    for area in tree:
        entry = known.get(area["key"])
        if entry is None:
            kid = adopt(scenario, lambda held: title_base(held) == area["title"])
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
        ident = entry.get("id") or _id_in(children(scenario), entry["number"])
        entry["id"] = ident
        link(scenario, entry["number"], ident)
        flush()
        for container in area["containers"]:
            held = next((c for c in entry["containers"] if c["title"] == container["title"]), None)
            if held is None:
                title = f"{CASES_EMOJI} {container['title']}"
                kid = adopt(entry["number"], lambda text: text == title)
                if kid is None:
                    made = create(title, _container_body(area["key"]), [])
                    containers_created.append(f"{area['key']}: {title}")
                    kid = {"number": made["number"], "id": made["id"]}
                held = {"title": container["title"], "number": kid["number"], "id": kid["id"]}
                entry["containers"].append(held)
                flush()
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
    if tree:
        state["layout"] = "areas"
        flush()
    return {"parent": scenario, "boarded": state["boarded"], "cases": issues,
            "issues": {key: value["number"] for key, value in issues.items()}, "created": created,
            "updated": updated, "removed": [k for k in state["cases"] if k not in issues],
            "removed_areas": [k for k in (state.get("areas") or {}) if tree and k not in {a["key"] for a in tree}],
            "areas": areas_done, "containers_created": containers_created,
            "map_path": str(path) if path else None, "map": state}


def _id_in(kids, number):
    """The numeric id of an issue among the sub-issues listed, for an entry an older map wrote with no id."""
    return next(kid["id"] for kid in kids if kid["number"] == number)


def _container_body(area_key):
    return f"The permanent case records of the area `{area_key}`, one issue per case."


def _labels(plan):
    """Every label the plan's issues need, the case label included, whatever the granularity."""
    out = {CASE_LABEL, *plan["parent"]["labels"]}
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


# --------------------------------------------------------------------------- the write-back command (#1226)

def write_back(results_path, map_path, github, plan=None):
    """Read a run's results file and write every verdict back to the scenario's issues. Deterministic, no model.

    The map beside the script (#1225) says which issue each case is; `plan` groups the steps that belong to one issue
    (a per-batch publish), and without it every case id of the map is one issue. This never opens a bug: the run
    command owns the one bug a red run files, and this links it on the parent.

    A malformed results file, a map that names no case or no parent, a run that executed no step and a core that never
    loaded are all failures, and none of them writes anything back.
    """
    try:
        report = read_results(results_path)
    except ResultsError as ex:
        return {"ok": False, "reason": "malformed results file: " + "; ".join(ex.problems),
                "problems": ex.problems}
    report.setdefault("script_sha", report["script"]["sha256"])
    published = read_map(map_path)
    if not published["issues"]:
        return {"ok": False, "reason": f"the map {map_path} names no case: publish the scenario first (#1225)"}
    if published["parent"] is None:
        return {"ok": False, "reason": f"the map {map_path} names no parent issue: publish the scenario first"}
    if plan is None:
        plan = {"name": report["script"]["name"],
                "issues": [{"cases": [case]} for case in sorted(published["issues"])]}
    out = write_results(plan, report, github, published)
    grouped = {case for issue in plan["issues"] for case in issue["cases"]}
    unknown = [r["id"] for r in report["steps"] if r["id"] not in grouped]
    if unknown:
        out["unknown_cases"] = unknown
    return out


def _publish_command(args, github):
    scenario, script = read_scenario(args.markdown), read_script(args.script)
    if args.stamp:
        script = record_hashes(scenario, script)
        if not args.dry_run:
            Path(args.script).write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    runner = github if github is not None else rest_runner(args.repo, project=args.board)
    try:
        plan = build_plan(scenario, script, granularity=args.granularity)
        out = publish(plan, runner, script_path=args.script, dry_run=args.dry_run)
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
    sr.add_argument("--no-file-bug", action="store_true", help="do not let the run file its one bug")
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
        out = start_run(args.script, out_dir=args.out_dir, batch=args.batch, file_bug=not args.no_file_bug,
                        repo=args.repo, board=args.board, bug_helper=args.bug_helper)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if out["started"] else 1
    runner = github if github is not None else rest_runner(args.repo, project=args.board, bug_helper=args.bug_helper)
    out = write_back(args.results, args.map, runner)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("ok") else 1



# --------------------------------------------------------------------------- starting a run, never waiting on it

RUN_COMMAND = [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "gui_test_run_script.py")]


def start_run(script_path, *, command=None, out_dir=None, adapter="mesen-gui", batch=None, case=None, file_bug=True,
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
    if file_bug:
        args.append("--file-bug")
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
                 purpose=None, title=None, saved=None, parent=None, map_path=None):
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
    published = publish(plan, github, script_path=script_path, map_path=map_path, saved=saved)
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
            args += ["-F" if isinstance(v, int) and not isinstance(v, bool) else "-f", f"{k}={v}"]
        out = call(args)
        return json.loads(out) if json_out else out

    def gh(op, **kw):
        if op == "ensure_label":
            call(["gh", "label", "create", kw["name"], "--force", "--repo", repo])
            return {}
        if op == "create_issue":
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("POST", "issues", fields, json_out=True)
        if op == "update_issue":
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("PATCH", f"issues/{kw['number']}", fields, json_out=True)
        if op == "add_sub_issue":
            fields = [("sub_issue_id", kw["sub"])] + ([("replace_parent", "true")] if kw.get("replace_parent") else [])
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
            return {"state": (issue or {}).get("state"), "verdict": said, "title": (issue or {}).get("title")}
        if op == "board":
            if not project:
                return {}
            url = f"https://github.com/{repo}/issues/{kw['number']}"
            owner, number = _project_ref(project)
            item = json.loads(call(["gh", "project", "item-add", number, "--owner", owner, "--url", url,
                                    "--format", "json"]))
            if kw.get("status") is not None:     # a caller that names a status wants it set on the new item
                call(["gh", "project", "item-edit", "--id", item["id"], "--field", "Status",
                      "--text", kw["status"], "--project-id", project])
            return item
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
