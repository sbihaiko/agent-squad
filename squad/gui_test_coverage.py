"""No case of a manual scenario may be lost when it becomes a `gui-test/1` script (MesenAI #1242).

`to_test_scenario.gap_report` pairs a scenario with a script one case to one step, which is the shape `/to-test-scenario`
publishes per case. The pilot script is not that shape: its ids are finer than the scenario's cases (`HOME-02` is four
`home.focus-holds-*` steps plus the press and the back), so the pairing is by reference instead - a step refers to the
case it walks, in its `id`, its `precondition` or its `expect`, and every case of the scenario must be referred to by
at least one step.

Both directions are reported, because both are failures the same way: a case no step walks is the silent drop this
whole check exists to stop, and a step naming a case the scenario does not define is a typo that hides one. The
scenario's own `case_ids` also misses a case whose cell carries a tag (`P4-04 **(pass 3)**` never matches
`[A-Z][A-Z0-9]*-\\d+$`), so the id is read as the leading token of the cell instead.
"""
import re

CASE_REF = re.compile(r"\b[A-Z][A-Z0-9]*-\d\d\b")


def scenario_case_ids(markdown_text):
    """The case ids of a manual scenario, in file order: the leading id token of the first cell of every row of a table headed `ID`."""
    out, in_table, is_case_table = [], False, False
    for line in markdown_text.splitlines():
        if not line.lstrip().startswith("|"):
            in_table = is_case_table = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not in_table:
            in_table, is_case_table = True, bool(cells) and cells[0].upper() == "ID"
            continue
        if all(set(c) <= set("-: ") for c in cells):  # the header separator
            continue
        if is_case_table and cells:
            token = cells[0].split()[0] if cells[0].split() else ""
            if re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", token) and token not in out:
                out.append(token)
    return out


def referenced_case_ids(script):
    """Every case id the script's steps name, as `{id: [step ids]}` - a step refers to a case in its id, precondition or expect."""
    out = {}
    for batch in script.get("batches", []):
        for part in ("setup", "steps", "teardown"):
            for step in batch.get(part) or []:
                text = " ".join(str(step.get(k, "")) for k in ("id", "precondition", "expect"))
                for case in CASE_REF.findall(text):
                    out.setdefault(case, []).append(step["id"])
    return out


def coverage(markdown_text, script):
    """`{"missing": [...], "unknown": [...]}` - a case no step walks, and a step naming a case the scenario does not define."""
    cases, seen = scenario_case_ids(markdown_text), referenced_case_ids(script)
    return {"missing": [c for c in cases if c not in seen],
            "unknown": sorted(c for c in seen if c not in set(cases))}
