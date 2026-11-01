"""The ticket profile comment (SPEC 1035, #1040): the close path's one writer, and the comment's render and parse.

The body is a function of the dataset rows alone. Runs sort by run id and rows by phase and attempt, so no
timestamp, pass time or input order reaches the bytes. A run's block is always its fresh rendering, never a second
block for the same run.

Closing a ticket appends the record's rows to the dataset first, then forms the whole body and writes it once through
a transport. A failed write leaves the dataset whole, and the comment comes back from the dataset alone.
"""
import json
import re

from squad import profile_dataset, profiler

MARKER = "<!-- agent-squad:profile -->"
TABLE = ("phase", "review_round", "entry_index", "attempt", "model_id", "provider", "outcome", "tokens_in",
         "tokens_out", "tokens_cache", "tokens_src", "turns")
_BLOCK = re.compile(r"<!-- agent-squad:profile-run (\S+) begin -->\n.*?```json\n(.*?)\n```\n"
                    r"<!-- agent-squad:profile-run \1 end -->", re.S)


def _order(row):
    entry = -1 if row["entry_index"] is None else row["entry_index"]
    return (row["phase"], row["review_round"], entry, row["attempt"], row["key"])


def _cell(value):
    return "-" if value is None else str(value).replace("|", "\\|")


def _block(run, rows):
    lines = [f"<!-- agent-squad:profile-run {run} begin -->", f"### Run {run}", "",
             "| " + " | ".join(TABLE) + " |", "|" + "---|" * len(TABLE)]
    lines += ["| " + " | ".join(_cell(r[c]) for c in TABLE) + " |" for r in sorted(rows, key=_order)]
    lines += ["", "```json", json.dumps(sorted(rows, key=_order), sort_keys=True, indent=2), "```",
              f"<!-- agent-squad:profile-run {run} end -->"]
    return "\n".join(lines)


def render(rows):
    """The whole comment body for a ticket's dataset rows."""
    runs = {}
    for row in rows:
        runs.setdefault(row["run_id"], []).append(row)
    return "\n\n".join([MARKER, *(_block(run, runs[run]) for run in sorted(runs))]) + "\n"


def parse(body):
    """{run id: its rows} from a comment body: the JSON block of each run, as the dataset holds them."""
    return {run: json.loads(payload) for run, payload in _BLOCK.findall(body)}


def close(root, issue, record_path, transport):
    """Append the record's rows, then project the ticket's dataset into its one comment. Return the body.

    The transport answers `comments(issue)` with [{"id", "body"}], and takes `post(issue, body)` and
    `patch(comment_id, body)`. A body that already stands is not written again.
    """
    for row in profiler.from_file(record_path):
        profile_dataset.append_row(root, row)
    body = render(profile_dataset.read(root, issue))
    found = next((c for c in transport.comments(issue) if c["body"].startswith(MARKER)), None)
    if found is None:
        transport.post(issue, body)
    elif found["body"] != body:
        transport.patch(found["id"], body)
    return body
