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
  `update_issue`, `add_sub_issue`, `comment`, `close`, `reopen`, `issue_state`, `board` and `file_bug`. `rest_runner`
  builds the real one on `gh api` (REST, never GraphQL: a scenario publishes dozens of sub-issues). This module reads
  `issue_state` to know whether a verdict changed and whether a case is closed, and it **never** calls `file_bug`: the
  run command owns the one bug a red run files, and the write-back only links it.
- **the run command** — `run_command(script)` returns one run report (`run_id`, `script_sha`, `platform`,
  `build_sha`, `core_available`, `results`). It is the `gui-test/1` runner of its own issue (MesenAI #1222); until it
  exists the scenario publishes and stops, and never fabricates a verdict.

Coverage is by content, not only by id: the case's text (precondition, steps, expected) is hashed, and the conversion
records that hash on the step it produced. A case whose id is there but whose text changed is **stale** and goes back
to conversion for that case only, so an edited case is never silently ignored.

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
from pathlib import Path

from squad.gui_test import validate

CASE_ID = re.compile(r"[A-Z][A-Z0-9]*-\d+$")
CASE_LABEL = "test-case"
TRIAGE_LABEL = "ready-for-agent"
INITIAL_STATUS = "To triage"
SETUP_ROLES = {"setup", "recovery"}
AUTO_MODES = {"automated"}
SETUP_STATUSES = {"needs review", "pending"}
RESULTS_FORMAT = "gui-test-results/1"
VERDICT_FAMILIES = ("passed", "failed", "needs review", "pending", "skipped")
EVIDENCE_FAMILIES = ("failed", "needs review")
_VERDICT_LINE = re.compile(r"\*\*(passed|failed|needs review|pending)\*\*")


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


def _scenario_parent_body(scenario, script, steps, edges):
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
        issues = [_case_issue(by_case[s["id"]], s, edges) for s in steps]
    else:
        issues = [_batch_issue(batch["id"], steps, edges) for batch in script["batches"]]
    return {"scenario": scenario["name"], "granularity": granularity,
            "parent": {"title": f"Test scenario: {scenario['name']}",
                       "labels": [PARENT_LABEL, f"wave:{wave}"],
                       "body": _scenario_parent_body(scenario, script, steps, edges)},
            "issues": issues}
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
    parent_body = _parent_body(name, script, script_path, purpose, parent_blocked)
    issues = (_plan_per_case if granularity == "per-case" else _plan_per_batch)(script, parent)
    for i in issues:
        i["wave"] = f"{i['wave']:02d}"
        i["labels"] = [CASE_LABEL, f"wave:{i['wave']}"]
    all_steps = [s for _, _, s in steps(script)]
    return {
        "name": name,
        "granularity": granularity,
        "parent": {"title": parent_title, "body": parent_body, "labels": [TRIAGE_LABEL, "wave:01"]},
        "issues": issues,
        "variants": script.get("variants") or {},
        "fixtures": script.get("fixtures") or {},
        "manual": [s["id"] for s in all_steps if s.get("mode") == "manual"],
        "goals": [s["id"] for s in all_steps
                  if (s.get("action") or {}).get("name") in ("nav.goal", "nav.sweep")],
    }


def _parent_body(name, script, script_path, purpose, blocked):
    lines = [f"Scenario `{name}`: every case below is run against the target adapter and its verdict is written back "
             "to the case's sub-issue.", ""]
    if purpose:
        lines += [purpose, ""]
    lines += [f"- **Script:** `{script_path or name}`",
              f"- **Target:** `{script.get('target', '(none stated)')}`", ""]
    lines += ["## Fixtures", "", "```json", json.dumps(script.get("fixtures") or {}, indent=2), "```", ""]
    lines += ["## Variants", ""]
    lines += [f"- `{axis}`: {', '.join(values)}" for axis, values in (script.get("variants") or {}).items()]
    lines += ["", "## Cases", "", "| Batch | Case | Expected | Mode |", "|---|---|---|---|"]
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
# --------------------------------------------------------------------------- publishing, idempotently


def load_map(path):
    """The case id -> issue number map saved beside the script, or {} when there is none yet."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def save_map(path, mapping):
    """Write the map beside the script: a second invocation finds the same issues instead of creating new ones."""
    Path(path).write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n")


def publish(plan, github, saved=None, parent=None):
    """Create or update the parent and its sub-issues; return the map and what happened.

    Idempotent by case id through `saved` (the map of a previous publish): a case the map knows is updated, never
    duplicated. A case the map knows and the plan does not is reported in `removed` and left open: only a person
    closes a case.
    """
    saved = dict(saved or {})
    issues, created, updated = {}, [], []
    for label in _labels(plan):
        github("ensure_label", name=label)
    if parent:
        github("update_issue", number=parent, title=plan["parent"]["title"],
               body=plan["parent"]["body"], labels=plan["parent"]["labels"])
        parent_number, _ = parent, updated.append("parent")
    else:
        parent_number = _create(github, plan["parent"])
        github("board", number=parent_number, status=INITIAL_STATUS)
    for issue in plan["issues"]:
        key = issue["cases"][0]
        if key in saved:
            github("update_issue", number=saved[key], title=issue["title"], body=issue["body"],
                   labels=issue["labels"])
            issues[key] = saved[key]
            updated.append(key)
            continue
        made = github("create_issue", title=issue["title"], body=issue["body"], labels=issue["labels"])
        issues[key] = made["number"]
        created.append(key)
        github("add_sub_issue", parent=parent_number, sub=made["id"])
        github("board", number=made["number"], status=INITIAL_STATUS)
    return {"parent": parent_number, "issues": issues, "created": created, "updated": updated,
            "removed": [k for k in saved if k not in issues and k != "parent"]}


def _create(github, item):
    made = github("create_issue", title=item["title"], body=item["body"], labels=item["labels"])
    return made["number"]


def _labels(plan):
    waves = {i["wave"] for i in plan["issues"]}
    return sorted({TRIAGE_LABEL, CASE_LABEL, *plan["parent"]["labels"], *(f"wave:{w}" for w in waves)})


# --------------------------------------------------------------------------- writing a run back


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
    scenario has simply not been published yet.
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
    return {"parent": parent if isinstance(parent, int) and not isinstance(parent, bool) else None, "issues": issues}


def _bug_url(run):
    """The URL of the one bug the run command opened, or None; this module links it and never files one."""
    bug = run.get("bug")
    if isinstance(bug, dict):
        return bug.get("url")
    return bug if isinstance(bug, str) else None


def _case_line(case, result):
    """One case's line: its verdict, and the evidence only where a person must look at a failure or a doubt."""
    verdict = result["verdict"]
    evidence = result.get("evidence")
    if evidence and verdict.startswith(EVIDENCE_FAMILIES):
        return f"- `{case}`: {verdict} (evidence: {evidence})"
    return f"- `{case}`: {verdict}"


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


def _summary(plan, run, counts, review, pending, missing, bug):
    lines = [f"## Run summary: `{plan['name']}`", "",
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
    args = ap.parse_args(argv)
    runner = github if github is not None else rest_runner(args.repo, project=args.board, bug_helper=args.bug_helper)
    out = write_back(args.results, args.map, runner)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0 if out.get("ok") else 1



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
    published = publish(plan, github, saved=saved, parent=parent)
    if map_path:
        save_map(map_path, {**published["issues"], "parent": published["parent"]})
    if run_command is None:
        return {"gaps": gaps, "plan": plan, "published": published, "run": None,
                "reason": "run command unavailable"}
    run = run_command(script)
    out = write_results(plan, run, github, published)
    return {"gaps": gaps, "plan": plan, "published": published, "run": run.get("run_id"), **out}


# --------------------------------------------------------------------------- the real GitHub runner


def _sh(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def rest_runner(repo, shell=_sh, project=None, bug_helper=None, initial_status=INITIAL_STATUS):
    """The real GitHub runner: REST through `gh api` (number, not GraphQL), plus the repo's label and board helpers.

    `project` is the board to add every issue to, e.g. `users/sbihaiko/projects/1`; without it the board op is a no-op.
    `bug_helper` is the path of the repo's own bug helper (`scripts/report-bug.sh` in the checkout), the one route a
    failed run's single bug takes.
    """
    def api(method, path, fields=(), json_out=False):
        args = ["gh", "api", "--method", method, f"repos/{repo}/{path}"] if method != "GET" else \
               ["gh", "api", f"repos/{repo}/{path}"]
        for k, v in fields:
            args += ["-f", f"{k}={v}"]
        out = shell(args)
        return json.loads(out) if json_out else out

    def gh(op, **kw):
        if op == "ensure_label":
            shell(["gh", "label", "create", kw["name"], "--force", "--repo", repo])
            return {}
        if op == "create_issue":
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("POST", "issues", fields, json_out=True)
        if op == "update_issue":
            fields = [("title", kw["title"]), ("body", kw["body"])] + [("labels[]", x) for x in kw["labels"]]
            return api("PATCH", f"issues/{kw['number']}", fields, json_out=True)
        if op == "add_sub_issue":
            return api("POST", f"issues/{kw['parent']}/sub_issues", [("sub_issue_id", kw["sub"])], json_out=True)
        if op == "comment":
            return api("POST", f"issues/{kw['number']}/comments", [("body", kw["body"])], json_out=True)
        if op == "close":
            return api("PATCH", f"issues/{kw['number']}", [("state", "closed")], json_out=True)
        if op == "reopen":
            return api("PATCH", f"issues/{kw['number']}", [("state", "open")], json_out=True)
        if op == "issue_state":
            issue = api("GET", f"issues/{kw['number']}", json_out=True)
            comments = api("GET", f"issues/{kw['number']}/comments", json_out=True)
            said = next((m.group(1) for c in reversed(comments or [])
                         if (m := _VERDICT_LINE.match((c.get("body") or "").strip()))), None)
            return {"state": (issue or {}).get("state"), "verdict": said}
        if op == "board":
            if not project:
                return {}
            url = f"https://github.com/{repo}/issues/{kw['number']}"
            item = json.loads(shell(["gh", "project", "item-add", project, "--url", url, "--format", "json"]))
            shell(["gh", "project", "item-edit", "--id", item["id"], "--field", "Status",
                   "--text", kw.get("status", initial_status), "--project-id", project])
            return item
        if op == "file_bug":
            if not bug_helper:
                raise ValueError("file_bug needs bug_helper (the repo's scripts/report-bug.sh path)")
            out = shell([bug_helper, kw["title"], kw["body"], kw["priority"]])
            return {"url": next((w for w in out.split() if w.startswith("http")), None)}
        raise ValueError(f"unknown op {op!r}")
    return gh


if __name__ == "__main__":
    sys.exit(main())
