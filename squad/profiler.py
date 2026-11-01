"""The ticket profiler's row contract (SPEC 1035, #1036): one row per phase per model attempt.

The reader takes a run record that already exists and answers rows. It writes nothing, starts nothing, and no
engine path imports it: the record stays the only run truth (ADR-0019), and the profile is a view of it.

A phase is the name of the work a node did (`PHASE_OF`, one of plan, code, review, fix, gate, land). An attempt
is one event the record holds about the node entry: a chain entry left out (`skipped`), a chain entry a provider
refused (`refused`), the call that answered (`accepted` or `failed`), a node that ran no model (`none`), or a
landing. The attempt number is that row's place among the attempts of its (phase, review_round, entry), in record
order, counting from 1: the chain's own order, because the record writes the skips, the refusals and the answer
in that order.

A phase's winning model is the model of its accepted row highest on `entry_index`: a later accepted entry
supersedes an earlier one, and a refused or skipped row never wins. A phase with no accepted row has no winner.

The idempotency key is `run_id + phase + review_round + entry_index + attempt`. The same record read twice
answers the same rows and the same keys, so a resumed or reopened ticket cannot add one row twice (SPEC 1035).

A fact the record does not hold is `unknown`, never a guess (ADR-0155): an old record with no `chain_id` on its
call answers `unknown`, and its chain is never rebuilt from the current configuration.
"""
from squad import record

SCHEMA = 1                          # the row's own schema version: an older line stays readable
UNKNOWN = "unknown"                 # the answer for a fact the record does not hold
NO_MODEL = "none"                   # a node that ran no model: a gate, a loop, an action, a landing
OUTCOMES = ("accepted", "refused", "skipped", "failed")
PHASES = ("plan", "code", "review", "fix", "gate", "land")

# The phase map: the graph's own node names to the loop's phase. A node the map does not place answers `unknown`
# rather than a guessed phase; a project adds its own names through `rows(..., phase_of=...)`.
PHASE_OF = {
    "scout": "plan", "spec": "plan", "spec_draft": "plan", "spec_proxy": "plan", "decompose": "plan",
    "plan": "plan", "plan_proxy": "plan", "router": "plan", "route": "plan",
    "actor": "code", "work": "code", "code": "code",
    "critic": "review", "review": "review",
    "fix": "fix",
    "gate": "gate", "budget_gate": "gate", "plan_gate": "gate", "spec_gate": "gate", "scope_gate": "gate",
    "security_gate": "gate", "review_gate": "gate", "final_gate": "gate", "learning_gate": "gate",
    "ac_verify": "gate", "visual_guard": "gate",
    "land": "land", "merge": "land", "manual_merge": "land", "ship": "land", "commit": "land",
}

COLUMNS = ("schema", "issue", "run_id", "phase", "review_round", "chain_id", "entry_index", "model_id",
           "provider", "attempt", "outcome", "tokens_in", "tokens_out", "tokens_cache", "turns", "key")


class RowError(Exception):
    """A row the contract refuses: it lacks a column, names an outcome it does not define, or carries a key that
    is not its own. The message names the field."""


def key(row):
    """The idempotency key: `run_id + phase + review_round + entry_index + attempt`."""
    return f"{row['run_id']}:{row['phase']}:{row['review_round']}:{row['entry_index']}:{row['attempt']}"


def validate(row):
    """Refuse a row that breaks the contract, naming the field. Return the row when it holds."""
    for f in COLUMNS:
        if f not in row:
            raise RowError(f"profile row lacks {f}")
    if row["outcome"] not in OUTCOMES:
        raise RowError(f"outcome {row['outcome']!r} is not one of {list(OUTCOMES)}")
    if row["key"] != key(row):
        raise RowError(f"key {row['key']!r} is not the key of its own columns ({key(row)!r})")
    return row


def phase_of(node, phase_map=None):
    """The phase a node belongs to: the phase map's answer, else `unknown`."""
    return (phase_map if phase_map is not None else PHASE_OF).get(node, UNKNOWN)


def _start(events):
    return next((e for e in events if e["event"] == "run_start"), {})


def _chains(events):
    """{(node, entry): chain id} from the entry's first call that names one. An entry whose call names none keeps
    `unknown`: the chain is what the call wrote down, never what the configuration holds now."""
    out = {}
    for e in events:
        if e["event"] == "call_start" and e.get("chain_id"):
            out.setdefault((e.get("node"), e.get("entry")), e["chain_id"])
    return out


def _cache(e):
    """The row's cache tokens: the parts the call reported, summed; None when it reported none (never a zero)."""
    read, write = e.get("cache_read"), e.get("cache_write")
    if read is None and write is None:
        return None
    return (read or 0) + (write or 0)


def _attempt(e):
    """(model, outcome, event) for one attempt the record holds, else None."""
    kind = e["event"]
    if kind == "model_skipped":
        return e.get("model"), "skipped", e
    if kind == "model_refused":
        return e.get("model"), "refused", e
    if kind == "outcome":
        return (e.get("model") or NO_MODEL), ("accepted" if e.get("outcome") == "pass" else "failed"), e
    if kind == "land":
        return NO_MODEL, ("accepted" if e.get("outcome") == "pass" else "failed"), e
    return None


def rows(events, phase_of_map=None):
    """One row per phase per model attempt, in record order. `phase_of_map` adds or overrides the phase map."""
    start = _start(events)
    run_id = start.get("run_id") or UNKNOWN
    issue = start.get("issue") or UNKNOWN
    chains = _chains(events)
    rounds, attempts, out = {}, {}, []
    for e in events:
        if e["event"] == "review_round":
            rounds[e["node"]] = max(rounds.get(e["node"], 0), e.get("round") or 0)
            continue
        got = _attempt(e)
        if got is None:
            continue
        model, outcome, ev = got
        node, entry = ev.get("node"), ev.get("entry")
        group = (node, entry, rounds.get(node, 0))
        attempts[group] = attempts.get(group, 0) + 1
        row = {"schema": SCHEMA, "issue": issue, "run_id": run_id, "phase": phase_of(node, phase_of_map),
               "review_round": rounds.get(node, 0), "chain_id": chains.get((node, entry)) or UNKNOWN,
               "entry_index": entry, "model_id": model or UNKNOWN, "provider": ev.get("provider") or UNKNOWN,
               "attempt": attempts[group], "outcome": outcome, "tokens_in": ev.get("tokens_in"),
               "tokens_out": ev.get("tokens_out"), "tokens_cache": _cache(ev), "turns": ev.get("turns")}
        row["key"] = key(row)
        out.append(validate(row))
    return out


def winner(rows, phase):
    """The row that counts as a phase's winning model: its accepted row highest on entry index, else None."""
    win = None
    for r in rows:
        if r["phase"] != phase or r["outcome"] != "accepted":
            continue
        if win is None or (r["entry_index"], r["attempt"]) > (win["entry_index"], win["attempt"]):
            win = r
    return win


def from_file(path, phase_of_map=None):
    """Read a run record on disk and answer its rows."""
    return rows(record.read(path), phase_of_map)
