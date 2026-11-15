"""The loop's step records, read back: one pure fold of the file's lines into steps with a state (#1750).

The two lanes append to `$SG_STATE/steps.tsv` through steprec.sh: a start line when a step begins, a finish line when it
ends. A record is six tab-separated fields:

    start  <id>  <name>  <epoch>  -        -
    end    <id>  <name>  <epoch>  <state>  <detail>

The state of a finish is `done` with the exit status the step carried, or `skipped` with the reason it did not run. The fold
joins each finish to the start that names its id, so a step is `running` while it holds a start and no finish, `done` or
`skipped` once it ends. A finish whose start is gone -- the size cut took it -- is no step, and a line that is no whole
record, such as the fragment a cut splits, is skipped rather than guessed at.

fold() is pure: it reads lines, never a file, so its answer is a function of what a reader hands it.
"""

STATES = ("done", "skipped", "running")
FIELDS = 6


def _record(line):
    """<line> -> the record it holds, or None when the line is no whole record."""
    fields = line.rstrip("\n").split("\t")
    if len(fields) != FIELDS:
        return None
    kind, rid, name, epoch, state, detail = fields
    if kind not in ("start", "end") or not rid or not name:
        return None
    try:
        epoch = int(epoch)
    except ValueError:
        return None
    if kind == "end" and state not in ("done", "skipped"):
        return None
    return {"kind": kind, "id": rid, "name": name, "epoch": epoch, "state": state, "detail": detail}


def fold(lines):
    """<lines> -> the steps the file holds, in the order their starts were written.

    Each step is a dict with its id, name, start epoch, end epoch (None while it runs), state (`done`, `skipped` or
    `running`), exit status (only a done step carries one) and reason (only a skipped step carries one).
    """
    steps, open_steps = [], {}
    for line in lines:
        record = _record(line)
        if record is None:
            continue
        if record["kind"] == "start":
            if record["id"] in open_steps:
                continue
            step = {"id": record["id"], "name": record["name"], "start": record["epoch"], "end": None,
                    "state": "running", "rc": None, "reason": None}
            open_steps[record["id"]] = step
            steps.append(step)
            continue
        step = open_steps.pop(record["id"], None)
        if step is None:
            continue   # its start went in a cut: a finish alone names no step
        step["end"] = record["epoch"]
        if record["state"] == "done":
            step["state"] = "done"
            try:
                step["rc"] = int(record["detail"])
            except ValueError:
                step["rc"] = None
        else:
            step["state"] = "skipped"
            step["reason"] = record["detail"] or None
    return steps
